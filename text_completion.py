"""正文交付凭证：只消费原生工具元数据，不把模型声明当执行事实。"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

STATE_KEY = "text_completion"
FAILURE_NOTE = "审稿环节失败已跳过"


def workspace_id(root: Path) -> str:
    return hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()


def current_turn(state: dict) -> str | None:
    turn = next((m.id for m in reversed(state.get("messages", [])) if isinstance(m, HumanMessage)), None)
    return turn or (state.get(STATE_KEY) or {}).get("turn")


def completion_snapshot(state: dict, root: Path) -> dict:
    workspace = workspace_id(root)
    saved = state.get(STATE_KEY)
    if isinstance(saved, dict) and saved.get("workspace") == workspace:
        snapshot = deepcopy(saved)
    else:
        initial = [int(p.stem[1:-1]) for p in (Path(root) / "正文").glob("第*章.md")
                   if re.fullmatch(r"第\d+章", p.stem) and p.read_text(encoding="utf-8").strip()]
        snapshot = {"workspace": workspace, "chapters": {}, "seen": [], "initial": initial}
    snapshot["turn"] = current_turn(state)
    seen = set(snapshot["seen"])
    message_turn = None
    for message in state.get("messages", []):
        if isinstance(message, HumanMessage):
            message_turn = message.id
        if not isinstance(message, ToolMessage):
            continue
        fact = message.response_metadata.get("writer_text")
        if not isinstance(fact, dict) and isinstance(message.artifact, dict):
            fact = message.artifact.get("writer_text")
        if not isinstance(fact, dict) or fact.get("workspace") != workspace:
            continue
        chapter = fact.get("chapter")
        if type(chapter) is not int or chapter < 1 or not message.tool_call_id:
            continue
        event = fact.get("event")
        identity = f"{event}:{message.tool_call_id}"
        if identity in seen or event not in {"save", "review"}:
            continue
        seen.add(identity)
        snapshot["seen"].append(identity)
        key = str(chapter)
        if event == "save":
            if message.status == "error":
                continue
            if fact.get("is_writing_unit") is True:
                snapshot["active_turn"] = message_turn or snapshot["turn"]
                snapshot["chapters"][key] = {
                    "unit": message.tool_call_id, "status": fact.get("status", ""),
                    "review": None, "review_turn": None, "order": len(snapshot["seen"]),
                }
            elif key in snapshot["chapters"]:
                snapshot["active_turn"] = message_turn or snapshot["turn"]
                snapshot["chapters"][key]["status"] = fact.get("status", "")
        elif key in snapshot["chapters"]:
            entry = snapshot["chapters"][key]
            if fact.get("unit") == entry["unit"] and fact.get("outcome") in {"success", "failed"}:
                snapshot["active_turn"] = fact.get("turn") or message_turn or snapshot["turn"]
                entry["review"] = fact["outcome"]
                entry["review_turn"] = fact.get("turn")
    return snapshot


def review_target(state: dict, root: Path, description: str | None = None) -> tuple[int, dict] | None:
    chapters = completion_snapshot(state, root)["chapters"]
    if description is not None:
        requested = {int(n) for n in re.findall(r"第\s*(\d+)\s*章", description)}
        if len(requested) != 1:
            return None
        chapter = requested.pop()
        entry = chapters.get(str(chapter))
        return (chapter, entry) if entry is not None else None
    pending = [(int(k), v) for k, v in chapters.items() if v.get("review") is None]
    candidates = pending or [(int(k), v) for k, v in chapters.items()]
    return max(candidates, key=lambda item: item[1]["order"]) if candidates else None


def parallel_writing_unit(state: dict, root: Path) -> bool:
    snapshot = completion_snapshot(state, root)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return False
    known = set(snapshot["initial"]) | {int(k) for k in snapshot["chapters"]}
    for call in messages[-1].tool_calls:
        args = call.get("args", {})
        if call.get("name") == "save_artifact" and args.get("kind") == "chapter_text":
            if args.get("mode", "full") == "append":
                return True
            if args.get("mode", "full") == "full" and args.get("chapter") not in known:
                return True
    return False


def review_block_reason(state: dict, root: Path, description: str = "") -> str | None:
    if parallel_writing_unit(state, root):
        return "本批正文保存尚未返回；请先核对真实保存结果，再委托审稿。"
    snapshot = completion_snapshot(state, root)
    if not snapshot["chapters"]:
        return None
    if (all(v.get("review") is not None for v in snapshot["chapters"].values())
            and snapshot.get("active_turn") != current_turn(state)):
        return None
    if not isinstance(description, str):
        return "请用文字明确只审第N章。"
    target = review_target(state, root, description)
    if target is None:
        requested = {int(n) for n in re.findall(r"第\s*(\d+)\s*章", description)}
        if (len(requested) == 1 and requested.issubset(snapshot["initial"])
                and requested.isdisjoint({int(k) for k in snapshot["chapters"]})):
            return None
        return "请明确只审一个已起草章节，委托说明使用“审第N章”。"
    chapter, entry = target
    if entry["status"] not in {"初稿", "人修定稿", "定稿"}:
        return f"第{chapter}章仍未转为初稿，请先完成整章正文和状态保存。"
    if entry.get("review") is not None and entry.get("review_turn") == current_turn(state):
        return f"第{chapter}章本轮已实际尝试过审稿，请按已有结果修订或注明失败降级，不再审稿。"
    return None


def delivery_block_reason(state: dict, root: Path) -> str | None:
    if parallel_writing_unit(state, root):
        return "正文保存尚未返回，不能同时交审。"
    for chapter, entry in completion_snapshot(state, root)["chapters"].items():
        if entry["status"] not in {"初稿", "人修定稿", "定稿"}:
            return f"第{chapter}章仍未转为初稿。"
        if entry.get("review") is None:
            return f"第{chapter}章最新起草单元之后尚未实际委托review审稿。"
    return None


def failed_review_note(state: dict, root: Path) -> str:
    failed = [k for k, v in completion_snapshot(state, root)["chapters"].items() if v.get("review") == "failed"]
    return f"{FAILURE_NOTE}（第{'、'.join(failed)}章）；本轮不重试，按自检交付。" if failed else ""


def mark_review_result(result: Any, *, state: dict, root: Path, call_id: str,
                       description: str = "", failed: bool = False) -> Any:
    if not isinstance(description, str) or (not isinstance(result, Command) and not failed):
        return result
    target = review_target(state, root, description)
    if target is None:
        return result
    chapter, entry = target

    def marked(message):
        if not isinstance(message, ToolMessage) or message.tool_call_id != call_id:
            return message
        outcome = "success" if not failed and message.status != "error" else "failed"
        metadata = dict(message.response_metadata)
        metadata["writer_text"] = {
            "workspace": workspace_id(root), "chapter": chapter, "event": "review",
            "unit": entry["unit"], "outcome": outcome, "turn": current_turn(state),
        }
        content = message.content
        if outcome == "failed":
            note = f"{FAILURE_NOTE}；本轮不重试，按自检交付。"
            content = f"{content}\n\n{note}" if isinstance(content, str) else [*content, {"type": "text", "text": note}]
        return message.model_copy(update={"response_metadata": metadata, "content": content})

    if isinstance(result, ToolMessage):
        return marked(result)
    if isinstance(result, Command) and isinstance(result.update, dict):
        update = dict(result.update)
        update["messages"] = [marked(m) for m in update.get("messages", [])]
        return replace(result, update=update)
    return result


def enabled_for(abc) -> bool:
    from contracts.writing_artifacts import STAGE_TEXT, classify_writing_workspace, resolve_confirmed_stage

    return (
        getattr(abc.agent, "name", None) == "author"
        and not getattr(getattr(abc, "ctx", None), "character_repair", False)
        and classify_writing_workspace(abc.workspace_path) == "five"
        and resolve_confirmed_stage(abc.workspace_path) == STAGE_TEXT
    )
