"""REQ-20261102-135406: account settings and character repair reach real assembly."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.writing.agent import MetaAgentService
from app.platform.core.db import (
    Database, UserRepository, WorkspaceRepository, WritingSkillRepository, init_database,
)
from app.platform.core.security import generate_master_key, load_master_key
from app.schemas.screenplay import ScreenplayGenerateRequest
from contracts.writing_artifacts import WritingState, save_writing_state


@pytest.fixture()
def account(tmp_path):
    db = Database(tmp_path / "skills.db", load_master_key(generate_master_key()))
    init_database(db)
    owner = UserRepository(db).create(username="generation", password="pw123456")["user_id"]
    books = [WorkspaceRepository(db).create(owner_id=owner, title=name) for name in ("甲", "乙")]
    workspace = tmp_path / "book"
    workspace.mkdir()
    save_writing_state(workspace, WritingState(stage="text", confirmed_stage="text"))
    return db, owner, books, workspace


@pytest.fixture()
def service():
    result = MetaAgentService.__new__(MetaAgentService)
    result.settings = SimpleNamespace(writer_agent_mode="live")
    return result


def _pending_text(account):
    db, owner, books, _ = account
    repo = WritingSkillRepository(db)
    ids = [repo.create(owner_id=owner, name=f"技{n}", description="正文", content=f"指令{n}")["skill_id"] for n in range(6)]
    repo.set_enabled(books[0]["workspace_id"], owner, ids[:5])
    repo.set_enabled(books[1]["workspace_id"], owner, ids[5:])
    return repo, ids


def test_same_account_uses_global_skills_in_another_book(service, account):
    db, owner, books, workspace = account
    repo = WritingSkillRepository(db)
    skill = repo.create(owner_id=owner, name="全局", description="共享", content="所有作品遵守相同要求")
    repo.set_enabled(books[0]["workspace_id"], owner, [skill["skill_id"]])
    prompt = service._compose_user_writing_skills(books[1]["workspace_id"], owner, workspace)
    assert prompt is not None and "所有作品遵守相同要求" in prompt


def test_pending_stage_blocks_only_its_new_generations(service, account):
    _, owner, _, workspace = account
    _pending_text(account)
    reason = service._generation_block_reason(workspace, owner_id=owner)
    assert reason and "技能" in reason and "选择" in reason
    save_writing_state(workspace, WritingState(stage="outline", confirmed_stage="outline"))
    assert service._generation_block_reason(workspace, owner_id=owner) is None


def test_protagonist_missing_blocks_writing_but_not_repair(service, account):
    _, owner, _, workspace = account
    (workspace / ".writing" / "character_mode").write_text("enabled\n", encoding="utf-8")
    reason = service._generation_block_reason(workspace, owner_id=owner)
    assert reason and "主角" in reason
    assert service._generation_block_reason(workspace, owner_id=owner, character_repair=True) is None


def test_repair_bypasses_skill_pending_without_resolving_it(service, account):
    _, owner, _, workspace = account
    repo, _ = _pending_text(account)
    assert service._generation_block_reason(workspace, owner_id=owner, character_repair=True) is None
    assert repo.get_global_state(owner)["pending_stages"] == ["text"]


def test_author_and_reviewer_receive_the_same_skill_snapshot(service, account, monkeypatch):
    _, owner, books, workspace = account
    reads = []

    def read(*args, **kwargs):
        reads.append(True)
        return [{"name": "冻结", "description": "一致", "content": f"本轮快照{len(reads)}", "stage": "text"}]

    monkeypatch.setattr(service, "_list_enabled_stage_skills", read)
    styles = service._apply_user_writing_skills(
        styles={"author": "段规程"}, agent_name="author", workspace_id=books[0]["workspace_id"],
        owner_id=owner, workspace_path=workspace,
    )
    assert len(reads) == 1
    assert "本轮快照1" in styles["author"] and "本轮快照1" in styles["review"]


def test_blocked_stream_does_not_start_runtime_or_change_stage(service, account, monkeypatch):
    _, owner, books, workspace = account
    _pending_text(account)
    state_path = workspace / ".writing" / "state.json"
    before = state_path.read_bytes()
    calls = []

    async def resolve(*args):
        calls.append(True)
        raise AssertionError("blocked request must not build runtime")

    monkeypatch.setattr(service, "_resolve_checkpointer", resolve)
    thread = SimpleNamespace(thread_id="blocked-account", workspace_id=books[0]["workspace_id"], workspace_path=str(workspace))
    payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="继续写")

    async def collect():
        return [frame async for frame in service.generate_stream(payload, thread, owner_id=owner)]

    with pytest.raises(ValueError, match="技能.*选择"):
        asyncio.run(collect())
    assert calls == []
    assert state_path.read_bytes() == before


def test_repair_assembly_uses_only_repair_prompt_and_explicit_context(service, account, monkeypatch):
    import app.domains.writing.agent as module
    import app.platform.agent.loader as loader

    _, owner, books, workspace = account
    captured = []
    package = SimpleNamespace(
        character_repair_prompt=lambda: "专用人物卡重做规程",
        author_base_neutral_prompt=lambda: "常驻骨架",
        guide_prompt=lambda mode: "逐步确认",
        assemble=lambda ctx, **kwargs: captured.append(ctx) or object(),
    )
    monkeypatch.setattr(loader, "load_current_package", lambda: package)
    monkeypatch.setattr(module, "get_memory_backend", lambda *args: None)
    monkeypatch.setattr(module, "_get_memory_recall_cls", lambda *args: None)
    monkeypatch.setattr(service, "_backend_for_workspace", lambda *args: object())
    monkeypatch.setattr(service, "_resolve_meta_style", lambda *args: None)
    monkeypatch.setattr(service, "_resolve_style_for_subagent", lambda *args: None)
    monkeypatch.setattr(service, "_list_enabled_stage_skills", lambda *args, **kwargs: pytest.fail("repair must not read business skills"))
    service.trace_recorder = SimpleNamespace()
    service._assemble_via_package(
        workspace, workspace_id=books[0]["workspace_id"], owner_id=owner,
        model=object(), checkpointer=object(), agent_name="author", character_repair=True,
    )
    assert captured[0].character_repair is True
    assert captured[0].prompt_overrides["author"] == "专用人物卡重做规程"
    assert not captured[0].styles


def test_repair_uses_separate_checkpoint_without_mutating_writing_progress(service, account):
    from app.domains.writing.agent import _character_repair_route

    _, _, _, workspace = account
    original = WritingState(stage="chapter", confirmed_stage="chapter")
    original.active_threads["author"] = "writing:author:1"
    original.active_traces["author"] = "writing-trace"
    save_writing_state(workspace, original)
    before = (workspace / ".writing" / "state.json").read_bytes()
    decision = _character_repair_route(workspace, "conversation", resume_trace=None)
    assert decision.thread_id == "conversation:character-repair"
    assert decision.agent_name == "author"
    assert decision.phase == "chapter"
    assert (workspace / ".writing" / "state.json").read_bytes() == before


@pytest.fixture()
def runtime(service, account, monkeypatch):
    from unittest.mock import AsyncMock
    from app.platform.trace.recorder import TraceRecorder
    from app.schemas.screenplay import ThreadSummary

    _, owner, books, workspace = account
    monkeypatch.setattr("app.platform.trace.recorder._notify_evolution", lambda *args, **kwargs: None)
    thread = ThreadSummary(
        thread_id="repair-stream", workspace_id=books[0]["workspace_id"], session_name="补卡",
        workspace_path=str(workspace), created_at="2026-11-02T06:00:00Z", updated_at="2026-11-02T06:00:00Z",
    )
    service.trace_recorder = TraceRecorder()
    monkeypatch.setattr(service, "_resolve_checkpointer", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_model", lambda *args: object())
    monkeypatch.setattr(service, "_issue_run_binding", lambda *args: (None, None))
    monkeypatch.setattr(service, "_gate_resume", lambda *args: None)
    monkeypatch.setattr(service, "_resume_binding_snapshot", lambda *args: None)
    monkeypatch.setattr(service, "_normalize_thread_tail", AsyncMock(return_value=0))
    monkeypatch.setattr(service, "_enforce_history_budget", AsyncMock(return_value=0))
    return service, owner, workspace, thread


def test_repair_stream_resumes_its_own_choice_without_auto_answer_or_stage_changes(runtime, account, monkeypatch):
    from langgraph.types import Command
    from app.platform.streaming import StreamResult
    from contracts.writing_artifacts import load_writing_state

    service, owner, workspace, thread = runtime
    _pending_text(account)
    normal = service.trace_recorder.create_run(thread, "normal-writing")
    service.trace_recorder.await_input_run(thread, normal.trace_id)
    state = load_writing_state(workspace)
    state.stage_modes["text"] = "auto"
    state.active_threads["author"] = "regular-writing-checkpoint"
    state.active_traces["author"] = normal.trace_id
    save_writing_state(workspace, state)
    (workspace / "正文").mkdir()
    manuscript = workspace / "正文/第001章.md"
    manuscript.write_text("已有正文保持原样", encoding="utf-8")
    builds, inputs = [], []
    monkeypatch.setattr(service, "_agent_for_workspace", lambda *args, **kwargs: builds.append(kwargs) or object())

    async def run(agent, agent_input, config, **kwargs):
        inputs.append((agent_input, config["configurable"]["thread_id"]))
        tasks = [extra.task for extra in kwargs["extra_tasks"]]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        async def frames():
            if False:
                yield ""

        if len(inputs) == 1:
            return frames(), StreamResult(pending_interrupt={"kind": "choice", "question": "主角卡请确认"})
        (workspace / "人物").mkdir()
        (workspace / "人物/主角.md").write_text("# 主角\n已确认的人物卡", encoding="utf-8")
        return frames(), StreamResult(full_result={"messages": [{"role": "assistant", "content": "人物卡已补齐"}]})

    monkeypatch.setattr("app.domains.writing.agent.run_agent_stream", run)

    async def collect(payload):
        return [frame async for frame in service.generate_stream(payload, thread, owner_id=owner)]

    first = asyncio.run(collect(ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="重做主角", character_repair=True)))
    assert any(frame.startswith("event: interrupt") for frame in first)
    assert not any(frame.startswith("event: auto_answer") for frame in first)
    after_choice = load_writing_state(workspace)
    assert after_choice.active_traces["author"] == normal.trace_id
    repair_trace = after_choice.active_traces["character-repair"]
    second = asyncio.run(collect(ScreenplayGenerateRequest(
        thread_id=thread.thread_id, prompt="确认", resume="确认", trace_id=repair_trace, character_repair=True,
    )))
    assert any(frame.startswith("event: final") for frame in second)
    assert isinstance(inputs[1][0], Command)
    assert [entry[1] for entry in inputs] == ["repair-stream:character-repair"] * 2
    assert all(build["character_repair"] for build in builds)
    final_state = load_writing_state(workspace)
    assert final_state.confirmed_stage == "text"
    assert final_state.active_traces["author"] == normal.trace_id
    assert "character-repair" not in final_state.active_traces
    assert manuscript.read_text(encoding="utf-8") == "已有正文保持原样"


def test_failed_repair_assembly_closes_trace_and_keeps_progress(runtime, monkeypatch):
    from app.domains.writing.conversation_log import ConversationLog
    from contracts.writing_artifacts import load_writing_state

    service, owner, workspace, thread = runtime

    def unsupported(*args, **kwargs):
        raise RuntimeError("harness 尚未支持人物卡重做")

    monkeypatch.setattr(service, "_agent_for_workspace", unsupported)

    async def collect():
        payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="重做主角", character_repair=True)
        return [frame async for frame in service.generate_stream(payload, thread, owner_id=owner)]

    with pytest.raises(RuntimeError, match="harness"):
        asyncio.run(collect())
    assert service.trace_recorder.list_active_runs() == []
    assert service.trace_recorder.list_runs(thread)[0].status == "failed"
    assert load_writing_state(workspace).confirmed_stage == "text"
    assert ConversationLog(workspace).read(thread.thread_id)[-1].status == "failed"


def test_protagonist_deletion_between_admission_and_trace_cannot_start_model(runtime, monkeypatch):
    from unittest.mock import Mock

    service, owner, workspace, thread = runtime
    (workspace / "人物").mkdir()
    protagonist = workspace / "人物/主角.md"
    protagonist.write_text("原卡", encoding="utf-8")

    def removed_after_trace(*args):
        (workspace / ".writing/character_mode").write_text("enabled", encoding="utf-8")
        protagonist.unlink()
        return None, None

    monkeypatch.setattr(service, "_issue_run_binding", removed_after_trace)
    build = Mock(side_effect=AssertionError("missing protagonist must not start model"))
    monkeypatch.setattr(service, "_agent_for_workspace", build)

    async def collect():
        payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="继续正文")
        return [frame async for frame in service.generate_stream(payload, thread, owner_id=owner)]

    with pytest.raises(ValueError, match="主角"):
        asyncio.run(collect())
    build.assert_not_called()
    assert service.trace_recorder.list_active_runs() == []


def test_conversation_restores_character_repair_for_both_message_roles(tmp_path):
    from app.domains.writing.conversation_log import ConversationLog

    log = ConversationLog(tmp_path)
    user_id = log.admit_user("conversation", "重做主角", character_repair=True)
    assistant_id = log.open_assistant("conversation", "trace", character_repair=True)
    log.close_assistant("conversation", assistant_id, status="awaiting", content="确认主角卡")
    restored = ConversationLog(tmp_path).read("conversation")
    assert [message.id for message in restored] == [user_id, assistant_id]
    assert all(message.character_repair for message in restored)
    assert all(message.model_dump()["character_repair"] for message in restored)


def test_replayed_message_cannot_change_character_repair_mode(tmp_path):
    from app.domains.writing.conversation_log import ConversationLog, ConversationLogError

    log = ConversationLog(tmp_path)
    message_id = log.admit_user("conversation", "重做", character_repair=True)
    assert log.admit_user("conversation", "重做", message_id=message_id, character_repair=True) == message_id
    with pytest.raises(ConversationLogError, match="模式"):
        log.admit_user("conversation", "重做", message_id=message_id, character_repair=False)
    assert log.read("conversation")[0].character_repair is True


def test_old_conversation_without_repair_field_remains_normal_writing(tmp_path):
    from app.domains.writing.conversation_log import ConversationLog

    log = ConversationLog(tmp_path)
    log._append("conversation", {"kind": "user", "id": "old", "content": "继续", "status": "completed"})
    assert log.read("conversation")[0].character_repair is False


def test_model_configuration_failure_does_not_leave_a_running_trace(runtime, monkeypatch):
    service, owner, _, thread = runtime

    def invalid_model(*args):
        raise RuntimeError("model configuration invalid")

    monkeypatch.setattr(service, "_resolve_model", invalid_model)

    async def collect():
        payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="重做主角", character_repair=True)
        return [frame async for frame in service.generate_stream(payload, thread, owner_id=owner)]

    with pytest.raises(RuntimeError, match="model configuration"):
        asyncio.run(collect())
    assert service.trace_recorder.list_active_runs() == []
    assert service.trace_recorder.list_runs(thread)[0].status == "failed"


def test_closing_stream_during_preparation_does_not_leave_a_running_trace(runtime):
    service, owner, _, thread = runtime

    async def close_after_started():
        payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="重做主角", character_repair=True)
        stream = service.generate_stream(payload, thread, owner_id=owner)
        first = await anext(stream)
        assert '"started"' in first
        await stream.aclose()

    asyncio.run(close_after_started())
    assert service.trace_recorder.list_active_runs() == []
