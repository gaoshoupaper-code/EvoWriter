"""Run a published writing Harness with access to one instruction and one conversation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import struct
import sys
import tempfile

from contracts.writing_artifacts import artifact_relpath, volume_draft_relpath

_MAX_MESSAGE = 32 * 1024 * 1024
logger = logging.getLogger("writer.creative.worker")


class _WorkerDisconnected(RuntimeError):
    pass
@dataclass
class _Live:
    process: asyncio.subprocess.Process
    stopped: bool = False


_running: dict[str, _Live] = {}


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _packet(value: bytes) -> bytes:
    if len(value) > _MAX_MESSAGE:
        raise ValueError("创作进程消息超过上限")
    return struct.pack("!I", len(value)) + value


def _size(header: bytes) -> int:
    size = struct.unpack("!I", header)[0]
    if size > _MAX_MESSAGE:
        raise ValueError("创作进程消息超过上限")
    return size


async def _receive(reader) -> bytes:
    return await reader.readexactly(_size(await reader.readexactly(4)))


async def _send(writer, data: bytes):
    writer.write(_packet(data))
    await writer.drain()


def _artifact(args):
    if args.get("draft"):
        return volume_draft_relpath(args["volume"])
    return artifact_relpath(args["kind"], volume=args.get("volume"), chapter=args.get("chapter"), name=args.get("name"))


def _accept_change(task, message):
    """Paths are always derived by the parent from the authorized tool arguments."""
    args = message["args"]
    if not isinstance(args, dict) or not isinstance(message["call"], str):
        raise ValueError("无效的产物操作")
    if message["op"] == "save":
        denial = task.require_write("save_artifact", args)
        if denial:
            return {"receipt": "[拒绝] " + denial}
        path = _artifact(args)
        receipt = task.apply_worker_save(message["call"], args, message["content"])
    elif message["op"] == "character":
        if task.agent != "text":
            return {"receipt": "[拒绝] 仅正文可更新人物现状"}
        path = artifact_relpath("character", name=args["name"])
        receipt = task.apply_worker_character(message["call"], **args)
    else:
        raise ValueError("该创作进程操作不可用")
    with task.book.tx() as db:
        row = db.execute("SELECT text FROM private.files WHERE task=? AND path=?", (task.id, path)).fetchone()
    return {"receipt": receipt, "content": row[0] if row else None}


async def _changes(task, reader, writer):
    while True:
        message = json.loads(await _receive(reader))
        try:
            answer = await asyncio.to_thread(_accept_change, task, message)
        except (KeyError, ValueError, TypeError):
            answer = {"receipt": "[拒绝] 无效的产物操作"}
        await _send(writer, _json(answer))


async def _models(reader, writer, model, billing):
    from app.domains.writing.creative_worker_model import forward_model
    from app.platform.credits.ledger import BillingError

    async def emit(data):
        await _send(writer, b"C" + data)

    while True:
        body = json.loads(await _receive(reader))
        try:
            await forward_model(body, model, billing, emit)
        except BillingError:
            raise
        except (ValueError, RuntimeError):
            await _send(writer, b"X")
        else:
            await _send(writer, b"E")


def cancel_private_instruction(task_id: str) -> bool:
    live = _running.get(task_id)
    if live is not None and live.process.returncode is None:
        try:
            live.process.terminate()
        except ProcessLookupError:
            return False
        live.stopped = True
        return True
    return False


async def _parent_frames(task, channels, model, billing):
    (frames, _), (changes, changes_out), (models, models_out) = channels
    helpers = {asyncio.create_task(_changes(task, changes, changes_out)),
               asyncio.create_task(_models(models, models_out, model, billing))}
    pending_frame = None
    try:
        while True:
            pending_frame = asyncio.create_task(_receive(frames))
            while True:
                done, _ = await asyncio.wait({pending_frame, *helpers}, return_when=asyncio.FIRST_COMPLETED)
                for worker in helpers.intersection(done):
                    helpers.remove(worker)
                    try:
                        worker.result()
                    except asyncio.IncompleteReadError:
                        pass
                if pending_frame.done():
                    break
            try:
                message = json.loads(pending_frame.result())
            except asyncio.IncompleteReadError:
                raise _WorkerDisconnected("创作进程中断，已保存内容可继续") from None
            if message.get("done"):
                return
            if "error" in message:
                raise RuntimeError("创作进程未完成：" + str(message["error"])[:400])
            yield message["frame"]
    finally:
        if pending_frame is not None:
            helpers.add(pending_frame)
        for worker in helpers:
            worker.cancel()
        await asyncio.gather(*helpers, return_exceptions=True)


def _controlled_model(settings, restored):
    from app.domains.writing.models import build_writer_model
    from app.platform.llm_config.loader import get_active_llm_config
    from contracts.platform import LlmConfigSnapshot

    config = get_active_llm_config()
    if not config:
        return build_writer_model(settings), None
    # Take endpoint and key from the same active credential record. A restored
    # binding can fix the model name only while its trusted endpoint still matches.
    name = restored.model if restored and restored.base_url.rstrip("/") == config.base_url.rstrip("/") else config.model
    snapshot = LlmConfigSnapshot(model=name, base_url=config.base_url, source="evolution")
    model = build_writer_model(settings, api_key=config.api_key, base_url=config.base_url, model_name_override=name)
    return model, snapshot


async def run_private_instruction(task, row, payload, owner_id, traceparent=None, *, binding_service):
    from app.platform.agent.artifact_client import get_artifact_client
    from app.platform.core.db import WritingSkillRepository, get_database
    from app.platform.core.settings import get_settings
    from app.platform.credits.runtime import current_billing

    if sys.platform != "linux":
        raise RuntimeError("正式创作的受限进程需要 Linux")
    settings = get_settings()
    bound = await asyncio.to_thread(binding_service._resume_binding_snapshot, task.row["trace"]) if task.row.get("trace") else None
    model, task.binding_snapshot = await asyncio.to_thread(_controlled_model, settings, bound)
    model_name = model.model_name
    from app.domains.writing.deepseek_thinking import DeepSeekThinkingChatModel
    if isinstance(model, DeepSeekThinkingChatModel):
        model_name = "deepseek:" + model_name
    skills = [s for s in WritingSkillRepository(get_database()).list_enabled_for_owner(owner_id) if s["stage"] == task.agent]
    checkout = await asyncio.to_thread(get_artifact_client().ensure_artifact, task.row["rev"])
    session = task.book.session_dir(task.agent, row.thread_id).resolve()
    executor_root = Path(__file__).resolve().parents[3]
    # The subprocess starts without service credentials. Only the parent opens the model endpoint.
    env = {"PATH": os.defpath, "PYTHONPATH": os.pathsep.join((str(executor_root), str(executor_root.parent))),
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "LANG": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="instruction-", dir=task.book.private) as tmp:
        work, package = Path(tmp) / "work", Path(tmp) / "package"
        await asyncio.to_thread(shutil.copytree, checkout, package)
        await asyncio.to_thread(shutil.copytree, task.view, work / "manuscripts")
        pairs = [socket.socketpair() for _ in range(3)]
        channels = []
        process = None
        try:
            fds = [child.fileno() for _, child in pairs]
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.domains.writing.creative_worker_child", *map(str, fds), str(os.getpid()),
                env=env, cwd=executor_root, pass_fds=fds, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            live = _Live(process)
            _running[task.id] = live
            if task.book.task(task.agent, task_id=task.id).row["status"] != "running":
                from app.platform.credits.runtime import OperationStopped
                raise OperationStopped()
            for host, child in pairs:
                child.close()
                channels.append(await asyncio.open_connection(sock=host))
            data = {"work": str(work), "session": str(session), "package": str(package), "agent": task.agent,
                    "id": task.id, "recovering": task.recovering, "rev": task.row["rev"], "volume": task.row["volume"],
                    "chapter": task.row["chapter"], "model": model_name, "skills": skills, "owner": owner_id,
                    "thread": row.model_dump(mode="json"), "payload": payload.model_dump(mode="json"),
                    "traceparent": traceparent, "agent_mode": settings.writer_agent_mode,
                    "history_budget": settings.conversation_history_char_budget}
            await _send(channels[0][1], _json(data))
            try:
                async for frame in _parent_frames(task, channels, model, current_billing()):
                    yield frame
            except (RuntimeError, ConnectionError) as exc:
                if live.stopped:
                    from app.platform.credits.runtime import OperationStopped
                    raise OperationStopped() from None
                if isinstance(exc, (_WorkerDisconnected, ConnectionError)):
                    try:
                        code = await asyncio.wait_for(process.wait(), timeout=1)
                    except TimeoutError:
                        code = None
                    logger.warning("写作进程通道断开 pid=%s returncode=%s", process.pid, code)
                raise
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            if task.id in _running and _running[task.id].process is process:
                _running.pop(task.id, None)
            for _, writer in channels:
                writer.close()
            for host, child in pairs:
                host.close()
                child.close()
