import asyncio
import json
from pathlib import Path
import socket
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.domains.writing import creative_worker as host
from app.domains.writing.creative_worker_child import _Instruction, _message
from app.domains.writing.creative_scope import Book, CALL_ID


def active_task(tmp_path, monkeypatch, agent="outline"):
    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "private"))
    book = Book(tmp_path / "shared")
    book.register_thread(agent, "thread")
    task = book.start(agent, "thread", "input", {"prompt": "创作"}, 1, 1, "a" * 40)
    task.begin()
    return task


def test_worker_can_publish_only_through_parent_and_replays_a_save_once(tmp_path, monkeypatch):
    task = active_task(tmp_path, monkeypatch)
    with _channels() as (child, parent):
        view = _Instruction({"rev": "a" * 40, "agent": "outline", "volume": 1, "chapter": 1,
                             "work": str(tmp_path / "isolated"), "id": task.id, "recovering": False,
                             "package": str(tmp_path / "pkg")}, child)
        view.view.mkdir(parents=True)
        with parent.makefile("rb") as requests:
            def serve():
                for _ in range(3):
                    reply = host._accept_change(task, _message(requests))
                    parent.sendall(host._packet(host._json(reply)))
            responder = threading.Thread(target=serve)
            responder.start()
            try:
                token = CALL_ID.set("save-once")
                def save(text):
                    (view.view / "总纲.md").write_text(text, encoding="utf8")
                    return "已保存"
                args = {"kind": "outline", "mode": "full"}
                assert view.save(args, lambda: save("第一稿")) == "已保存"
                view.save(args, lambda: save("重放"))
                assert (view.view / "总纲.md").read_text(encoding="utf8") == "第一稿"
                # Evolved tools may write a staged file without calling the seed's save wrapper.
                token2 = CALL_ID.set("next-save")
                try:
                    before = view.capture_tool_output(args)
                    save("改稿")
                    assert view.publish_tool_output(args, before) is None
                    assert view.publish_tool_output(args, before) is None
                finally:
                    CALL_ID.reset(token2)
            finally:
                CALL_ID.reset(token)
                responder.join(timeout=2)
        assert not responder.is_alive()
        assert (task.book.root / "总纲.md").read_text(encoding="utf8") == "改稿"
        assert (view.view / "总纲.md").read_text(encoding="utf8") == "改稿"
        refused = host._accept_change(task, {"op": "save", "call": "foreign", "content": "正文",
                                           "args": {"kind": "chapter_text", "chapter": 1}})
        assert refused["receipt"].startswith("[拒绝]") and "content" not in refused
        assert not (task.book.root / "正文").exists()


@contextmanager
def _channels():
    child, parent = socket.socketpair()
    with child, parent:
        yield child, parent


def test_parent_preserves_sse_stream_and_requires_a_terminal_message():
    async def collect(packets):
        async def transport():
            return asyncio.StreamReader(), None
        channels = [await transport() for _ in range(3)]
        for packet in packets:
            channels[0][0].feed_data(host._packet(host._json(packet)))
        for reader, _ in channels:
            reader.feed_eof()
        return [frame async for frame in host._parent_frames(None, channels, None, None)]
    interrupt = 'event: interrupt\ndata: {"question":"继续吗"}\n\n'
    assert asyncio.run(collect([{"frame": interrupt}, {"done": True}])) == [interrupt]
    with pytest.raises(RuntimeError, match="中断"):
        asyncio.run(collect([]))


def test_stop_targets_only_the_named_instruction(monkeypatch):
    class Process:
        returncode = None
        terminated = False
        def terminate(self):
            self.terminated = True
    first, second = host._Live(Process()), host._Live(Process())
    monkeypatch.setattr(host, "_running", {"first": first, "second": second})
    assert not host.cancel_private_instruction("missing")
    assert host.cancel_private_instruction("first")
    assert first.process.terminated and first.stopped
    assert not second.process.terminated and not second.stopped


@pytest.mark.parametrize(("restored_url", "expected_name"), [
    ("https://current.invalid/v1/", "old-name"), ("https://old.invalid/v1", "active-name")])
def test_model_binding_uses_credentials_from_the_current_endpoint(monkeypatch, restored_url, expected_name):
    from app.domains.writing import models
    from app.platform.llm_config import loader
    active = SimpleNamespace(model="active-name", base_url="https://current.invalid/v1", api_key="fixture-key")
    monkeypatch.setattr(loader, "get_active_llm_config", lambda: active)
    monkeypatch.setattr(models, "build_writer_model", lambda _, **kwargs: kwargs)
    request, snapshot = host._controlled_model(None, SimpleNamespace(model="old-name", base_url=restored_url))
    assert request == {"api_key": "fixture-key", "base_url": active.base_url, "model_name_override": expected_name}
    assert snapshot.model == expected_name and snapshot.base_url == active.base_url
    assert "fixture-key" not in snapshot.model_dump_json()


def test_linux_worker_confirms_restarts_and_saves_using_real_scoped_harness(tmp_path, monkeypatch):
    from app.platform.isolation.sandbox import landlock_abi
    if landlock_abi() < 4:
        pytest.skip("requires Linux x86_64 with Landlock TCP support")

    from contracts.creative_harness import build_agent_package
    from app.domains.writing import creative_worker_model
    from app.platform.agent import artifact_client
    from app.platform.core import db, settings
    from app.platform.credits.runtime import frame_data
    from app.schemas.screenplay import ScreenplayGenerateRequest, ThreadSummary

    # Keep fatal-signal diagnostics visible in a guest with no host debugger.
    spawn = asyncio.create_subprocess_exec
    wrapper = """
import faulthandler, runpy, sys
from app.platform.isolation import sandbox
faulthandler.enable(all_threads=True)
enter = sandbox.restrict_process
def traced(**kwargs):
    print("creative worker: entering sandbox", file=sys.stderr, flush=True)
    result = enter(**kwargs)
    print("creative worker: sandbox active; loading Harness", file=sys.stderr, flush=True)
    return result
sandbox.restrict_process = traced
runpy.run_module(sys.argv.pop(1), run_name="__main__", alter_sys=True)
"""
    async def launch(python, flag, module, *args, **kwargs):
        assert flag == "-m"
        kwargs["stderr"] = None
        return await spawn(python, "-c", wrapper, module, *args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    task = active_task(tmp_path, monkeypatch)
    pkg = tmp_path / "package"
    build_agent_package(Path(__file__).resolve().parents[2] / "evolution/harnesses/repo", pkg, "outline")
    monkeypatch.setattr(artifact_client, "get_artifact_client", lambda: SimpleNamespace(ensure_artifact=lambda _: pkg))
    monkeypatch.setattr(db, "get_database", lambda: None)
    monkeypatch.setattr(db.WritingSkillRepository, "list_enabled_for_owner", lambda *args: [])
    monkeypatch.setattr(settings, "get_settings", lambda: SimpleNamespace(writer_agent_mode="live", conversation_history_char_budget=96000))
    monkeypatch.setattr(host, "_controlled_model", lambda *_args: (SimpleNamespace(model_name="test-model"), None))
    binder = SimpleNamespace(_resume_binding_snapshot=lambda _: None)
    session = task.book.session_dir(task.agent, "thread")
    row = ThreadSummary(thread_id="thread", workspace_id="book", session_name="测试", workspace_path=str(session),
                        created_at="now", updated_at="now", user_id="owner")
    saved = "# 总纲\n\n## 主题\n重建河港。"
    calls = iter([("confirm", "confirm_with_user", {"question": "从河港开场吗？", "options": [
                      {"label": "采用", "description": "从渡口起笔"}]}),
                  ("save", "save_artifact", {"kind": "outline", "mode": "full", "content": saved}), None])
    model_calls = []

    async def model_reply(body, model, billing, emit):
        action = next(calls)
        model_calls.append(action[1] if action else "final")
        message = {"role": "assistant", "content": "已保存总纲。" if action is None else ""}
        if action:
            message["tool_calls"] = [{"index": 0, "id": action[0], "type": "function", "function": {
                "name": action[1], "arguments": json.dumps(action[2], ensure_ascii=False)}}]
        finish = "tool_calls" if action else "stop"
        value = {"id": "chatcmpl-test", "created": 1, "model": "test-model",
                 "choices": [{"index": 0, "delta" if body.get("stream") else "message": message, "finish_reason": finish}]}
        if body.get("stream"):
            await emit(b"data: " + host._json(value) + b"\n\ndata: [DONE]\n\n")
        else:
            await emit(host._json(value))
    monkeypatch.setattr(creative_worker_model, "forward_model", model_reply)

    async def turn(payload):
        gen = host.run_private_instruction(task, row, payload, "owner", binding_service=binder)
        observed = []
        try:
            async with asyncio.timeout(360):
                async for frame in gen:
                    observed.append(frame_data(frame))
        except TimeoutError:
            pytest.fail(f"worker turn timed out, last SSE={[event for event, _ in observed[-10:]]}, "
                        f"model calls={model_calls}, saves={task.info()['progress']}")
        return observed

    request = ScreenplayGenerateRequest(thread_id="thread", message_id="first", prompt="从河港开场")
    first = asyncio.run(turn(request))
    trace = next(msg["trace_id"] for ev, msg in first if ev == "status" and msg.get("status") == "started")
    iv = next(msg for ev, msg in first if ev == "interrupt")
    assert iv["question"] == "从河港开场吗？"
    task.mark("running", trace)
    task.interrupt(iv)
    assert not (task.book.root / "总纲.md").exists()
    task.begin(resume=True)
    second = asyncio.run(turn(request.model_copy(update={"message_id": "second", "trace_id": trace, "resume": "采用"})))
    assert any(ev == "final" for ev, _ in second)
    assert (task.book.root / "总纲.md").read_text(encoding="utf8").count("重建河港。") == 1
