"""人物卡删除与生成准入必须共享同一作品临界区（AC-007/008）。"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domains.writing import agent as agent_module
from app.domains.writing import run_gate
from app.domains.writing.agent import MetaAgentService
from app.platform.streaming import StreamResult
from app.platform.trace.recorder import TraceRecorder
from app.routers import workspaces as workspace_router
from app.schemas.screenplay import (
    ArtifactRedoBatchDeleteRequest, ArtifactRedoDeleteRequest,
    ScreenplayGenerateRequest, ThreadSummary,
)
from contracts.writing_artifacts import WritingState, save_writing_state


def test_workspace_mutation_lock_uses_resolved_path(tmp_path: Path):
    lock = run_gate.get_workspace_mutation_lock(tmp_path)
    assert run_gate.get_workspace_mutation_lock(tmp_path / "nested" / "..") is lock
    assert run_gate.get_workspace_mutation_lock(tmp_path / "other") is not lock
    with lock:
        with run_gate.get_workspace_mutation_lock(tmp_path):
            pass


@pytest.mark.parametrize("batch", [False, True])
def test_accepted_character_deletion_finishes_before_generation_recheck(tmp_path, monkeypatch, batch):
    """删请求已过空闲检查时，生成复查必须等实际移卡完成。"""
    workspace = tmp_path / "book"
    workspace.mkdir()
    save_writing_state(workspace, WritingState(stage="text", confirmed_stage="text"))
    card = workspace / "人物" / "主角.md"
    card.parent.mkdir()
    card.write_text("# 原主角卡", encoding="utf-8")
    thread = ThreadSummary(
        thread_id="race-conversation", workspace_id="race-book", session_name="竞态",
        workspace_path=str(workspace), created_at="2026-11-02T06:00:00Z", updated_at="2026-11-02T06:00:00Z",
    )
    recorder = TraceRecorder()
    monkeypatch.setattr("app.platform.trace.recorder._notify_evolution", lambda *args, **kwargs: None)
    service = MetaAgentService.__new__(MetaAgentService)
    service.settings = SimpleNamespace(writer_agent_mode="live")
    service.trace_recorder = recorder
    monkeypatch.setattr(service, "_resolve_checkpointer", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_model", lambda *args: object())
    monkeypatch.setattr(service, "_issue_run_binding", lambda *args: (None, None))
    monkeypatch.setattr(service, "_normalize_thread_tail", AsyncMock(return_value=0))
    monkeypatch.setattr(service, "_enforce_history_budget", AsyncMock(return_value=0))
    monkeypatch.setattr(workspace_router, "get_trace_recorder", lambda: recorder)
    monkeypatch.setattr(workspace_router, "get_thread_store", lambda: SimpleNamespace(
        get_workspace=lambda *args: SimpleNamespace(workspace_path=str(workspace)),
    ))
    idle_checked, release_delete, generation_lock_requested = (threading.Event() for _ in range(3))
    assembled = threading.Event()
    results = {}
    original_check = workspace_router._assert_character_deletions_idle
    original_lock = run_gate.get_workspace_mutation_lock

    def pause_after_idle(*args):
        original_check(*args)
        idle_checked.set()
        assert release_delete.wait(5)

    def observe_generation_lock(path):
        generation_lock_requested.set()
        return original_lock(path)

    monkeypatch.setattr(workspace_router, "_assert_character_deletions_idle", pause_after_idle)
    monkeypatch.setattr(agent_module, "get_workspace_mutation_lock", observe_generation_lock, raising=False)

    def assemble(*args, **kwargs):
        assembled.set()
        return object()

    monkeypatch.setattr(service, "_agent_for_workspace", assemble)

    async def run(*args, **kwargs):
        tasks = [extra.task for extra in kwargs["extra_tasks"]]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        async def frames():
            if False:
                yield ""

        return frames(), StreamResult(full_result={"messages": [{"role": "assistant", "content": "不应启动"}]})

    monkeypatch.setattr(agent_module, "run_agent_stream", run)

    def delete():
        try:
            if batch:
                results["deleted"] = workspace_router.delete_artifacts_for_redo_batch(
                    thread.workspace_id, ArtifactRedoBatchDeleteRequest(rels=["人物/主角.md"]),
                    SimpleNamespace(user_id="owner"),
                ).deleted_count == 1
            else:
                results["deleted"] = workspace_router.delete_artifact_for_redo(
                    thread.workspace_id, ArtifactRedoDeleteRequest(rel="人物/主角.md"),
                    SimpleNamespace(user_id="owner"),
                ).status == "ok"
        except BaseException as exc:
            results["delete_error"] = exc

    def generate():
        async def collect():
            payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt="继续正文")
            return [frame async for frame in service.generate_stream(payload, thread)]

        try:
            results["frames"] = asyncio.run(collect())
        except BaseException as exc:
            results["generation_error"] = exc

    deleting = threading.Thread(target=delete)
    generating = threading.Thread(target=generate)
    deleting.start()
    try:
        assert idle_checked.wait(5)
        generating.start()
        assert generation_lock_requested.wait(5), "生成复查未使用作品共享锁"
        assert not assembled.is_set()
        assert card.exists()
        release_delete.set()
        deleting.join(5)
        generating.join(5)
        assert not deleting.is_alive() and not generating.is_alive()
        assert "delete_error" not in results
        assert results["deleted"]
        assert isinstance(results.get("generation_error"), ValueError)
        assert "主角" in str(results["generation_error"])
        assert not assembled.is_set()
        assert not card.exists()
        assert recorder.list_active_runs() == []
    finally:
        release_delete.set()
        deleting.join(5)
        if generating.ident is not None:
            generating.join(5)
