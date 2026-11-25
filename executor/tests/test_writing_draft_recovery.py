"""没写完就结束时不丢句子：作者对话路径（REQ-20261125-154544 FR-005、FR-007、DEC-012；AC-003、AC-004）。

正文按句写作后，句子先不进文件，文件头工具转「初稿」时才整章写入。
任务中途停止或中断时，执行端从本次运行的记录（Trace）里取回写作工具已写出的句子，
补进本章并保持「起草中」。记录由项目自己的接口读取，不依赖框架检查点的内部格式。
"""
from __future__ import annotations

import asyncio
import importlib
import socket
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.writing import creative_scope
from app.domains.writing import creative_worker as host
from app.domains.writing.creative_scope import ACTIVE, Book, CALL_ID, flush_unfinished_draft
from app.domains.writing.creative_worker_child import _Instruction, _message
from app.platform.agent.loader import load_package
from app.platform.trace.recorder import TraceRecorder
from contracts.creative_harness import build_agent_package
from contracts.sentence_draft import DRAFT, DraftState, pending_sentences

_SEED = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
_WRITTEN = [("c1", "她推门进来。"), ("c2", "「谁？」"), ("c3", "热气漫上纸页。")]
_TODAY = date.today().isoformat()


def _written(*ids):
    return [item for item in _WRITTEN if item[0] in ids]


def _text(*ids):
    return "\n\n".join(text for call_id, text in _written(*ids)) + "\n"


@pytest.fixture
def book(tmp_path):
    for rel, body in {"章纲/第001章.md": "靠岸", "文风.md": "短句"}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    value = Book(tmp_path)
    value.register_thread("text", "thread")
    return value


def _task(book, msg="write-1"):
    task = book.start("text", "thread", msg, {"prompt": "创作"}, 1, 1, "a" * 40)
    task.begin()
    return task


def _chapter(book):
    return (book.root / "正文" / "第001章.md").read_text(encoding="utf-8")


def _trace(session, calls, *, thread_id="thread"):
    """在会话目录里留下一份运行记录：写作工具逐个写出了这些句子，章节还没转初稿。

    calls 每项是 (调用编号, 句子, 回执)；回执以「[拒绝]」开头的算没写出。
    """
    from app.schemas.screenplay import ThreadSummary

    row = ThreadSummary(thread_id=thread_id, workspace_id="book", session_name="测试", workspace_path=str(session),
                        created_at="now", updated_at="now", user_id="owner")
    recorder = TraceRecorder()
    trace_id = recorder.create_run(row, "screenplay.generate.stream").trace_id
    for call_id, text, receipt in calls:
        recorder.append_event(trace_id, {"type": "tool_start", "status": "running", "source": "middleware",
                                         "tool_call_id": call_id, "tool_name": "write_action", "tool_args": {"text": text}})
        recorder.append_event(trace_id, {"type": "tool_end", "status": "completed", "source": "middleware",
                                         "tool_call_id": call_id, "tool_name": "write_action",
                                         "tool_args": {"text": text}, "tool_output": receipt})
    asyncio.run(recorder.aclose())
    return trace_id


def _ran(task, session, calls):
    """任务跑到一半：运行记录里已有这些句子，任务随后被标为中断。"""
    task.mark("running", _trace(session, calls))
    return flush_unfinished_draft(task, session)


# ── 标记：已写进文件到哪一句 ────────────────────────────────────────────


def test_a_whole_chapter_save_records_where_the_file_stops(book):
    task = _task(book)
    content = "# 第001章 · 夜探\n> 状态：初稿｜2026-11-20\n\n" + _text("c1", "c2")
    flush = {"kind": "chapter_text", "chapter": 1, "mode": "full", "content": content,
             "draft_flushed_through": "c2", "draft_sentences": 2}
    assert task.draft_marker() is None
    assert "已落盘" in task.apply_worker_save("head", flush, content)
    assert task.draft_marker() == "c2"
    assert "已落盘" in task.apply_worker_save("head", flush, content)
    assert task.draft_marker() == "c2"
    assert "已落盘" in task.apply_worker_save("edit", {"kind": "chapter_text", "chapter": 1, "mode": "full",
                                                      "content": content + "\n后来补的一句。\n"}, content)
    assert task.draft_marker() == "c2"


# ── 补存：没写完就结束 ─────────────────────────────────────────────────


def test_stopping_mid_chapter_keeps_every_written_sentence_as_an_unfinished_draft(book):
    task = _task(book)
    task.mark("stopped")
    assert task.flush_draft(_written("c1", "c2", "c3")) == 3
    assert _chapter(book) == f"# 第001章\n> 状态：起草中｜{_TODAY}\n\n" + _text("c1", "c2", "c3")
    assert task.draft_marker() == "c3"
    assert task.flush_draft(_written("c1", "c2", "c3")) == 0
    assert _chapter(book).count("热气漫上纸页。") == 1


def test_flushing_twice_adds_only_sentences_written_after_the_first_flush(book):
    task = _task(book)
    assert task.flush_draft(_written("c1", "c2")) == 2
    assert task.flush_draft(_written("c1", "c2", "c3")) == 1
    assert _chapter(book) == f"# 第001章\n> 状态：起草中｜{_TODAY}\n\n" + _text("c1", "c2", "c3")


def test_flush_joins_a_half_chapter_written_in_batches_and_keeps_its_date(book):
    task = _task(book)
    old = "# 第001章\n> 状态：起草中｜2026-11-20\n\n旧批次的一段。\n"
    assert "已落盘" in task.apply_worker_save("old", {"kind": "chapter_text", "chapter": 1, "mode": "full",
                                                      "content": old}, old)
    assert task.flush_draft(_written("c1")) == 1
    assert _chapter(book) == f"# 第001章\n> 状态：起草中｜2026-11-20\n\n旧批次的一段。\n\n她推门进来。\n"


@pytest.mark.parametrize("count", [1, 120])
def test_flush_handles_a_single_sentence_and_a_long_chapter(book, count):
    task = _task(book)
    written = [(f"c{index}", f"第{index}句。") for index in range(count)]
    assert task.flush_draft(written) == count
    assert _chapter(book) == f"# 第001章\n> 状态：起草中｜{_TODAY}\n\n" + "".join(f"第{index}句。\n\n" for index in range(count))[:-1]
    assert task.flush_draft(written) == 0


def test_flush_writes_nothing_for_other_agents_or_without_pending_sentences(book):
    book.register_thread("outline", "plan")
    outline = book.start("outline", "plan", "plan", {}, 1, 1, "a" * 40)
    outline.begin()
    assert outline.flush_draft(_written("c1")) == 0
    assert not (book.root / "总纲.md").exists()
    assert _task(book).flush_draft([]) == 0
    assert not (book.root / "正文").exists()


def test_markers_are_kept_per_task_and_a_flush_needs_no_tool_call_id(book):
    first = _task(book, "first")
    book.register_thread("text", "second-thread")
    second = book.start("text", "second-thread", "second", {}, 1, 1, "a" * 40)
    second.begin()
    assert first.flush_draft(_written("c1")) == 1
    assert (first.draft_marker(), second.draft_marker()) == ("c1", None)
    with pytest.raises(ValueError, match="缺少工具调用编号"):
        first.save({"kind": "chapter_text", "chapter": 1, "mode": "edit", "anchor": "x", "replacement": "y"}, lambda: "x")


# ── 中断后恢复：从运行记录取回句子 ─────────────────────────────────────


def test_an_interrupted_task_gets_its_sentences_back_before_it_resumes(book, monkeypatch):
    task = _task(book)
    session = book.session_dir("text", "thread")
    trace_id = _trace(session, [(call_id, text, "第 1 句。") for call_id, text in _WRITTEN])
    task.mark("running", trace_id)
    monkeypatch.setattr(creative_scope, "PROCESS", "重启后的进程")
    resumed = book.task("text", task_id=task.id)
    assert resumed.info()["status"] == "interrupted"
    assert asyncio.run(flush_unfinished_draft(resumed, session)) == 3
    resumed.begin(resume=True)
    assert _chapter(book) == f"# 第001章\n> 状态：起草中｜{_TODAY}\n\n" + _text("c1", "c2", "c3")
    assert resumed.draft_marker() == "c3"


def test_a_resumed_run_does_not_write_what_it_already_got_back(book, monkeypatch):
    task = _task(book)
    session = book.session_dir("text", "thread")
    assert asyncio.run(_ran(task, session, [(call_id, text, "第 1 句。") for call_id, text in _written("c1", "c2")])) == 2
    monkeypatch.setattr(creative_scope, "PROCESS", "重启后的进程")
    resumed = book.task("text", task_id=task.id)
    resumed.begin(resume=True)
    resumed.mark("running", _trace(session, [(call_id, text, "第 1 句。") for call_id, text in _WRITTEN]))
    assert asyncio.run(flush_unfinished_draft(resumed, session)) == 1
    assert _chapter(book) == f"# 第001章\n> 状态：起草中｜{_TODAY}\n\n" + _text("c1", "c2", "c3")


def test_only_sentences_the_tools_actually_accepted_are_written_back(book):
    task = _task(book)
    session = book.session_dir("text", "thread")
    calls = [("c1", "她推门进来。", "第 1 句，本章累计约 6 字。"),
             ("c2", "  ", "[拒绝] 句子是空的，请写一个完整的句子。"),
             ("c3", "「谁？」", "第 2 句，本章累计约 10 字。")]
    assert asyncio.run(_ran(task, session, calls)) == 2
    assert _chapter(book) == f"# 第001章\n> 状态：起草中｜{_TODAY}\n\n" + _text("c1", "c2")
    assert task.draft_marker() == "c3"


def test_a_task_without_a_trace_or_with_a_lost_one_gives_nothing_back(book):
    task = _task(book)
    session = book.session_dir("text", "thread")
    assert asyncio.run(flush_unfinished_draft(task, session)) == 0
    task.mark("running", "trace-never-recorded")
    assert asyncio.run(flush_unfinished_draft(task, session)) == 0
    assert not (book.root / "正文").exists()


# ── 受限子进程里的整章写入：经父进程落盘并留下标记 ─────────────────────


@pytest.fixture
def channels():
    child, parent = socket.socketpair()
    with child, parent:
        yield child, parent


def _child_instruction(task, package, work, channel):
    return _Instruction({"rev": "a" * 40, "agent": "text", "volume": 1, "chapter": 1, "work": str(work),
                         "id": task.id, "recovering": False, "package": str(package)}, channel)


def test_child_finalizes_the_chapter_through_the_parent_and_leaves_a_marker(tmp_path, monkeypatch, book, channels):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "private"))
    task = _task(book)
    package = tmp_path / "package"
    build_agent_package(_SEED, package, "text")
    load_package(package, "draft_recovery_package")
    child, parent = channels
    instruction = _child_instruction(task, package, tmp_path / "isolated", child)
    instruction.view.mkdir(parents=True)
    with parent.makefile("rb") as requests:
        def serve():
            for _ in range(1):
                reply = host._accept_change(task, _message(requests))
                parent.sendall(host._packet(host._json(reply)))
        call_token = CALL_ID.set("head")
        responder = threading.Thread(target=serve)
        responder.start()
        active_token = ACTIVE.set(instruction)
        draft_token = DRAFT.set(DraftState())
        try:
            abc = SimpleNamespace(ctx=SimpleNamespace(trace_recorder=None, trace_id=None, styles={"author": "起草规程"}),
                                  workspace_path=instruction.view, pkg_dir=package, trace_name="text-subagent")
            header = importlib.import_module("draft_recovery_package.tools.write_chapter_header").build(abc)
            runtime = SimpleNamespace(state={"messages": _messages("c1", "c2")}, tool_call_id="head")
            content, meta = header.func(status="初稿", title="夜探", runtime=runtime)
            assert (content, meta["writer_text"]["status"]) == ("文件头已改为「初稿」，写入本章 2 句。", "初稿")
        finally:
            DRAFT.reset(draft_token)
            ACTIVE.reset(active_token)
            responder.join(timeout=5)
            CALL_ID.reset(call_token)
    assert not responder.is_alive()
    assert _chapter(book) == f"# 第001章 · 夜探\n> 状态：初稿｜{_TODAY}\n\n" + _text("c1", "c2")
    assert task.draft_marker() == "c2"


def _messages(*ids):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages = [HumanMessage(content="写第1章", id="h1")]
    for call_id, text in _written(*ids):
        messages += [AIMessage(content="", tool_calls=[{"name": "write_action", "args": {"text": text}, "id": call_id}]),
                     ToolMessage(content="第 1 句。", tool_call_id=call_id)]
    return messages


def test_child_cannot_draft_in_batches_once_the_package_writes_by_sentence(tmp_path, monkeypatch, book, channels):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "private"))
    task = _task(book)
    package = tmp_path / "package"
    build_agent_package(_SEED, package, "text")
    child, _parent = channels
    instruction = _child_instruction(task, package, tmp_path / "isolated", child)
    args = {"kind": "chapter_text", "chapter": 1, "mode": "append", "content": "一批正文", "unit_kind": "ordinary"}
    assert instruction.save(args, lambda: pytest.fail("被拒的保存不应执行")).startswith("[拒绝] 起草请用写作工具逐句写")
    assert not (book.root / "正文").exists()
