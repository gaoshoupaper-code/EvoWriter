"""补卡与普通生成在模型装配前失败，也必须收尾已创建的 trace。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domains.writing.agent import MetaAgentService
from app.domains.writing.conversation_log import ConversationLog
from app.domains.writing.run_gate import get_conversation_gate
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ScreenplayGenerateRequest, ThreadSummary
from contracts.writing_artifacts import WritingState, save_writing_state


@pytest.fixture()
def preparation(tmp_path, monkeypatch):
    workspace = tmp_path / "book"
    workspace.mkdir()
    save_writing_state(workspace, WritingState(stage="text", confirmed_stage="text"))
    thread = ThreadSummary(
        thread_id="preparation", workspace_id="preparation-book", session_name="准备期",
        workspace_path=str(workspace), created_at="2026-11-02T06:00:00Z", updated_at="2026-11-02T06:00:00Z",
    )
    monkeypatch.setattr("app.platform.trace.recorder._notify_evolution", lambda *args, **kwargs: None)
    service = MetaAgentService.__new__(MetaAgentService)
    service.settings = SimpleNamespace(writer_agent_mode="live")
    service.trace_recorder = TraceRecorder()
    monkeypatch.setattr(service, "_resolve_checkpointer", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_issue_run_binding", lambda *args: (None, None))
    return service, workspace, thread


@pytest.mark.parametrize("repair", [False, True])
def test_model_creation_failure_finalizes_trace_and_assistant(preparation, monkeypatch, repair):
    service, workspace, thread = preparation

    def fail_model(*args):
        raise ValueError("writer_model configuration invalid")

    monkeypatch.setattr(service, "_resolve_model", fail_model)

    async def collect():
        try:
            payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="重做人物", character_repair=repair)
            return [frame async for frame in service.generate_stream(payload, thread)]
        finally:
            assert not get_conversation_gate(thread.thread_id)._lock.locked()

    with pytest.raises(ValueError, match="configuration invalid"):
        asyncio.run(collect())
    assert service.trace_recorder.list_active_runs() == []
    assert service.trace_recorder.list_runs(thread)[0].status == "failed"
    messages = ConversationLog(workspace).read(thread.thread_id)
    assert messages[-1].role == "assistant" and messages[-1].status == "failed"


@pytest.mark.parametrize("exit_kind, expected", [("disconnect", "interrupted"), ("stop", "cancelled"), ("close", "interrupted")])
def test_exit_after_started_frame_finalizes_preparation_trace(preparation, exit_kind, expected):
    service, _, thread = preparation

    async def collect():
        stream = service.generate_stream(ScreenplayGenerateRequest(
            thread_id=thread.thread_id, prompt="重做人物", character_repair=True,
        ), thread)
        frame = await anext(stream)
        assert frame.startswith("event: status\ndata: ")
        payload = json.loads(frame.split("\n")[1][len("data: "):])
        assert payload["status"] == "started"
        if exit_kind == "close":
            await stream.aclose()
        else:
            if exit_kind == "stop":
                service.trace_recorder.request_user_stop(payload["trace_id"])
            with pytest.raises(asyncio.CancelledError):
                await stream.athrow(asyncio.CancelledError())
        assert not get_conversation_gate(thread.thread_id)._lock.locked()

    asyncio.run(collect())
    assert service.trace_recorder.list_active_runs() == []
    assert service.trace_recorder.list_runs(thread)[0].status == expected
