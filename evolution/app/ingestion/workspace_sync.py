"""作品缓存对账同步：定时从 executor 拉取作品列表，维护本地 workspace_cache 表。

REQ-20261027-012631 FR-006 / DEC-004 / DEC-011：
- executor 的作品删除是物理删除，观测端要保留书名与归属，必须提前缓存。
- 5 分钟对账（不是 user_cache 的 24 小时）：管理员看到「已删除」的延迟分钟级。
- 失败语义：拉取失败跳过本轮，任何状态不变——executor 短暂不可达不得把
  全部作品误标已删除（FR-006 失败语义，AC-005 边界）。

数据流：GET {executor_url}/internal/workspaces → {"workspaces": [...], "total": n}
→ apply_workspaces 事务 upsert + 标删。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import app.core.db as db
from app.core.settings import settings

logger = logging.getLogger("evolution.workspace_sync")

_SYNC_INTERVAL = 300.0  # 对账间隔：5 分钟（DEC-011 分钟级）
_task: asyncio.Task | None = None


def start_workspace_sync_scheduler() -> None:
    """启动作品对账后台任务（幂等）。在 lifespan 启动时调用。"""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_sync_loop())


async def _sync_loop() -> None:
    """周期对账：启动先跑一次（接住 evolution 重启期间的删除），之后每 5 分钟。"""
    await asyncio.to_thread(sync_once)
    while True:
        await asyncio.sleep(_SYNC_INTERVAL)
        try:
            await asyncio.to_thread(sync_once)
        except Exception:
            logger.exception("作品缓存对账异常")


def _default_fetch(url: str) -> dict[str, Any]:
    import httpx

    resp = httpx.get(url, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def sync_once(
    fetch: Callable[[str], dict[str, Any]] | None = None,
) -> int:
    """拉取 executor 作品列表并应用对账。

    Args:
        fetch: 可注入的拉取函数（测试用）；缺省走 httpx GET。

    Returns:
        本轮应用的作品数。拉取失败返回 -1（状态不变，下轮重试）。
    """
    fetcher = fetch or _default_fetch
    url = f"{settings.executor_url.rstrip('/')}/internal/workspaces"
    try:
        payload = fetcher(url)
        workspaces = payload.get("workspaces")
        if not isinstance(workspaces, list):
            raise ValueError(f"unexpected payload shape: {type(workspaces)}")
    except Exception as exc:
        # 失败跳过：不动 deleted 标记，已删除不复活、未删不误标。
        logger.warning("作品缓存对账：拉取作品列表失败（本轮跳过）：%s", exc)
        return -1

    applied = apply_workspaces(workspaces)
    logger.info("作品缓存对账完成：%d 部作品", applied)
    return applied


def apply_workspaces(workspaces: list[dict[str, Any]]) -> int:
    """把 executor 作品列表应用进 workspace_cache（纯 DB 层，事务原子）。

    - 列表内作品 upsert：新作品插入，已有作品更新 title/owner，deleted 重置 0。
    - 缓存中有、列表中没有的作品标 deleted=1，记录保留（书名/归属/统计不丢）。
    """
    now = datetime.now(UTC).isoformat()
    conn = db.get_conn()
    with db._lock:
        for w in workspaces:
            ws_id = str(w.get("workspace_id") or "").strip()
            if not ws_id:
                continue
            conn.execute(
                """INSERT INTO workspace_cache
                       (workspace_id, owner_user_id, title, deleted, first_synced_at, synced_at)
                   VALUES (?, ?, ?, 0, ?, ?)
                   ON CONFLICT(workspace_id) DO UPDATE SET
                       owner_user_id = excluded.owner_user_id,
                       title = excluded.title,
                       deleted = 0,
                       synced_at = excluded.synced_at""",
                (ws_id, str(w.get("owner_user_id") or "unknown"),
                 str(w.get("title") or ""), now, now),
            )
        ids = [str(w.get("workspace_id") or "").strip() for w in workspaces]
        ids = [i for i in ids if i]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE workspace_cache SET deleted = 1, synced_at = ? "
                f"WHERE workspace_id NOT IN ({placeholders})",
                (now, *ids),
            )
        else:
            # executor 拉取成功但作品为空是真实状态：全部标删。
            conn.execute(
                "UPDATE workspace_cache SET deleted = 1, synced_at = ? WHERE deleted = 0",
                (now,),
            )
        conn.commit()
    return len(ids)
