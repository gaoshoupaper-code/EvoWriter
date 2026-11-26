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


# 启动子进程的原始函数：补丁函数被重复套用时仍回到它，不依赖同一次测试里的补丁顺序。
_SPAWN = asyncio.create_subprocess_exec


@contextmanager
def _linux_text_worker(tmp_path, monkeypatch, *, by_paragraph=True, replies=()):
    """真实受限子进程加正文包，模型由 replies 脚本代答（父进程里的转发函数）。

    replies 每项是一次模型回复：字符串是纯文字收尾，三元组是 (调用编号, 工具名, 参数)。
    特殊项 "FAIL" 让模型服务从此报错，直到把 env.failing 改回 False。
    """
    from app.platform.isolation.sandbox import landlock_abi
    if landlock_abi() < 4:
        pytest.skip("requires Linux x86_64 with Landlock TCP support")

    from contracts.creative_harness import HEADER_TOOL, SENTENCE_TOOLS, build_agent_package
    from app.domains.writing import creative_worker_model
    from app.platform.agent import artifact_client
    from app.platform.core import db, settings
    from app.platform.credits.runtime import frame_data
    from app.schemas.screenplay import ThreadSummary

    wrapper = """
import faulthandler, runpy, sys
faulthandler.enable(all_threads=True)
runpy.run_module(sys.argv.pop(1), run_name="__main__", alter_sys=True)
"""

    async def launch(python, flag, module, *args, **kwargs):
        assert flag == "-m"
        kwargs["stderr"] = None
        return await _SPAWN(python, "-c", wrapper, module, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "private"))
    shared = tmp_path / "shared"
    for rel, body in {"章纲/第001章.md": "靠岸", "文风.md": "短句"}.items():
        path = shared / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    book = Book(shared)
    book.register_thread("text", "thread")
    task = book.start("text", "thread", "input", {"prompt": "创作"}, 1, 1, "a" * 40)
    task.begin()
    pkg = tmp_path / "package"
    build_agent_package(Path(__file__).resolve().parents[2] / "evolution/harnesses/repo", pkg, "text")
    if not by_paragraph:  # 已发布的旧包：清单里没有写作工具，起草仍走整批保存
        path = pkg / "architecture.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["agents"][0]["tools"] = [
            name for name in manifest["agents"][0]["tools"] if name not in (*SENTENCE_TOOLS, HEADER_TOOL)]
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(artifact_client, "get_artifact_client", lambda: SimpleNamespace(ensure_artifact=lambda _: pkg))
    monkeypatch.setattr(db, "get_database", lambda: None)
    monkeypatch.setattr(db.WritingSkillRepository, "list_enabled_for_owner", lambda *args: [])
    monkeypatch.setattr(settings, "get_settings", lambda: SimpleNamespace(writer_agent_mode="live", conversation_history_char_budget=96000))
    monkeypatch.setattr(host, "_controlled_model", lambda *_args: (SimpleNamespace(model_name="test-model"), None))
    binder = SimpleNamespace(_resume_binding_snapshot=lambda _: None)
    session = task.book.session_dir(task.agent, "thread")
    row = ThreadSummary(thread_id="thread", workspace_id="book", session_name="测试", workspace_path=str(session),
                        created_at="now", updated_at="now", user_id="owner")
    env = SimpleNamespace(task=task, chapter=shared / "正文" / "第001章.md", replies=list(replies), offered=[],
                          failing=False, holding=False, session=session, frames=[])

    async def model_reply(body, model, billing, emit):
        names = sorted(tool["function"]["name"] for tool in body.get("tools", []))
        if names:
            env.offered.append(names)
        if env.failing:
            raise RuntimeError("模型服务通信失败")
        if not env.replies:
            pytest.fail(f"模型被调用的次数超出脚本，已绑定的工具={env.offered[-1:]}")
        reply = env.replies.pop(0)
        if reply == "FAIL":
            env.failing = True
            raise RuntimeError("模型服务通信失败")
        if reply == "HOLD":  # 模型一直不回：留给作者点停止
            env.holding = True
            await asyncio.sleep(3600)
        message = {"role": "assistant", "content": reply if isinstance(reply, str) else ""}
        if not isinstance(reply, str):
            call_id, name, arguments = reply
            message["tool_calls"] = [{"index": 0, "id": call_id, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]
        finish = "stop" if isinstance(reply, str) else "tool_calls"
        value = {"id": "chatcmpl-test", "created": 1, "model": "test-model",
                 "choices": [{"index": 0, "delta" if body.get("stream") else "message": message, "finish_reason": finish}]}
        if body.get("stream"):
            await emit(b"data: " + host._json(value) + b"\n\ndata: [DONE]\n\n")
        else:
            await emit(host._json(value))
    monkeypatch.setattr(creative_worker_model, "forward_model", model_reply)

    async def turn(payload):
        gen = host.run_private_instruction(task, row, payload, "owner", binding_service=binder)
        async with asyncio.timeout(360):
            async for frame in gen:
                env.frames.append(frame_data(frame))
        return env.frames

    env.turn = turn
    yield env


def _by_paragraph_replies():
    return [
        ("c1", "write_info", {"text": "小舟靠岸。"}),
        ("c2", "write_setup", {"text": "「船家，渡我。」"}),
        ("c3", "write_transition", {"text": "三日后，河开了。"}),
        ("head", "write_chapter_header", {"status": "初稿", "title": "渡口"}),
        ("review", "task", {"subagent_type": "review", "description": "审第1章"}),
        "通过，无修改意见。",
        "第1章已完成。",
    ]


def _by_batch_replies():
    return [
        ("first", "save_artifact", {"kind": "chapter_text", "chapter": 1, "unit_kind": "ordinary", "mode": "full",
                                    "content": "# 第001章 · 渡口\n> 状态：起草中｜2026-11-25\n\n小舟靠岸。"}),
        ("more", "save_artifact", {"kind": "chapter_text", "chapter": 1, "unit_kind": "ordinary", "mode": "append",
                                   "content": "船家收了钱。"}),
        ("done", "save_artifact", {"kind": "chapter_text", "chapter": 1, "mode": "edit",
                                   "anchor": "> 状态：起草中", "replacement": "> 状态：初稿"}),
        ("review", "task", {"subagent_type": "review", "description": "审第1章"}),
        "通过，无修改意见。",
        "第1章已完成。",
    ]


def _started_trace(env):
    return next(message["trace_id"] for event, message in env.frames
                if event == "status" and message.get("status") == "started")


def test_linux_text_worker_writes_a_chapter_paragraph_by_paragraph(tmp_path, monkeypatch):
    """带写作工具的包逐段起草，转初稿时整章一次写入，再实际审稿。"""
    from datetime import date

    from app.schemas.screenplay import ScreenplayGenerateRequest

    request = ScreenplayGenerateRequest(thread_id="thread", message_id="first", prompt="写第1章")
    with _linux_text_worker(tmp_path, monkeypatch, replies=_by_paragraph_replies()) as env:
        frames = asyncio.run(env.turn(request))
        assert any(event == "final" for event, _ in frames)
        assert env.chapter.read_text(encoding="utf-8") == (
            f"# 第001章 · 渡口\n> 状态：初稿｜{date.today().isoformat()}\n\n"
            "小舟靠岸。\n\n「船家，渡我。」\n\n三日后，河开了。\n")
        assert {"write_info", "write_chapter_header"} <= set(env.offered[0])
        assert env.task.draft_marker() == "c3"


def test_linux_old_package_still_drafts_in_batches(tmp_path, monkeypatch):
    """不带写作工具的已发布旧包，在同一版执行端上照旧整批保存并交稿。"""
    from datetime import date

    from app.schemas.screenplay import ScreenplayGenerateRequest

    request = ScreenplayGenerateRequest(thread_id="thread", message_id="first", prompt="写第1章")
    with _linux_text_worker(tmp_path, monkeypatch, by_paragraph=False, replies=_by_batch_replies()) as env:
        assert any(event == "final" for event, _ in asyncio.run(env.turn(request)))
        text = env.chapter.read_text(encoding="utf-8")
        # 旧流程照旧：整批保存，文件头保留它自己的进度行。
        assert text.startswith("# 第001章 · 渡口\n> 状态：初稿｜2026-11-25\n")
        assert text.endswith("\n\n小舟靠岸。\n\n船家收了钱。\n")
        assert all("write_causal" not in names for names in env.offered)


def test_linux_text_worker_keeps_paragraphs_written_before_a_service_failure(tmp_path, monkeypatch):
    """模型服务中途出错，已写的段落在收尾时补进文件（起草中），续写后每段只出现一次。"""
    from datetime import date

    from app.domains.writing.creative_scope import flush_unfinished_draft
    from app.schemas.screenplay import ScreenplayGenerateRequest

    replies = _by_paragraph_replies()
    request = ScreenplayGenerateRequest(thread_id="thread", message_id="first", prompt="写第1章")
    with _linux_text_worker(tmp_path, monkeypatch, replies=[*replies[:2], "FAIL", *replies[2:]]) as env:
        with pytest.raises(RuntimeError):
            asyncio.run(env.turn(request))
        assert not env.chapter.exists()
        # 路由的收尾顺序：流结束时任务还在运行就标中断，然后补存。
        env.task.mark("running", _started_trace(env))
        env.task.mark("interrupted")
        asyncio.run(flush_unfinished_draft(env.task, env.session))
        assert env.chapter.read_text(encoding="utf-8") == (
            f"# 第001章\n> 状态：起草中｜{date.today().isoformat()}\n\n小舟靠岸。\n\n「船家，渡我。」\n")
        assert env.task.draft_marker() == "c2"
        env.task.begin(resume=True)
        env.failing = False
        resumed = asyncio.run(env.turn(request.model_copy(update={"message_id": "second"})))
        assert any(event == "final" for event, _ in resumed)
        text = env.chapter.read_text(encoding="utf-8")
        assert text == (f"# 第001章 · 渡口\n> 状态：初稿｜{date.today().isoformat()}\n\n"
                        "小舟靠岸。\n\n「船家，渡我。」\n\n三日后，河开了。\n")
        assert (text.count("小舟靠岸。"), text.count("「船家，渡我。」")) == (1, 1)


def test_linux_text_worker_keeps_paragraphs_when_the_author_stops(tmp_path, monkeypatch):
    """作者主动停止（子进程收到终止信号），停止前刚写出的段落在收尾时补进文件。"""
    from datetime import date

    from app.domains.writing.creative_scope import flush_unfinished_draft
    from app.platform.credits.runtime import OperationStopped
    from app.schemas.screenplay import ScreenplayGenerateRequest

    request = ScreenplayGenerateRequest(thread_id="thread", message_id="first", prompt="写第1章")
    with _linux_text_worker(tmp_path, monkeypatch, replies=[*_by_paragraph_replies()[:2], "HOLD"]) as env:
        async def scenario():
            consuming = asyncio.create_task(env.turn(request))
            async with asyncio.timeout(120):
                while not env.holding:
                    await asyncio.sleep(0.02)
            # 路由的顺序：先标停止，再终止子进程；两段刚写完，运行记录还在缓冲里。
            env.task.mark("running", _started_trace(env))
            env.task.mark("stopped")
            assert host.cancel_private_instruction(env.task.id)
            with pytest.raises(OperationStopped):
                await consuming

        asyncio.run(scenario())
        asyncio.run(flush_unfinished_draft(env.task, env.session))
        assert env.chapter.read_text(encoding="utf-8") == (
            f"# 第001章\n> 状态：起草中｜{date.today().isoformat()}\n\n小舟靠岸。\n\n「船家，渡我。」\n")
        assert env.task.draft_marker() == "c2"
