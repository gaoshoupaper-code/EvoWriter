"""卷纲质量写入：有限修复、草稿隔离与正式稿凭据。"""
from __future__ import annotations

import hashlib
import json
import threading

import pytest

from app.domains.writing.volume_payoff_guard import VolumePayoffGuard, bind_volume_operation
from contracts.writing_artifacts import (
    next_stage_on_confirm,
    scan_writing_progress,
    volume_draft_relpath,
    volume_payoff_receipt_valid,
    volume_review_block_reason,
)


POLICY = "policy-v1"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def volume_text(payload="合规安排", status="定稿"):
    return (
        f"# 第01卷\n> 状态：{status}｜2026-10-29\n\n"
        "## 发展阶段\n阶段一\n\n"
        f"## 爽点节奏表\n{payload}\n\n"
        "## 爽点质检表\n逐项检查\n\n"
        "## 卷末钩子\n后续事件\n"
    )


def verdict(text, passed=None, *, policy=POLICY):
    good = "违规" not in text if passed is None else passed
    return {
        "passed": good,
        "candidate_hash": digest(text),
        "policy_hash": policy,
        "violations": [] if good else [{"code": "unknown_entry", "message": "清单外条目"}],
    }


def guard(workspace, *, operation="task-1", check=None, repair=None, **kwargs):
    return VolumePayoffGuard(
        workspace, operation_id=operation, policy_hash=POLICY,
        check=check or verdict,
        repair=repair or (lambda text, errors: text.replace("违规", "合规")),
        **kwargs,
    )


def write_original(workspace, text):
    path = workspace / "卷纲/第01卷.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def journal(workspace):
    return json.loads((workspace / ".writing/volume-payoff-checks.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", ["full", "edit", "rewrite"])
def test_each_source_repairs_then_rechecks_before_commit(tmp_path, source):
    calls = []
    service = guard(tmp_path, check=lambda text: calls.append(text) or verdict(text))
    candidate = volume_text("违规安排")
    result = service.commit(1, "", candidate, source)
    saved = (tmp_path / "卷纲/第01卷.md").read_text(encoding="utf-8")
    assert result["status"] == "committed"
    assert result["repair_attempts"] == 1
    assert result["content"] == saved == volume_text()
    assert calls == [candidate, saved]
    assert volume_payoff_receipt_valid(tmp_path, 1, saved)
    assert scan_writing_progress(tmp_path).volumes == [1]


def test_two_repairs_exhaust_then_resume_cannot_refresh_budget(tmp_path):
    repaired = []
    checked = []

    def repair(text, errors):
        repaired.append(text)
        return text + f"\n违规修订{len(repaired)}"

    def check(text):
        checked.append(text)
        return verdict(text)

    bad = volume_text("违规安排")
    result = guard(tmp_path, check=check, repair=repair).commit(1, "", bad, "full")
    assert result["status"] == "blocked"
    assert result["repair_attempts"] == 2
    assert len(repaired) == 2 and len(checked) == 3
    assert "起草中" in result["content"] and "清单外条目" in result["content"]
    assert (tmp_path / result["path"]).is_file()
    resumed = guard(tmp_path, check=check, repair=repair).commit(1, "", bad + "新候选", "rewrite")
    assert resumed["status"] == "blocked"
    assert resumed["repair_attempts"] == 2
    assert len(repaired) == 2 and len(checked) == 3
    assert scan_writing_progress(tmp_path).volumes == []


def test_failed_revision_preserves_accepted_file_and_receipt(tmp_path):
    good = volume_text()
    assert guard(tmp_path).commit(1, "", good, "full")["status"] == "committed"
    accepted = journal(tmp_path)["volumes"]["1"]["accepted_receipt"]
    old_bytes = (tmp_path / "卷纲/第01卷.md").read_bytes()
    result = guard(tmp_path, operation="task-2", repair=lambda text, errors: text).commit(
        1, good, volume_text("违规安排"), "edit",
    )
    assert result["status"] == "blocked"
    assert result["path"] == volume_draft_relpath(1)
    assert (tmp_path / "卷纲/第01卷.md").read_bytes() == old_bytes
    assert journal(tmp_path)["volumes"]["1"]["accepted_receipt"] == accepted
    assert volume_payoff_receipt_valid(tmp_path, 1, good)
    assert volume_review_block_reason(tmp_path, volume=1)
    assert volume_review_block_reason(tmp_path, volume=2) is None


def test_legacy_file_stays_eligible_until_actually_changed(tmp_path):
    original = volume_text("旧版安排")
    path = write_original(tmp_path, original)
    assert scan_writing_progress(tmp_path).volumes == [1]
    assert volume_payoff_receipt_valid(tmp_path, 1, original)
    assert not (tmp_path / ".writing/volume-payoff-checks.json").exists()
    guard(tmp_path).commit(1, original, volume_text(), "edit")
    path.write_text(volume_text("绕过守卫的内容"), encoding="utf-8")
    assert scan_writing_progress(tmp_path).volumes == []


def test_checker_exception_keeps_previous_bytes_and_draft(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)

    def check(text):
        raise TimeoutError("checker timed out")

    result = guard(tmp_path, check=check).commit(1, original, volume_text(), "full")
    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == original
    assert "TimeoutError" in result["reason"]
    assert "起草中" in result["content"]


@pytest.mark.parametrize("wrong_field", ["candidate_hash", "policy_hash", "passed"])
def test_unbound_or_malformed_pass_is_not_trusted(tmp_path, wrong_field):
    def check(text):
        result = verdict(text, True)
        result[wrong_field] = "forged"
        return result

    result = guard(tmp_path, check=check).commit(1, "", volume_text(), "full")
    assert result["status"] == "blocked"
    assert scan_writing_progress(tmp_path).volumes == []


def test_incomplete_draft_can_be_saved_without_full_check(tmp_path):
    def check(text):
        raise AssertionError("尚未形成节奏表，不应执行整卷检查")

    text = "# 第01卷\n> 状态：起草中｜2026-10-29\n\n## 发展阶段\n阶段待扩展\n"
    result = guard(tmp_path, check=check).commit(1, "", text, "full")
    assert result["status"] == "draft"
    assert scan_writing_progress(tmp_path).volumes == []
    assert "起草中" in result["content"]


def test_draft_edit_uses_draft_base_but_promotes_against_original(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    failed = guard(tmp_path, repair=lambda text, errors: text).commit(
        1, original, volume_text("违规安排"), "full",
    )
    draft_base = (tmp_path / failed["path"]).read_text(encoding="utf-8")
    good = volume_text()
    promoted = guard(tmp_path, operation="task-2").commit(1, draft_base, good, "edit", draft=True)
    assert promoted["status"] == "committed"
    assert path.read_text(encoding="utf-8") == good
    assert volume_review_block_reason(tmp_path, volume=1) is None


def test_draft_edit_rejects_stale_draft_base(tmp_path):
    original = volume_text("原稿")
    write_original(tmp_path, original)
    first = guard(tmp_path, repair=lambda text, errors: text).commit(
        1, original, volume_text("违规安排"), "full",
    )
    stale = first["content"]
    (tmp_path / first["path"]).write_text(stale + "作者编辑", encoding="utf-8")
    result = guard(tmp_path, operation="task-2").commit(1, stale, volume_text(), "edit", draft=True)
    assert result["status"] == "blocked"
    assert (tmp_path / "卷纲/第01卷.md").read_text(encoding="utf-8") == original


def test_original_changes_during_check_cannot_be_overwritten(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    newer = volume_text("作者刚修改的内容")

    def check(text):
        path.write_text(newer, encoding="utf-8")
        return verdict(text)

    result = guard(tmp_path, check=check).commit(1, original, volume_text(), "edit")
    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == newer
    assert "变化" in result["reason"]


def test_annotation_repair_cannot_change_prefix_or_suffix(tmp_path):
    original = volume_text("违规安排")
    write_original(tmp_path, original)
    prefix, suffix = original.split("违规安排")

    def repair(text, errors):
        return text.replace("违规安排", "合规安排").replace("后续事件", "越界改写")

    result = guard(tmp_path, repair=repair).commit(
        1, original, original, "edit", edit_scope={"prefix": prefix, "suffix": suffix},
    )
    assert result["status"] == "blocked"
    assert "越界改写" not in result["content"]
    assert (tmp_path / "卷纲/第01卷.md").read_text(encoding="utf-8") == original


def test_caller_cannot_change_operation_id(tmp_path):
    result = guard(tmp_path).commit(1, "", volume_text(), "full", operation_id="tool-new-id")
    assert result["status"] == "blocked"
    assert not (tmp_path / "卷纲/第01卷.md").exists()


def test_replaying_success_does_not_recheck_or_repair(tmp_path):
    calls = []
    candidate = volume_text("违规安排")
    first = guard(tmp_path, check=lambda text: calls.append(text) or verdict(text)).commit(
        1, "", candidate, "full",
    )
    replayed = guard(tmp_path, check=lambda text: calls.append(text) or verdict(text)).commit(
        1, "", candidate, "full",
    )
    assert replayed == first
    assert len(calls) == 2


def test_same_operation_cannot_change_rules_to_refresh_budget(tmp_path):
    service = guard(tmp_path, repair=lambda text, errors: text)
    service.commit(1, "", volume_text("违规安排"), "full")
    replacement = VolumePayoffGuard(
        tmp_path, operation_id="task-1", policy_hash="policy-v2",
        check=lambda text: verdict(text, policy="policy-v2"),
        repair=lambda text, errors: text,
    )
    result = replacement.commit(1, "", volume_text(), "full")
    assert result["status"] == "blocked"
    assert result["repair_attempts"] == 2
    assert scan_writing_progress(tmp_path).volumes == []


def test_interrupted_repair_counts_attempt_before_restart(tmp_path):
    calls = []

    def interrupted(text, errors):
        calls.append(text)
        raise SystemExit("模拟进程中断")

    bad = volume_text("违规安排")
    with pytest.raises(SystemExit):
        guard(tmp_path, repair=interrupted).commit(1, "", bad, "full")
    repaired = []

    def repair(text, errors):
        repaired.append(text)
        return text + "\n仍违规"

    result = guard(tmp_path, repair=repair).commit(1, "", bad, "edit")
    assert result["status"] == "blocked"
    assert result["repair_attempts"] == 2
    assert len(calls) == 1 and len(repaired) == 1


def test_unrelated_skill_uses_atomic_write_without_semantic_receipt(tmp_path):
    def check(text):
        raise AssertionError("非爽点定义不应调用复核")

    result = guard(tmp_path, policy_status="not_applicable", check=check).commit(
        1, "", volume_text("普通技能安排"), "full",
    )
    assert result["status"] == "committed"
    assert scan_writing_progress(tmp_path).volumes == [1]
    if (tmp_path / ".writing/volume-payoff-checks.json").exists():
        assert journal(tmp_path)["volumes"]["1"].get("accepted_receipt") is None


@pytest.mark.parametrize("policy_status", ["error", "ambiguous"])
def test_unavailable_definition_cannot_fall_back_to_unconstrained(tmp_path, policy_status):
    result = guard(tmp_path, policy_status=policy_status).commit(1, "", volume_text(), "full")
    assert result["status"] == "blocked"
    assert scan_writing_progress(tmp_path).volumes == []
    assert "起草中" in result["content"]


def test_latest_draft_cannot_use_previous_final_volume_to_advance(tmp_path):
    write_original(tmp_path, volume_text("旧卷"))
    second = tmp_path / "卷纲/第02卷.md"
    second.write_text(volume_text("新卷尚未确认", status="起草中"), encoding="utf-8")
    assert volume_review_block_reason(tmp_path)
    assert next_stage_on_confirm(tmp_path, "volume") == "volume"


def test_unrelated_old_failed_revision_does_not_block_latest_review(tmp_path):
    original = volume_text("旧卷")
    write_original(tmp_path, original)
    guard(tmp_path, repair=lambda text, errors: text).commit(1, original, volume_text("违规"), "edit")
    second = tmp_path / "卷纲/第02卷.md"
    second.write_text(volume_text("新卷"), encoding="utf-8")
    assert volume_review_block_reason(tmp_path, volume=1)
    assert volume_review_block_reason(tmp_path) is None
    assert next_stage_on_confirm(tmp_path, "volume") == "chapter"


def test_receipt_write_failure_restores_original_and_keeps_candidate_draft(tmp_path, monkeypatch):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    service = guard(tmp_path)
    save_journal = service._write_journal
    failed = []

    def fail_receipt_once(data):
        operation = data["volumes"]["1"]["active_operation"]
        if operation["status"] == "passed" and not failed:
            failed.append(True)
            raise OSError("模拟凭据落盘中断")
        save_journal(data)

    monkeypatch.setattr(service, "_write_journal", fail_receipt_once)
    result = service.commit(1, original, volume_text(), "edit")
    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == original
    assert result["path"] == volume_draft_relpath(1)


def test_incomplete_draft_does_not_replace_existing_final(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    result = guard(tmp_path).commit(
        1, original, "# 第01卷\n> 状态：起草中\n\n## 发展阶段\n待补\n", "full",
    )
    assert result["status"] == "draft"
    assert result["path"] == volume_draft_relpath(1)
    assert path.read_text(encoding="utf-8") == original


def test_parallel_calls_cannot_share_and_overwrite_same_repair_budget(tmp_path):
    checking = threading.Event()
    release = threading.Event()
    completed = []
    checks = []

    def check(text):
        checks.append(text)
        checking.set()
        assert release.wait(5)
        return verdict(text)

    first = guard(tmp_path, check=check)
    thread = threading.Thread(target=lambda: completed.append(first.commit(1, "", volume_text(), "full")))
    thread.start()
    assert checking.wait(5)
    try:
        second = guard(tmp_path, check=check).commit(1, "", volume_text(), "full")
    finally:
        release.set()
        thread.join(5)
    assert second["status"] == "blocked"
    assert "进行" in second["reason"]
    assert completed[0]["status"] == "committed"
    assert len(checks) == 1


def test_failed_candidate_with_unusual_status_is_still_marked_draft(tmp_path):
    candidate = volume_text("违规安排").replace("状态：定稿", "状态 定稿")
    result = guard(tmp_path, repair=lambda text, errors: text).commit(1, "", candidate, "full")
    assert result["status"] == "blocked"
    assert "> 状态：起草中" in result["content"]
    assert "状态 定稿" not in result["content"]


@pytest.mark.parametrize("policy_status", ["ready", "not_applicable"])
def test_complete_but_explicit_draft_does_not_replace_accepted_final(tmp_path, policy_status):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    result = guard(tmp_path, policy_status=policy_status).commit(
        1, original, volume_text("合规的新安排", status="起草中"), "full",
    )
    assert result["status"] == "draft"
    assert result["path"] == volume_draft_relpath(1)
    assert path.read_text(encoding="utf-8") == original
    assert journal(tmp_path)["volumes"]["1"]["accepted_receipt"] is None


def test_stopping_during_check_retains_draft_and_cannot_commit_pass(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    alive = [True]
    repairs = []

    def check(text):
        alive[0] = False
        return verdict(text)

    result = guard(
        tmp_path, check=check, is_active=lambda: alive[0],
        repair=lambda text, errors: repairs.append(text) or text,
    ).commit(1, original, volume_text(), "edit")
    assert result["status"] == "blocked"
    assert "停止" in result["reason"]
    assert path.read_text(encoding="utf-8") == original
    assert "> 状态：起草中" in result["content"]
    assert repairs == []


def test_stopping_during_repair_does_not_recheck_or_commit(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    alive = [True]
    checks = []

    def repair(text, errors):
        alive[0] = False
        return text.replace("违规", "合规")

    result = guard(
        tmp_path, check=lambda text: checks.append(text) or verdict(text),
        repair=repair, is_active=lambda: alive[0],
    ).commit(1, original, volume_text("违规安排"), "full")
    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == original
    assert len(checks) == 1


def test_inactive_unconstrained_write_cannot_overwrite_final(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    result = guard(tmp_path, policy_status="not_applicable", is_active=lambda: False).commit(
        1, original, volume_text(), "full",
    )
    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == original


def test_stop_at_commit_boundary_retains_previous_accepted_bytes(tmp_path, monkeypatch):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    alive = [True]
    service = guard(tmp_path, is_active=lambda: alive[0])
    write_journal = service._write_journal

    def stop_after_pending_record(data):
        write_journal(data)
        if data["volumes"]["1"]["active_operation"].get("reason") == "待提交":
            alive[0] = False

    monkeypatch.setattr(service, "_write_journal", stop_after_pending_record)
    result = service.commit(1, original, volume_text(), "edit")
    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == original


def test_operation_binding_only_explicit_new_revision_gets_new_budget_id(tmp_path):
    trace = "server-trace"
    assert bind_volume_operation(tmp_path, trace) == trace
    assert bind_volume_operation(tmp_path, trace, "ordinary-resume") == trace
    first = bind_volume_operation(tmp_path, trace, "rejection-1", new_revision=True)
    assert first == trace + ":revision:" + digest("rejection-1")[:16]
    assert bind_volume_operation(tmp_path, trace) == first
    assert bind_volume_operation(tmp_path, trace, "rejection-1", new_revision=True) == first
    second = bind_volume_operation(tmp_path, trace, "rejection-2", new_revision=True)
    assert second != first
    assert bind_volume_operation(tmp_path, trace, "rejection-1", new_revision=True) == first
    assert bind_volume_operation(tmp_path, trace) == second


def test_explicit_author_revision_can_repair_after_previous_budget_exhaustion(tmp_path):
    original = volume_text("原稿")
    write_original(tmp_path, original)
    trace = "server-trace"
    old_operation = bind_volume_operation(tmp_path, trace)
    failed = guard(tmp_path, operation=old_operation, repair=lambda text, errors: text).commit(
        1, original, volume_text("违规安排"), "full",
    )
    assert failed["status"] == "blocked" and failed["repair_attempts"] == 2
    ordinary = bind_volume_operation(tmp_path, trace, "ordinary-resume")
    unchanged = guard(tmp_path, operation=ordinary).commit(1, original, volume_text("违规"), "full")
    assert unchanged["status"] == "blocked" and unchanged["repair_attempts"] == 2
    revised = bind_volume_operation(tmp_path, trace, "author-reject-message", new_revision=True)
    assert volume_review_block_reason(tmp_path, operation_id=trace)
    repaired = guard(tmp_path, operation=revised).commit(1, original, volume_text("违规安排"), "full")
    assert repaired["status"] == "committed" and repaired["repair_attempts"] == 1
    assert volume_review_block_reason(tmp_path, operation_id=trace) is None


def test_duplicate_author_revision_message_does_not_refresh_its_budget(tmp_path):
    original = volume_text("原稿")
    write_original(tmp_path, original)
    trace = "server-trace"
    operation = bind_volume_operation(tmp_path, trace, "author-reject", new_revision=True)
    result = guard(tmp_path, operation=operation, repair=lambda text, errors: text).commit(
        1, original, volume_text("违规"), "edit",
    )
    repeated = bind_volume_operation(tmp_path, trace, "author-reject", new_revision=True)
    retried = guard(tmp_path, operation=repeated).commit(1, original, volume_text("违规新候选"), "rewrite")
    assert result["repair_attempts"] == retried["repair_attempts"] == 2
    assert retried["status"] == "blocked"


def test_old_revision_cannot_commit_after_new_revision_is_bound(tmp_path):
    original = volume_text("原稿")
    path = write_original(tmp_path, original)
    trace = "server-trace"
    old = bind_volume_operation(tmp_path, trace, "old-author-reject", new_revision=True)
    newer = bind_volume_operation(tmp_path, trace, "new-author-reject", new_revision=True)
    assert bind_volume_operation(tmp_path, trace, "old-author-reject", new_revision=True) == old
    result = guard(tmp_path, operation=old).commit(1, original, volume_text(), "full")
    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == original
    assert bind_volume_operation(tmp_path, trace) == newer


def test_pending_revision_does_not_borrow_old_pass_for_stage_advance(tmp_path):
    trace = "server-trace"
    old = bind_volume_operation(tmp_path, trace)
    assert guard(tmp_path, operation=old).commit(1, "", volume_text(), "full")["status"] == "committed"
    bind_volume_operation(tmp_path, trace, "author-reject", new_revision=True)
    assert volume_review_block_reason(tmp_path, operation_id=trace)
    assert volume_review_block_reason(tmp_path, volume=1, operation_id=trace)
    assert next_stage_on_confirm(tmp_path, "volume") == "volume"


def test_plain_new_trace_without_volume_writes_does_not_block_chapter_flow(tmp_path):
    write_original(tmp_path, volume_text())
    bind_volume_operation(tmp_path, "chapter-only-trace")
    assert volume_review_block_reason(tmp_path, operation_id="chapter-only-trace") is None
