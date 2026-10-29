"""审查恢复后，真实 generate_stream 把最新创作事实交给 Command。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Command

from app.domains.writing.agent import MetaAgentService
from app.domains.writing.conversation_log import ConversationLog
from app.platform.streaming import StreamResult
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ScreenplayGenerateRequest, ThreadSummary
from contracts.writing_artifacts import load_writing_state, save_writing_state


def _resume(tmp_path: Path, monkeypatch, stage: str, answer, *, legacy=False):
    monkeypatch.setattr("app.platform.trace.recorder._notify_evolution", lambda *args, **kwargs: None)
    thread = ThreadSummary(
        thread_id="resume-facts", workspace_id="ws-facts", session_name="resume-facts",
        workspace_path=str(tmp_path), created_at="2026-10-29T00:00:00Z", updated_at="2026-10-29T00:00:00Z",
    )
    (tmp_path / "总纲.md").write_text(
        "# 总纲\n> 状态：定稿\n## 核心纲\n| 卷 | 内容 |\n|---|---|\n| 1 | 开局 |\n| 2 | 后续 |\n",
        encoding="utf-8",
    )
    for directory in ("卷纲", "章纲", "正文"):
        (tmp_path / directory).mkdir()
    (tmp_path / "卷纲/第01卷.md").write_text(
        "# 第一卷\n> 状态：定稿\n本卷章号范围：1-2章\n"
        "## 发展阶段\n开局\n## 爽点节奏表\n胜利\n## 爽点质检表\n通过\n## 卷末钩子\n悬念\n",
        encoding="utf-8",
    )
    for chapter in (1, 2):
        for directory in ("章纲", "正文"):
            (tmp_path / directory / f"第{chapter:03d}章.md").write_text("内容", encoding="utf-8")
    if legacy:
        (tmp_path / "storyline.md").write_text("旧版工作区", encoding="utf-8")
    recorder = TraceRecorder()
    handle = recorder.create_run(thread, "screenplay.generate.stream")
    recorder.await_input_run(thread, handle.trace_id)
    state = load_writing_state(tmp_path)
    state.confirmed_stage = stage
    state.stage = stage
    state.stage_modes[stage] = "auto"
    state.text_batch_size = 7
    key = "legacy" if legacy else "author"
    state.active_threads[key] = f"{thread.thread_id}:author:1"
    state.active_traces[key] = handle.trace_id
    save_writing_state(tmp_path, state)
    service = MetaAgentService(
        SimpleNamespace(writer_agent_mode="live"), tmp_path, recorder, None, None,
    )
    monkeypatch.setattr(service, "_resolve_checkpointer", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_gate_resume", lambda trace_id: None)
    monkeypatch.setattr(service, "_resume_binding_snapshot", lambda trace_id: None)
    monkeypatch.setattr(service, "_resolve_model", lambda owner_id: object())
    monkeypatch.setattr(service, "_agent_for_workspace", lambda *args, **kwargs: object())
    monkeypatch.setattr(service, "_enforce_history_budget", AsyncMock(return_value=0))
    captured = []

    async def capture_stream(agent, agent_input, config, **kwargs):
        captured.append(agent_input)
        tasks = [extra.task for extra in kwargs["extra_tasks"]]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        async def frames():
            if False:
                yield ""
        return frames(), StreamResult(pending_interrupt={
            "kind": "review", "review_kind": "stage", "question": "下个产物待审",
        })

    monkeypatch.setattr("app.domains.writing.agent.run_agent_stream", capture_stream)
    payload = ScreenplayGenerateRequest(
        thread_id=thread.thread_id, trace_id=handle.trace_id, resume=answer,
        content=answer if isinstance(answer, str) else "结构化回答",
    )

    async def drive():
        return [frame async for frame in service.generate_stream(payload, thread, owner_id="tester")]

    frames = asyncio.run(drive())
    assert any(frame.startswith("event: interrupt") for frame in frames)
    assert len(captured) == 1 and isinstance(captured[0], Command)
    messages = ConversationLog(tmp_path).read(thread.thread_id)
    assert [message.content for message in messages if message.role == "user"] == [payload.content]
    assert payload.resume == answer
    return captured[0].resume


@pytest.mark.parametrize("stage, expected", [
    ("volume", "当前目标：第 2 卷卷纲。"),
    ("chapter", "细纲库存 0 章。"),
    ("text", "正文批次大小：7 章。"),
])
def test_resume_command_receives_latest_stage_facts(tmp_path, monkeypatch, stage, expected):
    resume = _resume(tmp_path, monkeypatch, stage, "确认通过")
    assert resume.startswith("确认通过\n\n【系统更新：当前运行状态】")
    assert expected in resume
    assert f"（{stage}）" in resume
    assert "本段当前为自动模式。" in resume


def test_legacy_resume_keeps_original_answer(tmp_path, monkeypatch):
    assert _resume(tmp_path, monkeypatch, "volume", "确认通过", legacy=True) == "确认通过"


def test_structured_resume_keeps_original_value(tmp_path, monkeypatch):
    answer = {"answer": "确认通过"}
    assert _resume(tmp_path, monkeypatch, "volume", answer) == answer
