"""会话消息日志（REQ-20261019-222403 FR-005/DEC-004/005）单元测试。

锁定：
  - 两阶段写：user 先落档（ledger）、assistant 开壳→收尾补内容与状态；
  - 折叠语义：同 id 最新状态生效、顺序保持、坏行跳过；
  - interrupted 携半截内容（DEC-004：显示保留、不进上下文的服务端留档）；
  - 同 message_id 重发重新入队（断连补跑幂等，DEC-009）；
  - GET 端点（threads/{id}/conversation）回放完整。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.domains.writing.conversation_log import (
    ConversationLog,
    ConversationLogError,
)


def _make_log(tmp_path: Path) -> ConversationLog:
    return ConversationLog(tmp_path)


def test_user_ledger_and_fold_lifecycle(tmp_path: Path) -> None:
    log = _make_log(tmp_path)
    uid = log.admit_user("conv-1", "写个爽文")
    assert uid.startswith("u-")
    msgs = log.read("conv-1")
    assert [(m.role, m.status, m.content) for m in msgs] == [("user", "queued", "写个爽文")]

    log.mark_user("conv-1", uid, "running")
    aid = log.open_assistant("conv-1", "trace-1")
    log.close_assistant("conv-1", aid, status="completed", content="好的，开工。")

    msgs = log.read("conv-1")
    assert [(m.role, m.status) for m in msgs] == [("user", "running"), ("assistant", "completed")]
    assert msgs[1].content == "好的，开工。"
    assert msgs[1].trace_id == "trace-1"


def test_interrupted_keeps_partial_content(tmp_path: Path) -> None:
    """DEC-004/AC-004：半截内容随 interrupted 落档（有内容、无完成标记）。"""
    log = _make_log(tmp_path)
    uid = log.admit_user("conv-1", "继续写")
    log.mark_user("conv-1", uid, "running")
    aid = log.open_assistant("conv-1", "trace-9")
    log.close_assistant("conv-1", aid, status="interrupted", content="第三章 铁匠铺之始\n夜色")
    msgs = log.read("conv-1")
    assert msgs[1].status == "interrupted"
    assert msgs[1].content.startswith("第三章")


def test_resend_same_message_id_requeues(tmp_path: Path) -> None:
    """DEC-009：同 message_id 重发 → 重新入队，不产生重复 user 记录。"""
    log = _make_log(tmp_path)
    uid = log.admit_user("conv-1", "排队消息", message_id="u-fixed")
    log.mark_user("conv-1", uid, "paused")
    log.admit_user("conv-1", "排队消息", message_id="u-fixed")
    msgs = log.read("conv-1")
    users = [m for m in msgs if m.role == "user"]
    assert len(users) == 1
    assert users[0].status == "queued"


def test_awaiting_leg_then_answer_leg(tmp_path: Path) -> None:
    """HITL：提问腿收 awaiting，resume 腿开新助手记录收 completed（对齐客户端双行形态）。"""
    log = _make_log(tmp_path)
    uid = log.admit_user("conv-1", "开写")
    log.mark_user("conv-1", uid, "running")
    q = log.open_assistant("conv-1", "trace-1")
    log.close_assistant("conv-1", q, status="awaiting", content="选哪个题材？")
    a = log.open_assistant("conv-1", "trace-1")
    log.close_assistant("conv-1", a, status="completed", content="收到，都市异能。")
    msgs = log.read("conv-1")
    assert [m.status for m in msgs] == ["running", "awaiting", "completed"]


def test_bad_lines_skipped_and_status_folds(tmp_path: Path) -> None:
    log = _make_log(tmp_path)
    uid = log.admit_user("conv-1", "a")
    log.mark_user("conv-1", uid, "cancelled")
    # 手写坏行（损坏磁盘模拟）
    path = tmp_path / ".writing" / "conversations"
    files = list(path.glob("*.jsonl"))
    files[0].open("a", encoding="utf-8").write("not-json\n" + json.dumps({"kind": "?"}) + "\n")
    msgs = log.read("conv-1")
    assert len(msgs) == 1 and msgs[0].status == "cancelled"


def test_admit_failure_raises(tmp_path: Path) -> None:
    """FR-005 失败语义：用户意图落档失败必须抛错（调用方拒绝该轮）。"""
    import pytest

    (tmp_path / ".writing").write_text("占位文件：让 conversations 目录创建失败", encoding="utf-8")
    log = _make_log(tmp_path)
    with pytest.raises(ConversationLogError):
        log.admit_user("conv-1", "x")


def test_unsafe_conversation_id_isolated(tmp_path: Path) -> None:
    """会话 id 含路径字符 → 文件名净化 + 哈希防碰撞（不同 id 不串台）。"""
    log = _make_log(tmp_path)
    log.admit_user("../etc/passwd", "a")
    log.admit_user("..\\etc\\passwd", "b")
    files = list((tmp_path / ".writing" / "conversations").glob("*.jsonl"))
    assert len(files) == 2  # 两个不同 id 各自落档
    assert all(not f.name.startswith("..") for f in files)
    assert len(log.read("../etc/passwd")) == 1
    assert len(log.read("..\\etc\\passwd")) == 1


def test_stop_clears_queued_and_paused(tmp_path: Path, monkeypatch) -> None:
    """FR-006 停止=清空队列：门外等待者取消 + 台账 queued/paused 全部转 cancelled。"""
    from types import SimpleNamespace

    from app.domains.writing.run_gate import get_conversation_gate
    from app.routers import screenplay as sp

    log = _make_log(tmp_path)
    log.admit_user("conv-1", "排队中消息")
    paused_id = log.admit_user("conv-1", "挂起消息")
    log.mark_user("conv-1", paused_id, "paused")

    class _Store:
        def get_thread(self, user_id, thread_id):
            return SimpleNamespace(workspace_path=str(tmp_path))

    monkeypatch.setattr(sp, "get_thread_store", lambda: _Store())
    gate = get_conversation_gate("conv-1")
    cleared = sp._clear_conversation_queue("u1", "conv-1")
    assert cleared == 0  # 本用例无门外等待者
    assert gate.epoch == 1  # 纪元递增：等待者据此区分 cancelled/paused
    msgs = log.read("conv-1")
    assert [m.status for m in msgs] == ["cancelled", "cancelled"]
