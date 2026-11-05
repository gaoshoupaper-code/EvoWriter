"""Durable target jobs over the normal five-stage writing service."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import logging
import multiprocessing as mp
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from contracts.target_tasks import TargetSpec, TargetTaskRequest, TargetTaskResult, TaskInput
from contracts.trace.files import open_anchored_text
from contracts.writing_artifacts import (
    is_final_status, load_writing_state, parse_relpath, parse_volume_chapter_range,
    save_writing_state, scan_writing_progress,
)

logger = logging.getLogger("writer.target_tasks")
TERMINAL = {"completed", "failed", "stopped"}


def _now():
    return datetime.now(UTC).isoformat()


def _business_path(name):
    return name == "demand.md" or parse_relpath(name) is not None


def _safe_file(root: Path, name: str):
    if root.resolve() != root.absolute():
        raise ValueError("工作目录不能经过符号链接")
    path = root / name
    path.resolve().relative_to(root.resolve())
    if path.is_symlink():
        raise ValueError(f"产物不能是符号链接：{name}")
    return path


def materialize_input(workspace: Path, data: TaskInput):
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in data.files.items():
        if not _business_path(name):
            raise ValueError(f"不是受支持的业务产物路径：{name}")
        path = _safe_file(workspace, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if data.demand_md:
        _safe_file(workspace, "demand.md").write_text(data.demand_md, encoding="utf-8")


def collect_artifacts(workspace: Path):
    files = {}
    if workspace.exists():
        for path in sorted(workspace.rglob("*.md")):
            relative = path.relative_to(workspace).as_posix()
            if any(part.startswith(".") for part in Path(relative).parts) or not _business_path(relative):
                continue
            files[relative] = _safe_file(workspace, relative).read_text(encoding="utf-8")
    return files


def validate_input(target: TargetSpec, data: TaskInput):
    missing = []
    if not data.confirmation_rules.strip():
        missing.append("预设确认规则")
    for name in data.files:
        if not _business_path(name):
            raise ValueError(f"不是受支持的业务产物路径：{name}")
    if target.task_type == "volume_outlines":
        if not data.demand_md.strip():
            missing.append("完整需求 demand.md")
    else:
        required = ["主题.md", "总纲.md", "文风.md"]
        if target.task_type == "chapter_outlines":
            required += [f"卷纲/第{n:02d}卷.md" for n in target.volume_ids]
        else:
            required.append(f"章纲/第{target.chapter:03d}章.md")
            if not any(name.startswith("卷纲/") for name in data.files):
                missing.append("目标章节所属卷纲")
            if target.chapter > 1:
                required.append(f"正文/第{target.chapter - 1:03d}章.md")
        missing += [name for name in required if not data.files.get(name, "").strip()]
        if any(name.startswith("人物/") for name in data.files) and not data.files.get("人物/主角.md", "").strip():
            missing.append("人物/主角.md")
    if missing:
        raise ValueError("缺少必需资料：" + "、".join(missing))
    if target.task_type == "chapter_outlines":
        for volume in target.volume_ids:
            name = f"卷纲/第{volume:02d}卷.md"
            if parse_volume_chapter_range(data.files[name]) is None:
                raise ValueError(f"{name} 缺少合法且唯一的本卷章号范围")
    if target.task_type == "chapter_text":
        existing = data.files.get(f"正文/第{target.chapter:03d}章.md", "")
        if re.search(r">\s*状态[：:][^\n]*人修定稿", existing):
            raise ValueError("目标正文已人修定稿，请先显式删除重做后再启动任务")
        ranges = [parse_volume_chapter_range(text) for name, text in data.files.items() if name.startswith("卷纲/")]
        if not any(span and span[0] <= target.chapter <= span[1] for span in ranges):
            raise ValueError("目标章节不在提供的卷纲章号范围内")


def _read(workspace, name):
    path = _safe_file(workspace, name)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


_CHAPTER_FIELDS = ("章节概括", "矛盾驱动", "主角状态", "供给点", "章末钩子", "开头方案", "修法承接", "伏笔操作")


def _chapter_ready(text):
    return is_final_status(text) and all(field in text for field in _CHAPTER_FIELDS)


def completion_checks(target: TargetSpec, workspace: Path, *, text_evidence=None):
    missing, ranges = [], {}
    if target.task_type == "volume_outlines":
        for volume in target.volume_ids:
            name = f"卷纲/第{volume:02d}卷.md"
            text = _read(workspace, name)
            if not is_final_status(text) or parse_volume_chapter_range(text) is None:
                missing.append(name)
    elif target.task_type == "chapter_outlines":
        occupied = set()
        for volume in target.volume_ids:
            name = f"卷纲/第{volume:02d}卷.md"
            span = parse_volume_chapter_range(_read(workspace, name))
            if span is None:
                missing.append(name + "：章号范围无效")
                continue
            ranges[str(volume)] = list(span)
            chapters = set(range(span[0], span[1] + 1))
            if occupied & chapters:
                missing.append(name + "：目标卷章号范围重叠")
            occupied.update(chapters)
            for chapter in sorted(chapters):
                chapter_name = f"章纲/第{chapter:03d}章.md"
                if not _chapter_ready(_read(workspace, chapter_name)):
                    missing.append(chapter_name)
    else:
        name = f"正文/第{target.chapter:03d}章.md"
        text = _read(workspace, name)
        from app.domains.writing.target_execution import manuscript_body
        match = re.search(r"^>\s*状态[：:]\s*([^｜|\n]+)", text, re.MULTILINE)
        if not manuscript_body(text) or not match or match[1].strip() not in {"初稿", "人修定稿", "定稿"}:
            missing.append(name + "：正文尚未完整保存为初稿")
        evidence = text_evidence or {}
        if evidence.get("review") != "success":
            missing.append("实际审稿未成功")
        if evidence.get("needs_revision") is not False and not evidence.get("revised"):
            missing.append("缺少所需审后修订证据")
    return {"complete": not missing, "missing": missing, "volume_ranges": ranges}


@contextmanager
def _file_lock(path: Path, *, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            try:
                yield stream.fileno()
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield stream.fileno()
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


class TargetTaskStore:
    """Atomic per-task journal shared by the API and isolated worker."""
    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, task_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", task_id):
            raise ValueError("非法任务编号")
        return self.root / task_id

    def package_directory(self, task_id):
        self.directory(task_id)
        return self.root.parent / f"{self.root.name}_packages" / task_id

    def trusted_path(self, task_id):
        self.directory(task_id)
        return self.root.parent / f"{self.root.name}_trusted" / f"{task_id}.json"

    def trusted(self, task_id):
        path = self.trusted_path(task_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def approve(self, task_id, record):
        path = self.trusted_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with staged.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        staged.replace(path)

    def trusted_all(self):
        directory = self.trusted_path("probe").parent
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))] if directory.exists() else []

    def record(self, task_id):
        try:
            with open_anchored_text(self.directory(task_id), "record.json") as stream:
                return json.load(stream)
        except FileNotFoundError:
            return None

    def _write(self, task_id, record):
        directory = self.directory(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"record-{uuid.uuid4().hex}.tmp"
        with path.open("w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        path.replace(directory / "record.json")

    def create(self, request: TargetTaskRequest, *, metadata=None):
        with _file_lock(self.directory(request.task_id) / "state.lock"):
            old = self.trusted(request.task_id) or self.record(request.task_id)
            request_data = request.model_dump(mode="json")
            identity = {k: v for k, v in request_data.items() if k not in {"attempt_id", "traceparent"}}
            if old is not None:
                previous = {k: v for k, v in old["request"].items() if k not in {"attempt_id", "traceparent"}}
                if identity != previous:
                    raise ValueError("同一任务编号不能用于不同输入或版本（任务冲突）")
                return self.get(request.task_id), False
            now = _now()
            result = TargetTaskResult(
                task_id=request.task_id, status="pending", input_revision=request.input.input_revision,
                source_commit=request.source_commit, artifact_digest=request.artifact_digest,
                usage={"input_tokens": None, "output_tokens": None, "total_tokens": None},
                checks={"identity": {
                    "target": request.target.model_dump(mode="json"),
                    "input_revision": request.input.input_revision,
                    "source_commit": request.source_commit,
                    "model_snapshot": request.model_snapshot.model_dump(mode="json") if request.model_snapshot else None,
                }},
                created_at=now, updated_at=now,
            )
            record = {"request": request_data, "result": result.model_dump(mode="json"),
                      "metadata": metadata or {}, "history": []}
            self._write(request.task_id, record)
            self.approve(request.task_id, {**deepcopy(record), "verified": False})
            return result, True

    def get(self, task_id):
        row = self.record(task_id)
        return TargetTaskResult.model_validate(row["result"]) if row else None

    def update(self, task_id, **changes):
        with _file_lock(self.directory(task_id) / "state.lock"):
            row = self.record(task_id)
            if row is None:
                raise ValueError("任务不存在")
            result = TargetTaskResult.model_validate({**row["result"], **changes, "updated_at": _now()})
            row["result"] = result.model_dump(mode="json")
            self._write(task_id, row)
            return result

    def journal(self, task_id, *, metadata=None, event=None):
        with _file_lock(self.directory(task_id) / "state.lock"):
            row = self.record(task_id)
            if metadata:
                row["metadata"].update(metadata)
            if event:
                row["history"].append({"at": _now(), **event})
            self._write(task_id, row)

    def all(self):
        return [self.get(path.name) for path in sorted(self.root.iterdir()) if path.is_dir() and (path / "record.json").is_file()]

    def unfinished(self):
        return [row for row in self.all() if row.status not in TERMINAL]

    def worker_alive(self, task_id):
        try:
            with _file_lock(self.directory(task_id) / "worker.lock", blocking=False):
                return False
        except OSError:
            return True


def bind_frozen_run(request, trace_id, settings):
    import httpx
    from contracts.platform import BindingAck, BindingCreate
    body = BindingCreate(trace_id=trace_id, harness_commit=request.source_commit,
                         llm_config=request.model_snapshot, run_purpose="optimization", degraded=False)
    try:
        response = httpx.post(f"{settings.platform_url.rstrip('/')}/api/bindings", json=body.model_dump(mode="json"), timeout=10)
        response.raise_for_status()
        return BindingAck.model_validate(response.json()).binding
    except Exception:
        logger.warning("固定任务绑定暂不可达，任务身份仍写入本地trace", exc_info=True)
        return None


def build_frozen_model(request, settings):
    from app.domains.writing.models import build_writer_model
    snapshot = request.model_snapshot
    reference = snapshot.api_key_ref if snapshot else None
    if reference and reference.startswith("experiment:"):
        import httpx
        match = re.fullmatch(r"experiment:([A-Za-z0-9_-]+):model", reference)
        if not match:
            raise ValueError("实验模型凭据引用无效")
        response = httpx.get(
            f"{settings.evolution_url.rstrip('/')}/api/ingestion/experiment-key/{match[1]}",
            headers={"X-Notify-Token": settings.evolution_notify_token}, timeout=30,
        )
        response.raise_for_status()
        config = response.json()
        if config["model"] != snapshot.model or config["base_url"].rstrip("/") != snapshot.base_url.rstrip("/"):
            raise ValueError("实验模型配置与冻结快照不一致")
        return build_writer_model(settings, api_key=config["api_key"], base_url=config["base_url"], model_name_override=config["model"])
    if snapshot:
        from app.platform.llm_config.loader import get_active_llm_config
        config = get_active_llm_config()
        if config and config.base_url.rstrip("/") != snapshot.base_url.rstrip("/"):
            raise ValueError("模型凭据端点已变化，拒绝向未经核实的快照端点发送凭据")
    return build_writer_model(settings, llm_override=snapshot)


def _next_step(target, workspace):
    if target.task_type == "volume_outlines":
        progress = scan_writing_progress(workspace)
        if not progress.theme_done:
            return "theme", None, None
        if not progress.outline_done:
            return "outline", None, None
        if progress.outline_volumes != len(target.volume_ids):
            raise ValueError("总纲全书卷数与声明目标范围不一致")
        for volume in target.volume_ids:
            text = _read(workspace, f"卷纲/第{volume:02d}卷.md")
            if not is_final_status(text) or parse_volume_chapter_range(text) is None:
                return "volume", volume, None
    elif target.task_type == "chapter_outlines":
        for volume in target.volume_ids:
            span = parse_volume_chapter_range(_read(workspace, f"卷纲/第{volume:02d}卷.md"))
            if span is None:
                raise ValueError(f"第{volume}卷章号范围无效")
            if any(not _chapter_ready(_read(workspace, f"章纲/第{chapter:03d}章.md")) for chapter in range(span[0], span[1] + 1)):
                return "chapter", volume, None
    else:
        return "text", None, target.chapter
    return None


def _fingerprint(workspace):
    raw = json.dumps(collect_artifacts(workspace), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _frame(frame):
    event, data = None, None
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return event, data


def _usage(recorder, trace_ids):
    from contracts.trace.usage import TraceMetricsAccumulator
    metrics = TraceMetricsAccumulator()
    for trace_id in trace_ids:
        for event in recorder.read_trace_events(trace_id) or []:
            metrics.add_event(event)
    return metrics.snapshot(terminal=True).model_dump()


async def drive_task(request, workspace, service, thread, execution, store):
    from app.schemas.screenplay import ScreenplayGenerateRequest
    from app.domains.writing.pipeline import on_run_complete
    task_id = request.task_id
    row = store.record(task_id)
    execution.evidence.update(row["metadata"].get("evidence", {}))
    trace_ids = list(store.get(task_id).trace_ids)
    step_number = row["metadata"].get("step_number", 0)
    seen_boundaries = set()
    while True:
        if (store.directory(task_id) / "stop").exists():
            raise asyncio.CancelledError
        checks = completion_checks(request.target, workspace, text_evidence=execution.evidence)
        missing_qc = []
        if request.target.task_type == "volume_outlines":
            for volume in request.target.volume_ids:
                name = f"卷纲/第{volume:02d}卷.md"
                actual = _read(workspace, name)
                original = request.input.files.get(name, "")
                reused_final = actual == original and is_final_status(original)
                if (is_final_status(actual) and not reused_final
                        and not execution.evidence.get("volume_qc", {}).get(str(volume), {}).get("success")):
                    missing_qc.append(volume)
            if missing_qc:
                checks["complete"] = False
                checks["missing"].extend(f"第{volume}卷缺少实际质检凭证" for volume in missing_qc)
        step = _next_step(request.target, workspace)
        if missing_qc:
            step = ("volume", missing_qc[0], None)
        if checks["complete"] and (step is None or request.target.task_type == "chapter_text"):
            return {**checks, "text_evidence": execution.evidence, "identity": execution.identity()}
        if step is None:
            raise ValueError("目标产物未通过完整性检查：" + "、".join(checks["missing"]))
        execution.stage, execution.volume, execution.chapter = step
        state = load_writing_state(workspace)
        state.confirmed_stage = execution.stage
        state.stage = execution.stage
        save_writing_state(workspace, state)
        phase = f"{execution.stage}:{execution.volume or execution.chapter or ''}"
        metadata = store.record(task_id)["metadata"]
        resume_trace = metadata.get("resume_trace") if metadata.get("phase") == phase else None
        if metadata.get("phase") != phase:
            step_number += 1
        stalled_rounds = metadata.get("stalled_rounds", 0) if metadata.get("phase") == phase else 0
        current_thread = thread.model_copy(update={"thread_id": f"{thread.thread_id}-{step_number}"})
        before = _fingerprint(workspace)
        before_evidence = json.dumps(execution.evidence, sort_keys=True, ensure_ascii=False)
        prompt = (
            f"全自动目标任务：{request.target.model_dump_json()}。当前目标阶段：{phase}。\n"
            f"完整需求：\n{request.input.demand_md}\n"
            f"预设确认规则：\n{request.input.confirmation_rules}\n"
            f"本次尚缺的检查：{checks['missing']}\n"
            "本任务不含人工精修，前文章节使用已提供的实际稿件。\n"
            "按同一会话中已起草的内容继续推进当前目标，不等待人工发出下一轮指令。"
            "必须使用 save_artifact 保存实际产物；普通回复、片段和进度标记不算交付。"
            "正文必须调用实际 review 审稿并完成所需修订；卷纲必须取得实际 volume_qc 质检。"
            "如果上一轮只回复了内容，先将该内容继续完善并保存，再补齐本次尚缺的检查。"
        )
        payload = ScreenplayGenerateRequest(thread_id=current_thread.thread_id, prompt=prompt,
                                            message_id=f"{task_id}-{step_number}-{uuid.uuid4().hex}")
        if resume_trace:
            payload = payload.model_copy(update={"trace_id": resume_trace, "resume": request.input.confirmation_rules})
        store.update(task_id, status="running", phase=phase)
        store.journal(task_id, metadata={"phase": phase, "step_number": step_number, "thread": current_thread.model_dump(mode="json")})
        interrupted, final = None, False
        with execution:
            async for frame in service.generate_stream(
                payload, current_thread, owner_id=current_thread.user_id,
                run_purpose="user_generation" if row["metadata"].get("owner_id") else "optimization",
                traceparent=request.traceparent,
            ):
                kind, data = _frame(frame)
                if kind == "status" and isinstance(data, dict):
                    trace_id = data.get("trace_id")
                    if trace_id and trace_id not in trace_ids:
                        trace_ids.append(trace_id)
                        store.update(task_id, trace_ids=trace_ids)
                    if data.get("status") in {"resume_rejected", "log_failed", "cancelled"}:
                        raise ValueError(str(data.get("reason") or data.get("error") or data["status"]))
                elif kind == "interrupt":
                    interrupted = data
                elif kind == "final":
                    final = True
                if (store.directory(task_id) / "stop").exists():
                    raise asyncio.CancelledError
        store.journal(task_id, metadata={"evidence": execution.evidence})
        store.update(task_id, artifacts=collect_artifacts(workspace), usage=_usage(service.trace_recorder, trace_ids))
        latest = trace_ids[-1] if trace_ids else None
        if interrupted:
            boundary = json.dumps([phase, before, interrupted], sort_keys=True, ensure_ascii=False)
            if boundary in seen_boundaries:
                raise ValueError("任务在相同确认点循环，目标未取得进展")
            seen_boundaries.add(boundary)
            if interrupted.get("kind") == "review" and interrupted.get("review_kind") in {"stage", "batch"}:
                after = _fingerprint(workspace)
                if after == before and not completion_checks(request.target, workspace, text_evidence=execution.evidence)["complete"]:
                    raise ValueError("交审时目标产物未取得进展")
                if latest:
                    service.trace_recorder.append_event(latest, {"type": "run_meta", "status": "completed", "source": "system", "input": {
                        "target_confirmation": {"rules": request.input.confirmation_rules, "review": interrupted}}})
                    service.trace_recorder.complete_run(current_thread, latest)
                on_run_complete(workspace, "author")
                store.journal(task_id, metadata={"resume_trace": None})
            else:
                store.journal(task_id, metadata={"resume_trace": latest})
        else:
            store.journal(task_id, metadata={"resume_trace": None})
            if not final:
                raise ValueError("生成流未完成且未提供可续跑确认点")
        after_evidence = json.dumps(execution.evidence, sort_keys=True, ensure_ascii=False)
        if _fingerprint(workspace) != before or after_evidence != before_evidence:
            stalled_rounds = 0
        elif final:
            stalled_rounds += 1
        store.journal(task_id, metadata={"stalled_rounds": stalled_rounds})
        if stalled_rounds >= 3:
            raise ValueError("连续3轮生成回复没有保存产物或取得实际审修进展，目标未完成")


async def _run_worker(root: Path, task_id: str, stopped, parent_pid, *, prepared_model=None, isolated=False):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from app.domains.writing.agent import MetaAgentService
    from app.domains.writing.target_execution import FrozenExecution
    from app.platform.agent.loader import load_package
    from app.platform.core.settings import get_settings
    from app.platform.trace import TraceRecorder
    from app.schemas.screenplay import ThreadSummary
    store = TargetTaskStore(root)
    record = store.record(task_id)
    request = TargetTaskRequest.model_validate(record["request"])
    metadata = record["metadata"]
    workspace = Path(metadata["workspace"])
    recorder = TraceRecorder()
    settings = get_settings()
    directory = store.directory(task_id)
    active = asyncio.current_task()

    async def watch_stop():
        while True:
            if stopped.is_set() or (directory / "stop").exists() or (parent_pid is not None and os.getppid() != parent_pid):
                active.cancel()
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch_stop())
    try:
        validate_input(request.target, request.input)
        if not metadata.get("initialized"):
            if not metadata.get("owner_id"):
                materialize_input(workspace, request.input)
            elif request.input.demand_md and not (workspace / "demand.md").exists():
                (workspace / "demand.md").write_text(request.input.demand_md, encoding="utf-8")
            store.journal(task_id, metadata={"initialized": True})
        from app.platform.memory.store import (
            MemoryStorePool, get_memory_store_pool, init_memory_store_pool, resolve_memory_root,
        )
        try:
            get_memory_store_pool()
        except RuntimeError:
            init_memory_store_pool(MemoryStorePool(resolve_memory_root()))
        for trace_id in store.get(task_id).trace_ids:
            recorder.register_external_run(trace_id, str(workspace))
        package_root = store.package_directory(task_id)
        model = prepared_model or build_frozen_model(request, settings)
        package = load_package(package_root, f"target_harness_{hashlib.sha256(task_id.encode()).hexdigest()[:16]}")
        execution = FrozenExecution(request, package, package_root, model, workspace)
        execution.isolated = isolated
        execution.persist = lambda evidence: store.journal(task_id, metadata={"evidence": evidence})
        thread = ThreadSummary.model_validate(metadata.get("base_thread", metadata["thread"]))
        async with AsyncSqliteSaver.from_conn_string(str(directory / "checkpoints.db")) as saver:
            await saver.setup()
            service = MetaAgentService(settings, workspace, recorder, None, saver)
            async def task_saver(owner_id):
                return saver
            service._resolve_checkpointer = task_saver
            store.update(task_id, status="running", attempts=store.get(task_id).attempts + 1)
            checks = await drive_task(request, workspace, service, thread, execution, store)
            store.update(task_id, status="completed", completed=True, checks=checks, error=None,
                         artifacts=collect_artifacts(workspace), usage=_usage(recorder, store.get(task_id).trace_ids))
    except asyncio.CancelledError:
        from app.platform.credits.runtime import current_billing, get_billing_runs
        billing = current_billing()
        billing_run = get_billing_runs().runs.get(billing.operation_id) if billing else None
        explicit = (directory / "stop").exists() or stopped.is_set() or bool(billing_run and billing_run.stopped)
        store.update(task_id, status="stopped" if explicit else "interrupted", completed=False,
                     error="用户停止" if explicit else "执行服务中断，等待核对恢复", artifacts=collect_artifacts(workspace))
        if billing:
            raise
    except BaseException as exc:
        from app.platform.credits.runtime import BudgetExhausted, OperationClosed, OperationStopped, current_billing, get_billing_runs
        billing = current_billing()
        billing_run = get_billing_runs().runs.get(billing.operation_id) if billing else None
        if isinstance(exc, OperationClosed):
            store.update(task_id, status="stopped", completed=False, error=str(exc), artifacts=collect_artifacts(workspace))
            raise
        if billing and (billing.exhausted or (billing_run and billing_run.stopped)
                        or isinstance(exc, (BudgetExhausted, OperationStopped))):
            error = BudgetExhausted() if billing.exhausted else OperationStopped()
            store.update(task_id, status="stopped", completed=False, error=str(error), artifacts=collect_artifacts(workspace))
            raise error
        logger.exception("目标任务失败 task=%s", task_id)
        try:
            artifacts = collect_artifacts(workspace)
        except (OSError, ValueError):
            artifacts = store.get(task_id).artifacts
        store.update(task_id, status="failed", completed=False, error=str(exc), artifacts=artifacts)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        try:
            store.update(task_id, usage=_usage(recorder, store.get(task_id).trace_ids))
        except Exception:
            logger.warning("目标任务用量未完整取得 task=%s", task_id, exc_info=True)
        await recorder.aclose()


def _prepare_candidate(store, task_id, lease_fd):
    """Freeze the one allowed credential before executable candidate imports."""
    import socket
    from urllib.parse import urlparse
    from app.platform.core.settings import get_settings
    from app.platform.isolation.sandbox import restrict_process, runtime_read_paths
    row = store.record(task_id)
    request = TargetTaskRequest.model_validate(row["request"])
    settings = get_settings()
    model = build_frozen_model(request, settings)
    endpoint = urlparse(str(getattr(model, "openai_api_base", None) or
                            (request.model_snapshot.base_url if request.model_snapshot else "")))
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or port in {7788, 7789, 7790}:
        raise ValueError("候选模型地址无效或使用受保护的内部服务端口")
    answers = socket.getaddrinfo(endpoint.hostname, port, type=socket.SOCK_STREAM)
    allowed_hosts = {endpoint.hostname, *(answer[4][0] for answer in answers)}

    def frozen_dns(host, target_port, family=0, type=0, proto=0, flags=0):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host not in allowed_hosts or int(target_port) != port:
            raise OSError("候选只能解析冻结的模型端点")
        result = [answer for answer in answers if (not family or answer[0] == family)
                  and (not type or answer[1] == type) and (not proto or answer[2] == proto)]
        if not result:
            raise OSError("冻结的模型端点没有匹配的地址")
        return result

    socket.getaddrinfo = frozen_dns
    directory = store.directory(task_id)
    workspace = Path(row["metadata"]["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    for name in type(settings).model_fields:
        if any(part in name for part in ("api_key", "master_key", "password", "token", "secret")):
            setattr(settings, name, "")
    settings.evolution_url = settings.evolution_notify_url = settings.platform_url = ""
    settings.workspace_root = str(workspace)
    settings.memory_dir = str(directory / "memory")
    settings.outcome_buffer_path = str(directory / "outcomes.db")
    settings.llm_config_cache_dir = str(directory / "llm_cache")
    settings.artifact_cache_dir = str(directory / "artifacts")
    settings.binding_spool_dir = str(directory / "binding_spool")
    settings.known_production_path = str(directory / "known_production.json")
    from app.platform.memory.store import MemoryStorePool, init_memory_store_pool
    init_memory_store_pool(MemoryStorePool(directory / "memory"))
    os.chdir(directory)
    restrict_process(
        read_paths=[*runtime_read_paths(), store.package_directory(task_id)],
        write_paths=[directory, workspace, Path("/dev/null")], connect_ports=[port], keep_fds=[lease_fd],
    )
    os.environ["TMPDIR"] = str(directory / "tmp")
    (directory / "tmp").mkdir(exist_ok=True)
    return model


def _target_worker(root: str, task_id: str, stopped, parent_pid: int):
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    store = TargetTaskStore(Path(root))
    acquired = False
    try:
        with _file_lock(store.directory(task_id) / "worker.lock", blocking=False) as lease_fd:
            acquired = True
            os.environ["OPENBLAS_NUM_THREADS"] = "1"
            os.environ["OMP_NUM_THREADS"] = "1"
            isolated = not store.record(task_id)["metadata"].get("owner_id")
            model = _prepare_candidate(store, task_id, lease_fd) if isolated else None
            asyncio.run(_run_worker(Path(root), task_id, stopped, parent_pid, prepared_model=model, isolated=isolated))
    except OSError as exc:
        if acquired:
            store.update(task_id, status="failed", error=f"目标执行准备失败：{exc}", completed=False)
        else:
            logger.info("目标任务已有存活执行者 task=%s", task_id)
    except Exception as exc:
        logger.exception("目标执行准备失败 task=%s", task_id)
        store.update(task_id, status="failed", error=str(exc), completed=False)


class TargetTaskManager:
    def __init__(self, root: Path, recorder=None):
        self.store = TargetTaskStore(root)
        self.processes = {}
        self.monitors = {}
        self._notified = {}
        self.recorder = recorder
        self._snapshots = {}

    def result(self, task_id):
        trusted = self.store.trusted(task_id)
        if trusted is None:
            return None
        if trusted.get("verified"):
            return TargetTaskResult.model_validate(trusted["result"])
        try:
            result = self.store.get(task_id)
            if result is None:
                raise ValueError("执行记录缺失")
        except (ValueError, OSError):
            result = TargetTaskResult.model_validate(trusted["result"])
            result = result.model_copy(update={"status": "recovering", "error": "执行记录异常，等待父进程核验"})
        if result.status in TERMINAL:
            result = result.model_copy(update={"status": "recovering", "completed": False})
        expected = trusted["request"]
        return result.model_copy(update={"task_id": task_id, "input_revision": expected["input"]["input_revision"],
                                         "source_commit": expected["source_commit"], "artifact_digest": expected["artifact_digest"]})

    def _trace_evidence(self, request, workspace, trace_ids):
        from app.domains.writing.target_execution import _content, _failed, manuscript_body
        evidence = {}
        if self.recorder is None:
            return evidence
        before_review = ""
        text_name = f"正文/第{request.target.chapter:03d}章.md" if request.target.chapter else None
        latest_text = request.input.files.get(text_name, "") if text_name else ""
        reviews = {}
        tasks = {}
        for trace_id in trace_ids:
            _safe_file(workspace, "traces/index.json")
            for path in (workspace / "traces").rglob("*"):
                if path.is_symlink():
                    raise ValueError("任务trace不能经过符号链接")
            if not self.recorder.register_external_run(trace_id, str(workspace)):
                raise ValueError("审修 trace 不属于固定任务工作区")
            run = self.recorder.find_run_by_trace_id(trace_id, workspace_path=str(workspace))
            if run is None:
                continue
            _safe_file(workspace, run.path)
            for event in self.recorder.read_trace_events(trace_id, hydrate_payloads=True) or []:
                args = event.tool_args if isinstance(event.tool_args, dict) else {}
                target = args.get("subagent_type") or args.get("name")
                if event.type == "artifact_revision" and isinstance(event.artifact, dict):
                    key = str(event.artifact.get("logical_key", "")).lstrip("/")
                    if text_name and key == text_name:
                        output = event.output if isinstance(event.output, dict) else {}
                        latest_text = output.get("content") or latest_text
                        if evidence.get("review") == "success" and before_review and manuscript_body(latest_text) != manuscript_body(before_review):
                            evidence["revised"] = True
                elif event.type == "tool_start" and event.tool_name == "task":
                    tasks[event.tool_call_id] = args
                    if target == "review":
                        chapters = {int(n) for n in re.findall(r"第\s*(\d+)\s*章", str(args.get("description", "")))}
                        if chapters == {request.target.chapter}:
                            reviews[event.tool_call_id] = latest_text
                elif event.type == "tool_end" and event.tool_name == "task":
                    args = tasks.pop(event.tool_call_id, args)
                    target = args.get("subagent_type") or args.get("name")
                    output = _content(event.tool_output)
                    success = not _failed(event.tool_output) and bool(output.strip())
                    if event.tool_call_id in reviews:
                        before_review = reviews[event.tool_call_id]
                        evidence.update({"review": "success" if success else "failed", "review_output": output,
                                         "needs_revision": "通过，无修改意见" not in output.replace(" ", ""), "revised": False})
                    elif target == "volume_qc" and success:
                        volumes = {int(n) for n in re.findall(r"第\s*(\d+)\s*卷", str(args.get("description", "")))}
                        for volume in volumes:
                            evidence.setdefault("volume_qc", {})[str(volume)] = {"success": True}
        if text_name and latest_text:
            evidence["last_saved_text_sha256"] = hashlib.sha256(latest_text.encode("utf-8")).hexdigest()
        return evidence

    def verify_result(self, task_id, trusted):
        request = TargetTaskRequest.model_validate(trusted["request"])
        metadata = trusted["metadata"]
        workspace = Path(metadata["workspace"])
        try:
            row = self.store.record(task_id)
            raw = TargetTaskResult.model_validate(row["result"])
            if row["request"] != trusted["request"] or row["metadata"].get("workspace") != metadata["workspace"]:
                raise ValueError("候选更改了固定任务身份或工作目录")
            if (raw.task_id != task_id or raw.source_commit != request.source_commit
                    or raw.input_revision != request.input.input_revision
                    or (request.artifact_digest and raw.artifact_digest != request.artifact_digest)):
                raise ValueError("候选上报身份与固定请求不一致")
            artifacts = collect_artifacts(workspace)
            if raw.status == "completed" or raw.completed:
                evidence = self._trace_evidence(request, workspace, raw.trace_ids)
                checks = completion_checks(request.target, workspace, text_evidence=evidence)
                if request.target.task_type == "chapter_text":
                    saved = artifacts.get(f"正文/第{request.target.chapter:03d}章.md", "")
                    if hashlib.sha256(saved.encode("utf-8")).hexdigest() != evidence.get("last_saved_text_sha256"):
                        checks["complete"] = False
                        checks["missing"].append("当前正文与审修trace保存的正文不一致")
                if request.target.task_type == "volume_outlines":
                    for volume in request.target.volume_ids:
                        name = f"卷纲/第{volume:02d}卷.md"
                        original = request.input.files.get(name, "")
                        if (artifacts.get(name) != original or not is_final_status(original)) and not evidence.get("volume_qc", {}).get(str(volume), {}).get("success"):
                            checks["missing"].append(f"第{volume}卷缺少实际质检证据")
                    checks["complete"] = not checks["missing"]
                    if not scan_writing_progress(workspace).theme_done or not scan_writing_progress(workspace).outline_done:
                        checks["complete"] = False
                        checks["missing"].append("主题或总纲未通过现有定稿检查")
                if not checks["complete"]:
                    raise ValueError("父进程核验未通过：" + "、".join(checks["missing"]))
                raw = raw.model_copy(update={"status": "completed", "completed": True,
                                             "checks": {**checks, "text_evidence": evidence,
                                                        "identity": trusted["result"]["checks"].get("identity", {})}})
            raw = raw.model_copy(update={"artifacts": artifacts})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            try:
                artifacts = collect_artifacts(workspace)
            except (ValueError, OSError):
                artifacts = {}
            raw = TargetTaskResult.model_validate(trusted["result"]).model_copy(update={
                "status": "failed", "completed": False, "error": str(exc),
                "artifacts": artifacts,
            })
        raw = raw.model_copy(update={"updated_at": _now()})
        approved = {**deepcopy(trusted), "result": raw.model_dump(mode="json"), "verified": True}
        self.store.approve(task_id, approved)
        try:
            journal = self.store.record(task_id)
        except (ValueError, OSError):
            journal = None
        if not isinstance(journal, dict) or not isinstance(journal.get("metadata"), dict):
            journal = deepcopy(trusted)
        journal["request"] = deepcopy(trusted["request"])
        journal["metadata"].update(deepcopy(metadata))
        journal["result"] = raw.model_dump(mode="json")
        with _file_lock(self.store.directory(task_id) / "state.lock"):
            self.store._write(task_id, journal)
        return raw

    def submit(self, request: TargetTaskRequest, *, workspace=None, thread=None, owner_id=None,
               billing_operation=None, public_fingerprint=None):
        existing = self.result(request.task_id)
        if existing is not None:
            result, _ = self.store.create(request)
            self.start(request.task_id)
            return self.result(request.task_id)
        validate_input(request.target, request.input)
        metadata = {"workspace": str(workspace or self.store.directory(request.task_id) / "workspace"), "owner_id": owner_id}
        if owner_id:
            metadata.update(billing_operation_id=billing_operation["operation_id"] if billing_operation else None,
                            billing_disabled_at_start=billing_operation is None, public_fingerprint=public_fingerprint)
        if thread is None:
            from app.schemas.screenplay import ThreadSummary
            now = _now()
            thread = ThreadSummary(thread_id=f"target-{request.task_id}", workspace_id=f"target-{request.task_id}",
                                   session_name="全自动目标任务", workspace_path=metadata["workspace"],
                                   user_id=f"target-{request.task_id}", created_at=now, updated_at=now)
        metadata["thread"] = thread.model_dump(mode="json")
        metadata["base_thread"] = metadata["thread"]
        if owner_id:
            from app.domains.writing.run_gate import claim_target_workspace
            claim_target_workspace(Path(workspace), request.task_id)
        try:
            result, _ = self.store.create(request, metadata=metadata)
            self.start(request.task_id)
            return result
        except BaseException:
            if owner_id:
                from app.domains.writing.run_gate import release_target_workspace
                release_target_workspace(Path(workspace), request.task_id)
            raise

    def ensure_package(self, task_id):
        from app.platform.agent.artifact_client import get_artifact_client
        record = self.store.trusted(task_id)
        request = TargetTaskRequest.model_validate(record["request"])
        destination = self.store.package_directory(task_id)
        marker = destination / ".target-package-ready"
        if (destination / "__init__.py").is_file() and marker.is_file():
            digest = marker.read_text(encoding="utf-8").strip()
            if request.artifact_digest is None or digest == request.artifact_digest:
                return destination
        source = get_artifact_client().ensure_artifact(request.source_commit, expected_digest=request.artifact_digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.parent / f".{task_id}-{uuid.uuid4().hex}"
        shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        digest_path = source / ".digest"
        digest = digest_path.read_text(encoding="utf-8").strip() if digest_path.is_file() else (request.artifact_digest or "")
        (staging / ".target-package-ready").write_text(digest, encoding="utf-8")
        if destination.exists():
            destination.resolve().relative_to(destination.parent.resolve())
            shutil.rmtree(destination)
        staging.replace(destination)
        self.store.journal(task_id, metadata={"package_root": str(destination), "actual_digest": digest or None})
        if digest:
            self.store.update(task_id, artifact_digest=digest)
        return destination

    def start(self, task_id):
        trusted = self.store.trusted(task_id)
        if trusted is None or (trusted.get("verified") and trusted["result"]["status"] in TERMINAL):
            return
        if task_id in self.monitors and not self.monitors[task_id].done():
            return
        trusted["verified"] = False
        self.store.approve(task_id, trusted)
        self._snapshots[task_id] = deepcopy(trusted)
        if trusted["metadata"].get("owner_id"):
            operation_id = trusted["metadata"].get("billing_operation_id")
            if operation_id:
                from app.platform.credits.ledger import FINAL, get_billing
                from app.platform.credits.runtime import get_billing_runs
                operation = get_billing().operation(operation_id, trusted["metadata"]["owner_id"])
                if operation["status"] in FINAL:
                    self.store.update(task_id, status="stopped", completed=False,
                                      error="本次预算操作已结算；请确认新预算后继续已有内容。")
                    self.verify_result(task_id, trusted)
                    self._release_workspace(task_id, trusted)
                    return
                run = get_billing_runs().submit(operation, lambda: self._normal_source(task_id))
                self.monitors[task_id] = run.task
            else:
                from app.platform.credits.ledger import get_billing
                if not trusted["metadata"].get("billing_disabled_at_start") and get_billing().config()["enabled"]:
                    self.store.update(task_id, status="stopped", completed=False, error="请确认本次积分预算后继续已有内容。")
                    self.verify_result(task_id, trusted)
                    self._release_workspace(task_id, trusted)
                    return
                self.monitors[task_id] = asyncio.create_task(self._run_normal(task_id))
            return
        self.monitors[task_id] = asyncio.create_task(self._monitor(task_id))

    async def _normal_source(self, task_id):
        from app.platform.credits.runtime import OperationClosed, OperationStopped, current_billing, sse
        await self._run_normal(task_id)
        billing = current_billing()
        if billing and not billing.ledger.can_continue(billing.operation_id):
            raise OperationClosed()
        result = self.result(task_id)
        if result.trace_ids:
            yield sse("status", {"trace_id": result.trace_ids[-1]})
        if result.completed and result.status == "completed":
            yield sse("final", {"task_id": task_id, "status": result.status})
        elif result.status == "stopped":
            raise OperationStopped()
        else:
            raise RuntimeError("全自动创作未完成，保留已有现场。")

    async def _run_normal(self, task_id):
        from app.platform.credits.runtime import BudgetExhausted, OperationClosed, OperationStopped, current_billing
        trusted = self._snapshots[task_id]
        try:
            billing = current_billing()
            if billing and not billing.ledger.can_continue(billing.operation_id):
                raise OperationClosed()
            while self.store.worker_alive(task_id):
                await asyncio.sleep(0.5)
            with _file_lock(self.store.directory(task_id) / "worker.lock", blocking=False):
                result = self.store.get(task_id)
                if result.status not in TERMINAL:
                    if (self.store.directory(task_id) / "stop").exists():
                        self.store.update(task_id, status="stopped", completed=False)
                    else:
                        await asyncio.to_thread(self.ensure_package, task_id)
                        await _run_worker(self.store.root, task_id, threading.Event(), None)
        except asyncio.CancelledError:
            if self.store.get(task_id).status not in TERMINAL:
                explicit = (self.store.directory(task_id) / "stop").exists()
                self.store.update(task_id, status="stopped" if explicit else "interrupted", completed=False,
                                  error="用户停止" if explicit else "执行服务中断，已有现场保留")
            raise
        except (BudgetExhausted, OperationClosed, OperationStopped) as exc:
            if self.store.get(task_id).status not in TERMINAL:
                self.store.update(task_id, status="stopped", completed=False, error=str(exc))
            raise
        except Exception as exc:
            if self.store.get(task_id).status not in TERMINAL:
                self.store.update(task_id, status="failed", completed=False, error=type(exc).__name__)
            raise
        finally:
            self.verify_result(task_id, trusted)
            await asyncio.to_thread(self._register_traces, task_id)
            self._release_workspace(task_id, trusted)

    async def _monitor(self, task_id):
        trusted = self._snapshots[task_id]
        workspace = Path(trusted["metadata"]["workspace"])
        if self.store.worker_alive(task_id):
            while self.store.worker_alive(task_id):
                await asyncio.to_thread(self._register_traces, task_id)
                await asyncio.sleep(0.5)
        try:
            row = self.store.record(task_id)
            result = TargetTaskResult.model_validate(row["result"])
            if row["request"] != trusted["request"] or row["metadata"].get("workspace") != str(workspace):
                self.verify_result(task_id, trusted)
                self._release_workspace(task_id, trusted)
                return
        except (ValueError, OSError, KeyError, TypeError):
            self.verify_result(task_id, trusted)
            self._release_workspace(task_id, trusted)
            return
        if result.status in TERMINAL:
            self.verify_result(task_id, trusted)
            self._release_workspace(task_id, trusted)
            return
        if (self.store.directory(task_id) / "stop").exists():
            self.store.update(task_id, status="stopped", completed=False)
            self.verify_result(task_id, trusted)
            self._release_workspace(task_id, trusted)
            return
        # Only the journal's execution progress is mutable. Restore the parent's
        # immutable identity and paths before importing a candidate on recovery.
        row["request"] = deepcopy(trusted["request"])
        row["metadata"].update(deepcopy(trusted["metadata"]))
        self.store._write(task_id, row)
        if result.status in {"running", "recovering", "interrupted", "stopping"}:
            self.store.journal(task_id, event={"type": "interrupted", "reason": "原执行者已退出，核对已保存结果后续跑"})
            self.store.update(task_id, status="recovering")
        try:
            await asyncio.to_thread(self.ensure_package, task_id)
        except Exception as exc:
            self.store.update(task_id, status="interrupted", error=f"候选制品准备失败，等待恢复：{exc}")
            return
        if (self.store.directory(task_id) / "stop").exists():
            return
        context = mp.get_context("spawn")
        stopped = context.Event()
        process = context.Process(target=_target_worker, args=(str(self.store.root), task_id, stopped, os.getpid()))
        self.processes[task_id] = process, stopped
        process.start()
        try:
            while process.is_alive():
                await asyncio.to_thread(self._register_traces, task_id)
                await asyncio.sleep(0.5)
            process.join(timeout=0)
            try:
                result = self.store.get(task_id)
                if result and result.status not in TERMINAL and result.status != "interrupted":
                    self.store.update(task_id, status="interrupted", error=f"隔离执行者退出（code={process.exitcode}），已保留现场")
            except (ValueError, OSError, KeyError, TypeError):
                pass
            self.verify_result(task_id, trusted)
            await asyncio.to_thread(self._register_traces, task_id)
        finally:
            self.processes.pop(task_id, None)
            self._release_workspace(task_id, trusted)

    def _release_workspace(self, task_id, trusted):
        result = self.result(task_id)
        if trusted["metadata"].get("owner_id") and result and result.status in TERMINAL:
            from app.domains.writing.run_gate import release_target_workspace
            release_target_workspace(Path(trusted["metadata"]["workspace"]), task_id)

    def _register_traces(self, task_id):
        if self.recorder is None:
            return
        try:
            row = self.store.record(task_id)
            trusted = self._snapshots.get(task_id) or self.store.trusted(task_id)
            metadata = trusted["metadata"]
            recorder = self.recorder
            for trace_id in row["result"]["trace_ids"]:
                if not recorder.register_external_run(trace_id, metadata["workspace"]):
                    continue
                run = recorder.find_run_by_trace_id(trace_id)
                if run is not None:
                    _safe_file(Path(metadata["workspace"]), run.path)
                status = run.status if run else "running"
                key = (task_id, trace_id)
                if self._notified.get(key) != status:
                    from app.platform.core.settings import get_settings
                    from app.platform.trace.recorder import _notify_evolution
                    from app.schemas.screenplay import ThreadSummary
                    if key not in self._notified:
                        bind_frozen_run(TargetTaskRequest.model_validate(trusted["request"]), trace_id, get_settings())
                    _notify_evolution(ThreadSummary.model_validate(metadata["thread"]), trace_id, status,
                                      run.model_dump(mode="json") if run else None)
                    self._notified[key] = status
        except (AssertionError, OSError, ValueError, KeyError, TypeError):
            pass

    async def stop(self, task_id):
        result = self.result(task_id)
        if result is None:
            return None
        if result.status in TERMINAL:
            return result
        (self.store.directory(task_id) / "stop").touch()
        self.store.update(task_id, status="stopping", completed=False)
        trusted = self._snapshots.get(task_id) or self.store.trusted(task_id)
        if trusted["metadata"].get("owner_id"):
            monitor = self.monitors.get(task_id)
            operation_id = trusted["metadata"].get("billing_operation_id")
            if operation_id:
                from app.platform.credits.ledger import get_billing
                from app.platform.credits.runtime import get_billing_runs
                operation = get_billing().operation(operation_id, trusted["metadata"]["owner_id"])
                get_billing_runs().request_stop(operation["user_id"], operation["thread_id"], None)
            elif monitor and not monitor.done():
                monitor.cancel()
            if monitor and not monitor.done():
                try:
                    await asyncio.wait_for(asyncio.shield(monitor), timeout=10)
                except TimeoutError:
                    return self.store.update(task_id, status="stopping", completed=False, error="停止已受理，正在核实执行者退出")
                except asyncio.CancelledError:
                    pass
            result = self.result(task_id)
            if result.status in TERMINAL:
                return result
        pair = self.processes.get(task_id)
        if pair:
            from app.platform.isolation.worker_process import _force_terminate
            process, stopped = pair
            stopped.set()
            deadline = asyncio.get_running_loop().time() + 10
            while process.is_alive() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.1)
            if process.is_alive():
                await asyncio.to_thread(_force_terminate, process)
            process.join(timeout=0)
        if self.store.worker_alive(task_id):
            return self.store.update(task_id, status="stopping", completed=False, error="停止已受理，正在核实执行者退出")
        self.store.update(task_id, status="stopped", completed=False, error="用户停止，已生成内容保留")
        trusted = self._snapshots.get(task_id) or self.store.trusted(task_id)
        result = self.verify_result(task_id, trusted)
        self._release_workspace(task_id, trusted)
        return result

    def recover(self):
        for trusted in self.store.trusted_all():
            self.start(trusted["request"]["task_id"])

    async def close(self):
        for task in self.monitors.values():
            task.cancel()
        await asyncio.gather(*self.monitors.values(), return_exceptions=True)


_manager = None


def get_manager():
    global _manager
    if _manager is None:
        from app.platform.core.settings import get_settings
        root = Path(get_settings().workspace_root).resolve().parent / "target_tasks"
        _manager = TargetTaskManager(root)
    return _manager
