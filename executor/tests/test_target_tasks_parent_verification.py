"""Parent-side completion never trusts a candidate's status or chosen paths."""
from __future__ import annotations

from copy import deepcopy

import pytest

from contracts.target_tasks import TargetTaskRequest
from app.domains.writing.target_tasks import TargetTaskManager
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


def _create(tmp_path):
    manager = TargetTaskManager(tmp_path / "jobs")
    request = TargetTaskRequest(
        task_id="verify-parent", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "chapter_text", "chapter": 1},
        input={"confirmation_rules": "确认"},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager.store.create(request, metadata={"workspace": str(workspace), "owner_id": None})
    trusted = deepcopy(manager.store.record(request.task_id))
    return manager, request, workspace, trusted


def test_parent_rejects_completed_claim_without_actual_artifact(tmp_path):
    manager, request, workspace, trusted = _create(tmp_path)
    manager.store.update(request.task_id, status="completed", completed=True,
                         artifacts={"正文/第001章.md": "并未实际保存的伪产物"},
                         checks={"text_evidence": {"review": "success", "needs_revision": False}})
    result = manager.verify_result(request.task_id, trusted)
    assert result.status == "failed" and not result.completed
    assert "正文/第001章.md" not in result.artifacts


def test_parent_uses_original_workspace_when_candidate_changes_journal_path(tmp_path):
    manager, request, workspace, trusted = _create(tmp_path)
    other = tmp_path / "other-workspace"
    (other / "正文").mkdir(parents=True)
    (other / "正文/第001章.md").write_text("# 第001章\n> 状态：初稿\n别处的正文", encoding="utf-8")
    manager.store.journal(request.task_id, metadata={"workspace": str(other)})
    manager.store.update(request.task_id, status="completed", completed=True)
    result = manager.verify_result(request.task_id, trusted)
    assert result.status == "failed" and not result.completed
    assert "别处的正文" not in str(result.artifacts)


def _trace(manager, workspace):
    manager.recorder = TraceRecorder()
    thread = ThreadSummary(thread_id="verification", workspace_id="verification", session_name="verification",
                           workspace_path=str(workspace), created_at="now", updated_at="now")
    trace = manager.recorder.create_run(thread, "target-test")
    return manager.recorder, trace.trace_id


def test_parent_accepts_real_saved_text_review_and_revision_trace(tmp_path):
    manager, request, workspace, trusted = _create(tmp_path)
    recorder, trace_id = _trace(manager, workspace)
    original = "# 第001章\n> 状态：初稿\n原稿的矛盾没有解决。"
    revised = "# 第001章\n> 状态：初稿\n修订稿补上了冲突与行动。"
    recorder.record_artifact_revision(trace_id, "author", file_path="正文/第001章.md", content=original, tool_call_id="save-before")
    recorder.append_event(trace_id, {"type": "tool_start", "status": "running", "source": "middleware", "tool_name": "task",
                                   "tool_call_id": "review-1", "tool_args": {"subagent_type": "review", "description": "审稿第1章"}})
    recorder.append_event(trace_id, {"type": "tool_end", "status": "completed", "source": "middleware", "tool_name": "task",
                                   "tool_call_id": "review-1", "tool_output": {"content": "请补上冲突与行动。"}})
    recorder.record_artifact_revision(trace_id, "author", file_path="正文/第001章.md", content=revised, tool_call_id="save-after")
    (workspace / "正文").mkdir()
    (workspace / "正文/第001章.md").write_text(revised, encoding="utf-8")
    manager.store.update(request.task_id, status="completed", completed=True, trace_ids=[trace_id])
    result = manager.verify_result(request.task_id, trusted)
    assert result.status == "completed" and result.completed
    assert result.checks["text_evidence"]["revised"]
    assert result.artifacts["正文/第001章.md"] == revised


def test_parent_accepts_a_chapter_finalized_by_the_header_tool(tmp_path, monkeypatch):
    """REQ-20261125-154544：逐句起草的整章写入必须留下修订记录，父进程才认得出审前稿和审后修订。"""
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from app.domains.writing import target_execution
    from app.domains.writing.creative_scope import CALL_ID
    from app.platform.agent.loader import load_package
    from contracts.sentence_draft import DRAFT, DraftState

    seed = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
    load_package(seed)
    from harness_current.tools import save_artifact, write_chapter_header

    manager, request, workspace, trusted = _create(tmp_path)
    recorder, trace_id = _trace(manager, workspace)
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    execution = target_execution._execution.set(SimpleNamespace(request=request, chapter=1, volume=None))
    draft = DRAFT.set(DraftState())
    abc = SimpleNamespace(ctx=SimpleNamespace(trace_recorder=recorder, trace_id=trace_id, styles={"author": "起草规程"}),
                          workspace_path=workspace, pkg_dir=seed, trace_name="text-subagent")
    written = [HumanMessage(content="写第1章", id="h1")]
    for call_id, name, text in (("c1", "write_action", "小舟靠岸。"), ("c2", "write_dialogue", "「船家。」")):
        written += [AIMessage(content="", tool_calls=[{"name": name, "args": {"text": text}, "id": call_id}]),
                    ToolMessage(content="第 1 句。", tool_call_id=call_id)]
    try:
        content, _meta = write_chapter_header.build(abc).func(
            status="初稿", title="渡口", runtime=SimpleNamespace(state={"messages": written}, tool_call_id="head"))
        assert content == "文件头已改为「初稿」，写入本章 2 句。"
    finally:
        DRAFT.reset(draft)
        target_execution._execution.reset(execution)
    recorder.append_event(trace_id, {"type": "tool_start", "status": "running", "source": "middleware", "tool_name": "task",
                                   "tool_call_id": "review-1", "tool_args": {"subagent_type": "review", "description": "审稿第1章"}})
    recorder.append_event(trace_id, {"type": "tool_end", "status": "completed", "source": "middleware", "tool_name": "task",
                                   "tool_call_id": "review-1", "tool_output": {"content": "请补上船家的反应。"}})
    # 审后修订仍走保存工具；整章写入已登记为「已读」，不必先重读整章
    call = CALL_ID.set("revise-1")
    try:
        assert "已落盘" in save_artifact.build(abc).invoke({
            "kind": "chapter_text", "chapter": 1, "mode": "edit",
            "anchor": "「船家。」", "replacement": "「船家。」老周把竹篙递了过来。"})
    finally:
        CALL_ID.reset(call)
    asyncio.run(recorder.aclose())
    manager.store.update(request.task_id, status="completed", completed=True, trace_ids=[trace_id])
    result = manager.verify_result(request.task_id, trusted)
    assert result.status == "completed" and result.completed, result.error
    assert result.checks["text_evidence"]["revised"] is True
    assert "老周把竹篙递了过来。" in result.artifacts["正文/第001章.md"]


def test_parent_replays_real_volume_qc_for_non_chapter_target(tmp_path):
    manager = TargetTaskManager(tmp_path / "jobs")
    request = TargetTaskRequest(task_id="verify-volume", attempt_id="first", source_commit="a" * 40,
                                target={"task_type": "volume_outlines", "volume_ids": [1]},
                                input={"demand_md": "完整故事需求", "confirmation_rules": "确认通过"})
    workspace = tmp_path / "workspace"
    (workspace / "卷纲").mkdir(parents=True)
    (workspace / "主题.md").write_text("> 状态：定稿\n" + "\n".join(
        f"## {heading}\n具体故事内容" for heading in ["一句话主题", "核心爽点", "核心矛盾", "核心爽点剧情"]), encoding="utf-8")
    (workspace / "总纲.md").write_text("> 状态：定稿\n## 核心纲\n| 卷 | 内容 |\n|---|---|\n| 1 | 明确故事内容 |", encoding="utf-8")
    (workspace / "卷纲/第01卷.md").write_text("> 状态：定稿\n本卷章号范围：1-1\n具体卷纲内容", encoding="utf-8")
    manager.store.create(request, metadata={"workspace": str(workspace), "owner_id": None})
    trusted = manager.store.trusted(request.task_id)
    recorder, trace_id = _trace(manager, workspace)
    recorder.append_event(trace_id, {"type": "tool_start", "status": "running", "source": "middleware", "tool_name": "task",
                                   "tool_call_id": "qc-1", "tool_args": {"subagent_type": "volume_qc", "description": "质检第1卷"}})
    recorder.append_event(trace_id, {"type": "tool_end", "status": "completed", "source": "middleware", "tool_name": "task",
                                   "tool_call_id": "qc-1", "tool_output": {"content": "本卷质检通过。"}})
    recorder.flush_sync(trace_id)
    manager.store.update(request.task_id, status="completed", completed=True, trace_ids=[trace_id])
    result = manager.verify_result(request.task_id, trusted)
    assert result.status == "completed" and result.completed, result.error


def test_unverified_child_completion_is_hidden_across_manager_restart(tmp_path):
    manager, request, workspace, trusted = _create(tmp_path)
    manager.store.journal(request.task_id, metadata={"workspace": "/forged/workspace"})
    manager.store.update(request.task_id, status="completed", completed=True)
    restored = TargetTaskManager(manager.store.root)
    assert restored.result(request.task_id).status == "recovering"
    assert not restored.result(request.task_id).completed
    assert restored.store.trusted(request.task_id)["metadata"]["workspace"] == str(workspace)


def test_parent_cannot_borrow_trace_registered_to_another_workspace(tmp_path):
    manager, request, workspace, trusted = _create(tmp_path)
    recorder, baseline_trace = _trace(manager, tmp_path / "baseline-workspace")
    with pytest.raises(ValueError, match="trace|Trace|工作区"):
        manager._trace_evidence(request, workspace, [baseline_trace])
