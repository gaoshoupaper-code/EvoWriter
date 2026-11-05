"""REQ-20261105-222916: target boundaries, isolation and durable identities."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.target_tasks import TargetSpec, TargetTaskRequest, TaskInput
from app.domains.writing.target_tasks import (
    TargetTaskStore, completion_checks, materialize_input, validate_input,
)


def _request(task_id="target-one", **changes):
    values = {
        "task_id": task_id, "attempt_id": "attempt-one", "source_commit": "a" * 40,
        "target": {"task_type": "volume_outlines", "volume_ids": [1, 2]},
        "input": {"demand_md": "完整需求；全书两卷。", "confirmation_rules": "依需求自行选择，确认通过。"},
    }
    values.update(changes)
    return TargetTaskRequest.model_validate(values)


def _write(root: Path, name: str, text: str):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_missing_fourth_volume_is_rejected_before_generation():
    target = TargetSpec(task_type="chapter_outlines", volume_ids=[1, 2, 3, 4])
    data = TaskInput(confirmation_rules="确认通过", files={
        "主题.md": "已定主题", "总纲.md": "已定总纲", "文风.md": "已定文风",
        **{f"卷纲/第{n:02d}卷.md": f"本卷章号范围：{n}-{n}" for n in [1, 2, 3]},
    })
    with pytest.raises(ValueError, match="第04卷"):
        validate_input(target, data)


def test_nonfirst_chapter_requires_previous_text():
    target = TargetSpec(task_type="chapter_text", chapter=2)
    data = TaskInput(confirmation_rules="确认通过", files={
        "主题.md": "主题", "总纲.md": "总纲", "文风.md": "文风",
        "卷纲/第01卷.md": "本卷章号范围：1-2", "章纲/第002章.md": "第二章卡片",
    })
    with pytest.raises(ValueError, match="第001章"):
        validate_input(target, data)


def test_materialized_copies_do_not_change_initial_sample(tmp_path):
    data = TaskInput(confirmation_rules="确认通过", files={"主题.md": "原始主题"})
    first, second = tmp_path / "first", tmp_path / "second"
    materialize_input(first, data)
    materialize_input(second, data)
    (first / "主题.md").write_text("本次回写", encoding="utf-8")
    assert data.files["主题.md"] == "原始主题"
    assert (second / "主题.md").read_text(encoding="utf-8") == "原始主题"


def test_materialize_rejects_unknown_business_path(tmp_path):
    data = TaskInput(confirmation_rules="确认通过", files={"arbitrary/module.md": "payload"})
    with pytest.raises(ValueError, match="产物"):
        materialize_input(tmp_path, data)


def test_all_volume_completion_requires_every_final_volume(tmp_path):
    target = _request().target
    _write(tmp_path, "卷纲/第01卷.md", "# 卷纲\n> 状态：定稿\n本卷章号范围：1-2\n内容")
    _write(tmp_path, "卷纲/第02卷.md", "# 卷纲\n> 状态：起草中\n本卷章号范围：3-4\n内容")
    assert not completion_checks(target, tmp_path)["complete"]
    _write(tmp_path, "卷纲/第02卷.md", "# 卷纲\n> 状态：定稿\n本卷章号范围：3-4\n内容")
    assert completion_checks(target, tmp_path)["complete"]


def test_four_volume_completion_uses_adjusted_ranges_and_rejects_holes(tmp_path):
    target = TargetSpec(task_type="chapter_outlines", volume_ids=[1, 2, 3, 4])
    for n in target.volume_ids:
        _write(tmp_path, f"卷纲/第{n:02d}卷.md", f"# 卷纲\n> 状态：定稿\n本卷章号范围：{n}-{n}\n内容")
        _write(tmp_path, f"章纲/第{n:03d}章.md", "# 章纲\n> 状态：定稿\n章节概括：推进\n矛盾驱动：阻力\n主角状态：转变\n供给点：兑现\n章末钩子：悬念\n开头方案：承接\n修法承接：无\n伏笔操作：无")
    assert completion_checks(target, tmp_path)["complete"]
    _write(tmp_path, "卷纲/第04卷.md", "# 卷纲\n> 状态：定稿\n本卷章号范围：4-5\n内容")
    checks = completion_checks(target, tmp_path)
    assert not checks["complete"]
    assert any("005" in str(item) for item in checks["missing"])


def test_text_does_not_complete_when_review_failed(tmp_path):
    _write(tmp_path, "正文/第001章.md", "# 第001章\n> 状态：初稿\n完整正文")
    target = TargetSpec(task_type="chapter_text", chapter=1)
    assert not completion_checks(target, tmp_path, text_evidence={"review": "failed"})["complete"]
    assert not completion_checks(target, tmp_path, text_evidence={"review": "success", "needs_revision": True, "revised": False})["complete"]
    assert completion_checks(target, tmp_path, text_evidence={"review": "success", "needs_revision": False})["complete"]


def test_durable_submission_is_idempotent_and_completed_work_is_reused(tmp_path):
    store = TargetTaskStore(tmp_path)
    request = _request()
    result, created = store.create(request)
    assert created and result.status == "pending"
    duplicate, created = TargetTaskStore(tmp_path).create(request)
    assert not created and duplicate.task_id == request.task_id
    store.update(request.task_id, status="completed", completed=True)
    duplicate, created = TargetTaskStore(tmp_path).create(request)
    assert not created and duplicate.status == "completed"


def test_stable_task_id_cannot_be_reused_for_different_input(tmp_path):
    store = TargetTaskStore(tmp_path)
    store.create(_request())
    changed = _request(input={"demand_md": "另一份需求", "confirmation_rules": "确认通过"})
    with pytest.raises(ValueError, match="不同|冲突"):
        store.create(changed)


def test_stopped_tasks_are_not_recovery_candidates(tmp_path):
    store = TargetTaskStore(tmp_path)
    store.create(_request("stopped-one"))
    store.update("stopped-one", status="stopped")
    store.create(_request("running-one"))
    store.update("running-one", status="running")
    restored = TargetTaskStore(tmp_path)
    assert [row.task_id for row in restored.unfinished()] == ["running-one"]


def test_unknown_usage_does_not_turn_into_zero(tmp_path):
    store = TargetTaskStore(tmp_path)
    result, _ = store.create(_request())
    assert result.usage.get("input_tokens") is None
    assert result.usage.get("output_tokens") is None


def test_normal_human_final_text_requires_explicit_redo():
    target = TargetSpec(task_type="chapter_text", chapter=1)
    data = TaskInput(confirmation_rules="确认通过", files={
        "主题.md": "主题", "总纲.md": "总纲", "文风.md": "文风",
        "卷纲/第01卷.md": "本卷章号范围：1-1", "章纲/第001章.md": "章纲",
        "正文/第001章.md": "# 第001章\n> 状态：人修定稿\n作者已经精修的正文",
    })
    with pytest.raises(ValueError, match="删除重做|人修定稿"):
        validate_input(target, data)
