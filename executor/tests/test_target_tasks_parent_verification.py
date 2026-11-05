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
