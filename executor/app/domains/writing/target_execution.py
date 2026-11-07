"""Task-local execution facts; ordinary conversations keep their existing defaults."""
from __future__ import annotations

import hashlib
import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts.target_tasks import TargetTaskRequest
from app.platform.agent.runtime.types import AgentMiddleware
from langchain_core.messages import ToolMessage


_execution: ContextVar[FrozenExecution | None] = ContextVar("writing_target_execution", default=None)


def current_execution():
    return _execution.get()


def _content(value):
    if isinstance(value, str):
        return value
    update = getattr(value, "update", None)
    if isinstance(update, dict):
        return "\n".join(_content(message) for message in update.get("messages", []))
    if isinstance(value, dict):
        if isinstance(value.get("update"), dict):
            return _content(value["update"])
        return _content(value.get("text", value.get("content", value.get("messages", ""))))
    if isinstance(value, list):
        return "\n".join(_content(item) for item in value)
    return _content(getattr(value, "content", ""))


def _failed(value):
    if isinstance(value, str):
        return value.startswith(("[save_artifact 失败", "[save_artifact 拒绝", "[交审失败]", "Error:"))
    if getattr(value, "status", None) == "error":
        return True
    update = getattr(value, "update", None)
    if isinstance(update, dict):
        return any(_failed(message) for message in update.get("messages", []))
    if isinstance(value, dict):
        if isinstance(value.get("update"), dict):
            return _failed(value["update"])
        if isinstance(value.get("messages"), list):
            return any(_failed(message) for message in value["messages"])
        return value.get("status") == "error" or _failed(value.get("content", value.get("text", "")))
    return False


def manuscript_body(value: str) -> str:
    return "\n".join(line for line in value.splitlines()
                     if not line.startswith(("# ", "> 状态", "> 进度", "> Skill", "> 场景"))).strip()


class TargetScopeMiddleware(AgentMiddleware):
    """Single-chapter evaluation must not inherit ordinary multi-card batches."""

    def __init__(self, execution):
        self.execution = execution

    def _block(self, request):
        target = self.execution.request.target
        if target.task_type != "chapter_outlines" or target.chapter is None:
            return None
        call = request.tool_call
        name, args = call.get("name"), call.get("args") or {}
        reason = None
        if name == "save_artifact":
            kind = args.get("kind")
            if kind in {"chapter_outline", "chapter_text"} and (kind != "chapter_outline" or args.get("chapter") != target.chapter):
                reason = f"本次只能保存第{target.chapter}章章纲，不生成其他章纲或正文。"
            elif kind == "volume_outline" and args.get("volume") not in target.volume_ids:
                reason = "本次只能回写目标章所属卷的任务副本。"
        elif name == "rewrite_rhythm_table":
            from contracts.writing_artifacts import parse_volume_chapter_range
            volume = args.get("volume")
            original = self.execution.request.input.files.get(f"卷纲/第{target.volume_ids[0]:02d}卷.md", "")
            span = parse_volume_chapter_range(str(args.get("table") or ""))
            if volume not in target.volume_ids or span is None or span != parse_volume_chapter_range(original):
                reason = "单章任务只允许在所属卷原章号范围内回写，不增减章节。"
        elif name in {"write_file", "edit_file"}:
            reason = "单章任务必须通过语义产物工具保存指定章纲。"
        if reason:
            return ToolMessage(content="[目标范围] " + reason, tool_call_id=call.get("id") or "",
                               name=name, status="error", response_metadata={"business_intercept": True})
        return None

    def wrap_tool_call(self, request, handler):
        blocked = self._block(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        blocked = self._block(request)
        return blocked if blocked is not None else await handler(request)


@dataclass
class FrozenExecution:
    request: TargetTaskRequest
    package: Any
    package_root: Path
    model: Any
    workspace: Path
    stage: str = ""
    volume: int | None = None
    chapter: int | None = None
    evidence: dict = field(default_factory=dict)
    persist: Any = None
    isolated: bool = False
    _calls: dict = field(default_factory=dict)

    def __enter__(self):
        self._token = _execution.set(self)
        return self

    def __exit__(self, *args):
        _execution.reset(self._token)

    @property
    def skills(self):
        return self.request.input.skills

    def identity(self):
        return {
            "task_id": self.request.task_id,
            "input_revision": self.request.input.input_revision,
            "source_commit": self.request.source_commit,
            "artifact_digest": self.request.artifact_digest,
            "model_snapshot": self.request.model_snapshot.model_dump(mode="json")
            if self.request.model_snapshot else None,
            "target": self.request.target.model_dump(mode="json"),
            "skills_digest": hashlib.sha256(json.dumps(self.skills, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        }

    def text_path(self):
        chapter = self.request.target.chapter
        return self.workspace / "正文" / f"第{chapter:03d}章.md" if chapter else None

    def read_text(self):
        path = self.text_path()
        return path.read_text(encoding="utf-8") if path and path.is_file() else ""

    def observe_event(self, event):
        """Observe real tool events outside the evolvable package."""
        kind, name = event.get("event"), event.get("name")
        data = event.get("data") or {}
        run_id = str(event.get("run_id") or "")
        if kind == "on_tool_start" and name == "task":
            args = data.get("input") or {}
            if not isinstance(args, dict):
                return
            target = args.get("subagent_type") or args.get("name")
            if target not in {"review", "volume_qc"}:
                return
            if target == "review":
                requested = {int(n) for n in re.findall(r"第\s*(\d+)\s*章", str(args.get("description", "")))}
                if requested != {self.request.target.chapter}:
                    return
                self.evidence["initial_draft"] = self.read_text()
            self._calls[run_id] = (target, self.volume)
        elif kind in {"on_tool_end", "on_tool_error"} and run_id in self._calls:
            target, volume = self._calls.pop(run_id)
            output = data.get("output")
            text = _content(output)
            success = kind == "on_tool_end" and not _failed(output) and bool(text.strip())
            if target == "review":
                self.evidence.update({
                    "review": "success" if success else "failed",
                    "review_output": text,
                    "needs_revision": "通过，无修改意见" not in text.replace(" ", ""),
                    "revised": False,
                })
            else:
                self.evidence.setdefault("volume_qc", {})[str(volume)] = {
                    "success": success, "output": text,
                }
        elif kind == "on_tool_end" and name == "save_artifact" and not _failed(data.get("output")):
            if self.evidence.get("review") == "success":
                before = manuscript_body(self.evidence.get("initial_draft", ""))
                after = manuscript_body(self.read_text())
                if before and after and before != after:
                    self.evidence["revised"] = True

    def observe(self, sink):
        execution = self

        class ObservedSink:
            async def on_event(self, event):
                execution.observe_event(event)
                if execution.persist is not None and event.get("event") in {"on_tool_start", "on_tool_end", "on_tool_error"}:
                    execution.persist(execution.evidence)
                return await sink.on_event(event)

        return ObservedSink()
