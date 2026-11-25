"""Child side of a single published creative instruction. Enter Landlock before importing its package."""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import sys
from threading import Lock
from hashlib import sha256

from app.domains.writing.creative_worker import _artifact, _json, _packet, _receive, _send, _size
from contracts.sentence_draft import DRAFT, DraftState


def _read_exact(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise EOFError("创作服务连接已关闭")
    return data


def _message(stream):
    return json.loads(_read_exact(stream, _size(_read_exact(stream, 4))))


class _Instruction:
    def __init__(self, data, changes):
        self.row = {name: data[name] for name in ("rev", "volume", "chapter")}
        self.id, self.agent, self.recovering = data["id"], data["agent"], data["recovering"]
        self.package_root = Path(data["package"])
        self.view = Path(data["work"]) / "manuscripts"
        self._socket = changes
        self._reader = changes.makefile("rb")
        self._lock = Lock()
        self._committed = {}

    def require_write(self, name, args):
        from contracts.creative_harness import write_denial
        return write_denial(self.agent, name, args, self.row["volume"], self.row["chapter"],
                            package_root=self.package_root)

    def _submit(self, op, args, rel, **fields):
        from app.domains.writing.creative_scope import CALL_ID
        with self._lock:
            self._socket.sendall(_packet(_json({"op": op, "call": CALL_ID.get(), "args": args, **fields})))
            response = _message(self._reader)
        if "content" in response:
            local = self.view / rel
            if response["content"] is None:
                local.unlink(missing_ok=True)
            else:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(response["content"], encoding="utf-8")
        return response["receipt"]

    def capture_tool_output(self, args):
        path = self.view / _artifact(args)
        return path.read_bytes() if path.is_file() else None

    def publish_tool_output(self, args, before):
        from app.domains.writing.creative_scope import CALL_ID
        if reason := self.require_write("save_artifact", args):
            return "[拒绝] " + reason
        rel = _artifact(args)
        path = self.view / rel
        current = self.capture_tool_output(args)
        if current is None or current == before:
            return None
        key = (CALL_ID.get(), rel)
        if self._committed.get(key) == sha256(current).digest():
            return None
        receipt = self._submit("save", args, rel, content=current.decode("utf-8"))
        if isinstance(receipt, str) and receipt.startswith("[拒绝]"):
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
            return receipt
        canonical = self.capture_tool_output(args)
        if canonical is not None:
            self._committed[key] = sha256(canonical).digest()
        return None

    def save(self, args, save):
        if reason := self.require_write("save_artifact", args):
            return "[拒绝] " + reason
        before = self.capture_tool_output(args)
        result = save()
        return self.publish_tool_output(args, before) or result

    def character(self, name, field, value, reason):
        from contracts.writing_artifacts import artifact_relpath
        args = {"name": name, "field": field, "value": value, "reason": reason}
        return self._submit("character", args, artifact_relpath("character", name=name))


async def _run(data, frames, changes, models):
    from app.domains.writing.agent import MetaAgentService
    from app.domains.writing.package_skill_reads import PackageSkillReads
    from app.domains.writing.creative_scope import ACTIVE
    from app.domains.writing.creative_worker_model import make_worker_model
    from app.platform.agent.loader import load_package
    from app.platform.core.settings import get_settings
    from app.platform.trace import TraceRecorder
    from app.schemas.screenplay import ScreenplayGenerateRequest, ThreadSummary
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    model_in, model_out = await asyncio.open_connection(sock=models)
    _, frames_out = await asyncio.open_connection(sock=frames)
    model_lock = asyncio.Lock()

    async def exchange(body):
        async with model_lock:
            await _send(model_out, _json(body))
            ended = False
            try:
                while True:
                    packet = await _receive(model_in)
                    if packet[:1] != b"C":
                        ended = True
                        if packet != b"E":
                            raise RuntimeError("创作模型调用失败")
                        return
                    yield packet[1:]
            finally:
                # A caller may stop consuming a streamed response early. Drain its
                # remaining frames before the next request uses this connection.
                if not ended:
                    while (await _receive(model_in))[:1] == b"C":
                        pass

    task = _Instruction(data, changes)
    task.package = load_package(task.package_root, "harness_instruction_" + task.row["rev"])
    model = make_worker_model(data["model"], exchange)
    session = Path(data["session"])
    thread = ThreadSummary.model_validate({**data["thread"], "workspace_path": str(session)})
    payload = ScreenplayGenerateRequest.model_validate(data["payload"])
    recorder = TraceRecorder()
    recorder.start_drain()

    class Service(MetaAgentService):
        def _issue_run_binding(self, trace_id, run_purpose):
            return None, None

        def _resume_binding_snapshot(self, trace_id):
            return None

        def _resolve_model(self, owner_id):
            return model

        def _list_enabled_stage_skills(self, workspace_id, owner_id, workspace_path):
            return data["skills"]

        def _resolve_meta_style(self, workspace_id, owner_id=None):
            return None

        def _resolve_style_for_subagent(self, workspace_id, style_key, owner_id=None):
            return None

    token = ACTIVE.set(task)
    # 恢复任务时带上「已写进文件到哪一句」；本轮新写的句子从这之后才算待存。
    draft_token = DRAFT.set(DraftState(flushed_through=data.get("draft_flushed_through")))
    try:
        checkpoint = session / "checkpoint.sqlite3"
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            await saver.setup()
            service = Service(get_settings(), task.view, recorder, None, saver)
            service.package_skill_reads = PackageSkillReads(checkpoint, task.id, task.row["rev"])
            async for frame in service.generate_stream(payload, thread, owner_id=data["owner"], traceparent=data["traceparent"]):
                await _send(frames_out, _json({"frame": frame}))
    finally:
        DRAFT.reset(draft_token)
        ACTIVE.reset(token)
        await recorder.aclose()
        model_out.close()
    await _send(frames_out, _json({"done": True}))


def _finish_on_sigterm(signum, _frame):
    # 作者主动停止时把强杀变成正常收尾：运行记录和检查点先落盘再退出，停止前已写的句子才补得回来
    # （REQ-20261125-154544）。父进程等 3 秒后仍会强杀，这里卡住也不会挂死。
    raise SystemExit(128 + signum)


def main():
    from app.platform.isolation.sandbox import restrict_process, runtime_read_paths
    signal.signal(signal.SIGTERM, _finish_on_sigterm)
    fds = [int(v) for v in sys.argv[1:4]]
    parent = int(sys.argv[4])
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent:
        raise RuntimeError("创作宿主已退出")
    frames, changes, models = [socket.socket(fileno=fd) for fd in fds]
    errors = frames
    try:
        with frames.makefile("rb") as initial:
            data = _message(initial)
        work = Path(data["work"])
        os.chdir(work)
        restrict_process(read_paths=[*runtime_read_paths(), Path(data["package"])],
                         write_paths=[work, Path(data["session"]), Path("/dev/null")],
                         connect_ports=(), keep_fds=fds)
        # The async transport owns frames and can close it during exception unwinding.
        errors = frames.dup()
        os.environ.update({"WRITING_AGENT_ID": data["agent"], "WRITER_MODEL": data["model"],
                           "WRITER_FRONTEND_ORIGIN": "", "WRITER_AGENT_MODE": data["agent_mode"],
                           "ADMIN_PASSWORD": "", "MASTER_KEY": "", "PLATFORM_URL": "",
                           "CONVERSATION_HISTORY_CHAR_BUDGET": str(data["history_budget"]), "TMPDIR": str(work)})
        asyncio.run(_run(data, frames, changes, models))
    except Exception as exc:
        try:
            errors.sendall(_packet(_json({"error": f"{type(exc).__name__}：{str(exc)[:320]}"})))
        except OSError:
            pass
        raise


if __name__ == "__main__":
    main()
