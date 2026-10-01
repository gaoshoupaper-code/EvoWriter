"""evolve_agents 表访问层（进化 Agent 实体，REQ-20261001-131018 DEC-003/009/010）。

进化 Agent = 绑定作品的长期进化负责人：
  - 一个 Agent 绑一个作品（1:1，DEC-009）：active 状态下 workspace_id 唯一。
    先查后插 + DB 层 partial unique index（idx_ea_active_workspace）双保险，
    并发创建的竞态由 IntegrityError 兜底转为业务错误。
  - 归档释放绑定（DEC-010）：status → archived 后作品可再绑新 Agent。
  - 作品删除标记（DEC-010）：executor 确认作品不存在时写 work_deleted_at（粘性，
    不自动清除——workspace_id 是 uuid，不存在"同 id 复活"）。

会话（evolve_sessions.agent_id）与进化点（evolve_points.agent_id）挂在本实体下。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

import app.core.db as db


class WorkspaceAlreadyBoundError(Exception):
    """作品已被其他 active Agent 绑定（1:1 校验失败，DEC-009）。

    message 含占用 Agent 的名字与 id，API 层直接透传给前端做引导。
    """

    def __init__(self, occupier: dict[str, Any]) -> None:
        self.occupier = occupier
        super().__init__(
            f"作品已被进化 Agent「{occupier.get('name')}」（{occupier.get('agent_id')}）绑定"
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create(name: str, workspace_id: str) -> dict[str, Any]:
    """创建进化 Agent（绑定作品，status=active）。

    Raises:
        WorkspaceAlreadyBoundError: workspace_id 已被其他 active Agent 绑定。
    """
    occupier = find_active_by_workspace(workspace_id)
    if occupier is not None:
        raise WorkspaceAlreadyBoundError(occupier)
    agent_id = uuid.uuid4().hex[:12]
    now = _now()
    try:
        db.execute(
            """INSERT INTO evolve_agents (agent_id, name, workspace_id, status, created_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?)""",
            (agent_id, name, workspace_id, now, now),
        )
    except sqlite3.IntegrityError as exc:
        # partial unique index 兜底：查后插的窗口内被并发抢占
        occupier = find_active_by_workspace(workspace_id)
        if occupier is not None:
            raise WorkspaceAlreadyBoundError(occupier) from exc
        raise
    return get(agent_id)  # type: ignore[return-value]


def get(agent_id: str) -> dict[str, Any] | None:
    """按 agent_id 查单个 Agent。"""
    return db.query_one(
        "SELECT * FROM evolve_agents WHERE agent_id = ?",
        (agent_id,),
    )


def find_active_by_workspace(workspace_id: str) -> dict[str, Any] | None:
    """查绑定某作品的 active Agent（1:1 查重用）。归档 Agent 不算占用。"""
    return db.query_one(
        "SELECT * FROM evolve_agents WHERE workspace_id = ? AND status = 'active'",
        (workspace_id,),
    )


def list_agents(
    *, include_archived: bool = False, limit: int = 100,
) -> list[dict[str, Any]]:
    """列出 Agent（最新在前），附会话数与已发布数统计。

    Args:
        include_archived: True 时含归档 Agent（归档区展示用）。
    """
    where = "" if include_archived else "WHERE a.status = 'active'"
    return db.query_all(
        f"""SELECT a.*,
                   (SELECT COUNT(*) FROM evolve_sessions s WHERE s.agent_id = a.agent_id) AS session_count,
                   (SELECT COUNT(*) FROM evolve_sessions s
                    WHERE s.agent_id = a.agent_id AND s.status = 'published') AS published_count
            FROM evolve_agents a
            {where}
            ORDER BY a.id DESC LIMIT ?""",
        (limit,),
    )


def rename(agent_id: str, name: str) -> bool:
    """改名（绑定关系不可改，FR-001——换作品须归档重建）。

    Returns:
        True 命中已更新；False agent_id 不存在。
    """
    cur = db.execute(
        "UPDATE evolve_agents SET name = ?, updated_at = ? WHERE agent_id = ?",
        (name, _now(), agent_id),
    )
    return cur.rowcount > 0


def archive(agent_id: str) -> bool:
    """归档 Agent（DEC-010）：释放绑定，历史会话进「未绑定」区由查询侧归类。

    Returns:
        True 命中已归档；False agent_id 不存在或已是 archived。
    """
    cur = db.execute(
        "UPDATE evolve_agents SET status = 'archived', updated_at = ? "
        "WHERE agent_id = ? AND status = 'active'",
        (_now(), agent_id),
    )
    return cur.rowcount > 0


def mark_work_deleted(agent_id: str) -> bool:
    """标记绑定作品已被删除（DEC-010，粘性——不提供清除入口）。

    仅在 executor 明确返回作品不存在时由 API 层调用（不可达不误标，FR-009）。

    Returns:
        True 本次写入标记；False agent 不存在或早已标记。
    """
    cur = db.execute(
        "UPDATE evolve_agents SET work_deleted_at = ?, updated_at = ? "
        "WHERE agent_id = ? AND work_deleted_at IS NULL",
        (_now(), _now(), agent_id),
    )
    return cur.rowcount > 0


__all__ = [
    "WorkspaceAlreadyBoundError",
    "create",
    "get",
    "find_active_by_workspace",
    "list_agents",
    "rename",
    "archive",
    "mark_work_deleted",
]
