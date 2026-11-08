"""Readable trace identity uses structural snapshots, never request content."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


@pytest.fixture
def thread(tmp_path):
    return ThreadSummary(thread_id="thread-1", workspace_id="book-1", user_id="author-1",
                         session_name="正文对话", workspace_path=str(tmp_path / "book"),
                         created_at="now", updated_at="now")


def test_display_context_is_persisted_before_first_run_event(thread):
    recorder = TraceRecorder()
    display = {"workspace_title": "雾城", "task_phase": "text", "operation": "generation"}
    append = recorder.append_event

    def observe_start(trace_id, event):
        if event["type"] == "run_start":
            assert recorder.find_run_by_trace_id(trace_id).run_snapshot["display_context"] == display
        return append(trace_id, event)

    with patch.object(recorder, "append_event", side_effect=observe_start):
        handle = recorder.create_run(thread, "screenplay.generate.stream", display_context=display)
    assert handle.trace_id.startswith("trace-")


def test_runtime_snapshot_is_immediately_readable_and_survives_restart(thread):
    recorder = TraceRecorder()
    display = {"workspace_title": "旧作品名", "task_phase": "text"}
    handle = recorder.create_run(thread, "screenplay.generate.stream", display_context=display)
    recorder.set_run_snapshot(handle.trace_id, {"model_name": "writer"})
    assert recorder.find_run_by_trace_id(handle.trace_id).run_snapshot["display_context"] == display
    assert recorder.find_run_by_trace_id(handle.trace_id).run_snapshot["model_name"] == "writer"
    restarted = TraceRecorder()
    run = restarted.find_run_by_trace_id(handle.trace_id, workspace_path=thread.workspace_path)
    assert run.run_snapshot["display_context"] == display
    recorder.complete_run(thread, handle.trace_id)
    run = restarted.find_run_by_trace_id(handle.trace_id)
    assert run.run_snapshot["model_name"] == "writer"


def test_snapshot_update_does_not_drop_a_concurrent_run_in_the_same_workspace(thread):
    recorder = TraceRecorder()
    first = recorder.create_run(thread, "screenplay.generate.stream")
    read_started, allow_write = Event(), Event()
    read_index = recorder._read_run_index

    def pause_after_read(current):
        rows = read_index(current)
        if current_thread().name.startswith("snapshot-update"):
            read_started.set()
            assert allow_write.wait(timeout=5)
        return rows

    with patch.object(recorder, "_read_run_index", side_effect=pause_after_read):
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-update") as pool:
            update = pool.submit(recorder.set_run_snapshot, first.trace_id, {"model_name": "writer"})
            while not read_started.wait(timeout=0.01) and not update.done():
                pass
            try:
                second = recorder.create_run(thread.model_copy(update={"thread_id": "thread-2"}),
                                             "screenplay.generate.stream")
            finally:
                allow_write.set()
            update.result(timeout=5)
    assert recorder.find_run_by_trace_id(second.trace_id) is not None
    assert recorder.find_run_by_trace_id(first.trace_id).run_snapshot["model_name"] == "writer"


def test_snapshot_update_does_not_fail_generation_when_index_writes_are_unavailable(thread):
    recorder = TraceRecorder()
    handle = recorder.create_run(thread, "screenplay.generate.stream")
    with patch.object(recorder, "_write_index", side_effect=OSError("temporary disk error")):
        recorder.set_run_snapshot(handle.trace_id, {"model_name": "writer"})
    assert recorder.find_run_by_trace_id(handle.trace_id).run_snapshot["model_name"] == "writer"


def test_active_rows_expose_only_display_context_not_runtime_snapshot(thread):
    recorder = TraceRecorder()
    display = {"workspace_title": "雾城", "task_phase": "chapter"}
    handle = recorder.create_run(thread, "screenplay.generate.stream", display_context=display)
    recorder.set_run_snapshot(handle.trace_id, {"binding": {"private": "hidden"}})
    active = recorder.list_active_runs()[0]
    assert active["display_context"] == display
    assert "run_snapshot" not in active
    assert "private" not in json.dumps(active)

    external = TraceRecorder()
    assert external.register_external_run(handle.trace_id, thread.workspace_path)
    assert external.list_active_runs()[0]["display_context"] == display


def test_resume_keeps_original_display_identity_after_session_rename(thread):
    recorder = TraceRecorder()
    display = {"workspace_title": "旧作品名", "task_phase": "text"}
    handle = recorder.create_run(thread, "screenplay.generate.stream", display_context=display)
    recorder.await_input_run(thread, handle.trace_id)
    renamed = thread.model_copy(update={"session_name": "新会话名"})
    restarted = TraceRecorder()
    resumed, is_new = restarted.resume_run(
        renamed, handle.trace_id, display_context={"workspace_title": "新作品名", "task_phase": "chapter"},
    )
    assert not is_new
    assert resumed.trace_id == handle.trace_id
    run = restarted.find_run_by_trace_id(handle.trace_id)
    assert run.session_name == "正文对话"
    assert run.run_snapshot["display_context"] == display


def test_terminal_snapshot_keeps_display_when_runtime_identity_is_added(thread):
    recorder = TraceRecorder()
    display = {"workspace_title": "旧作品名", "task_phase": "text"}
    handle = recorder.create_run(thread, "screenplay.generate.stream", display_context=display)
    recorder.set_run_snapshot(handle.trace_id, {
        "model_name": "writer", "display_context": {"workspace_title": "改名后的作品"},
    })
    assert recorder.find_run_by_trace_id(handle.trace_id).run_snapshot["display_context"] == display
    recorder.complete_run(thread, handle.trace_id)
    assert recorder.find_run_by_trace_id(handle.trace_id).run_snapshot["display_context"] == display
    assert recorder.list_active_runs() == []


def test_unknown_resume_creates_new_run_with_available_display_context(thread):
    recorder = TraceRecorder()
    display = {"workspace_title": "雾城", "task_phase": "outline"}
    handle, is_new = recorder.resume_run(thread, "trace-not-found", display_context=display)
    assert is_new
    assert handle.trace_id != "trace-not-found"
    assert recorder.find_run_by_trace_id(handle.trace_id).run_snapshot["display_context"] == display


def test_legacy_active_run_without_context_remains_readable(thread):
    recorder = TraceRecorder()
    handle = recorder.create_run(thread, "screenplay.generate.stream")
    assert recorder.list_active_runs()[0]["display_context"] == {}
    recorder.set_run_snapshot(handle.trace_id, {"harness_version": "v1"})
    assert recorder.find_run_by_trace_id(handle.trace_id).run_snapshot == {"harness_version": "v1"}


@pytest.fixture
def workspace_db(monkeypatch):
    from app.platform.core import db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE workspaces (workspace_id TEXT, owner_id TEXT, title TEXT);"
                       "CREATE TABLE threads (workspace_id TEXT);"
                       "INSERT INTO workspaces VALUES ('book-1', 'author-1', '雾城');")
    monkeypatch.setattr(db, "get_database", lambda: SimpleNamespace(conn=conn))
    yield conn
    conn.close()


def test_capture_uses_starting_title_and_does_not_guess_chapter(thread, workspace_db):
    from app.domains.writing.trace_display import capture_display_context

    first = capture_display_context(thread, task_phase="text")
    workspace_db.execute("UPDATE workspaces SET title = '新作品名'")
    second = capture_display_context(thread, task_phase="text")
    assert first == {"workspace_title": "雾城", "task_phase": "text"}
    assert second["workspace_title"] == "新作品名"
    assert "target" not in first


def test_capture_keeps_only_explicit_target_scope(thread, workspace_db):
    from app.domains.writing.trace_display import capture_display_context
    from contracts.target_tasks import TargetSpec

    execution = SimpleNamespace(request=SimpleNamespace(
        target=TargetSpec(task_type="chapter_outlines", volume_ids=[2], chapter=25),
        input=SimpleNamespace(demand_md="正文不得读取")), stage="chapter", volume=2, chapter=25)
    display = capture_display_context(thread, task_phase="chapter", execution=execution)
    assert display["task_type"] == "chapter_outlines"
    assert display["target"] == {"task_type": "chapter_outlines", "volume_ids": [2], "chapter": 25}
    assert "正文不得读取" not in json.dumps(display, ensure_ascii=False)


def test_missing_or_other_owner_workspace_does_not_fail_capture(thread, workspace_db):
    from app.domains.writing.trace_display import capture_display_context

    assert capture_display_context(thread, task_phase="theme", owner_id="another-author") == {
        "task_phase": "theme"}
    with patch("app.platform.core.db.get_database", side_effect=RuntimeError("database unavailable")):
        assert capture_display_context(thread, task_phase="volume") == {
            "task_phase": "volume"}


@pytest.mark.parametrize("automatic,repair", [(False, False), (True, False), (False, True)])
def test_generate_stream_start_has_business_snapshot_without_request_body(
    thread, workspace_db, monkeypatch, automatic, repair,
):
    from app.domains.writing.agent import MetaAgentService
    from app.domains.writing.pipeline import RouteDecision
    from app.domains.writing.target_execution import FrozenExecution
    from app.schemas.screenplay import ScreenplayGenerateRequest
    from contracts.target_tasks import TargetTaskRequest

    recorder = TraceRecorder()
    service = MetaAgentService(SimpleNamespace(writer_agent_mode="live"),
                               Path(thread.workspace_path), recorder, None, None)
    monkeypatch.setattr(service, "_generation_block_reason", lambda *args, **kwargs: None)

    def stop_before_model(*args):
        raise RuntimeError("stop before model assembly")

    monkeypatch.setattr(service, "_issue_run_binding", stop_before_model)
    monkeypatch.setattr("app.domains.writing.pipeline.route", lambda *args, **kwargs: RouteDecision(
        agent_name="author", thread_id=thread.thread_id, directive="", phase="text"))
    execution = None
    if automatic:
        request = TargetTaskRequest(task_id="explicit-chapter", attempt_id="first", source_commit="a" * 40,
                                    target={"task_type": "chapter_text", "chapter": 3},
                                    input={"confirmation_rules": "确认", "demand_md": "PRIVATE_CONTENT"})
        execution = FrozenExecution(request, object(), Path(thread.workspace_path), object(),
                                    Path(thread.workspace_path), stage="text", chapter=3)
    started = []

    async def drive():
        with execution if execution is not None else nullcontext():
            payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="PRIVATE_CONTENT",
                                                character_repair=repair)
            async for frame in service.generate_stream(payload, thread, owner_id=thread.user_id):
                if frame.startswith("event: status\ndata: "):
                    status = json.loads(frame.split("data: ", 1)[1])
                    if status.get("status") == "started":
                        started.append(recorder.find_run_by_trace_id(status["trace_id"]).run_snapshot["display_context"])

    with pytest.raises(RuntimeError, match="stop before model assembly"):
        asyncio.run(drive())
    assert len(started) == 1
    assert started[0]["workspace_title"] == "雾城"
    assert started[0]["task_phase"] == "text"
    assert "PRIVATE_CONTENT" not in json.dumps(started, ensure_ascii=False)
    assert "operation" not in started[0]
    if automatic:
        assert started[0]["target"]["chapter"] == 3
        assert started[0]["task_type"] == "chapter_text"
    else:
        assert "target" not in started[0]
    if repair:
        assert started[0]["task_type"] == "character.repair"
