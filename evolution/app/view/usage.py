"""三层用量观测 API（REQ-20261027-012631 FR-002 / FR-003 / FR-005）。

用户层 GET /usage/users：全部注册用户的运行状态、上次创作时间、token 累计。
作品层 GET /usage/workspaces：某用户全部作品的书名、最新创作时间、token 累计、
状态（running / normal / aborted / deleted，DEC-005 trace 活动派生）。

token 口径：仅显式 user_generation，未知来源不算用户。
已入库与活跃用量按 Trace 去重；未知与部分已知明确标记。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import app.core.db as db
import app.view.active as active_mod
from app.view.observation_scope import scope_sql, source_scope, experiment_metadata
from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger("evolution.usage")

router = APIRouter(prefix="/usage", tags=["usage"])

class UserUsageRow(BaseModel):
    user_id: str
    username: str
    running: bool
    last_created_at: str | None
    total_tokens: int | None
    total_partial: bool = False


class WorkspaceUsageRow(BaseModel):
    workspace_id: str
    title: str
    owner_user_id: str
    status: Literal["running", "normal", "aborted", "deleted"]
    last_created_at: str | None
    total_tokens: int | None
    total_partial: bool = False


def _later(a: str | None, b: str | None) -> str | None:
    """两个 ISO 时间戳取较新者（任一为 None 返回另一个）。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


def _active_user_rows() -> list[dict[str, Any]]:
    """活跃缓存中归属用户的创作行（进化源剔除）。

    owner 优先取行内 owner_user_id（executor index 透传）；缺省回退
    workspace_cache 归属（索引丢失降级路径，U1 契约）。
    """
    try:
        rows = active_mod.get_active_runs()
    except Exception:
        logger.warning("活跃缓存读取失败，用量观测降级为仅已入库数据", exc_info=True)
        return []
    result: list[dict[str, Any]] = []
    experiment_refs = experiment_metadata()
    for r in rows:
        refs = {**experiment_refs.get(r.get("trace_id"), {}), **r.get("external_refs", {})}
        if source_scope({**r, "external_refs": refs}) != "user":
            continue
        owner = r.get("owner_user_id")
        if not owner:
            ws = r.get("workspace_id") or ""
            if ws:
                row = db.query_one(
                    "SELECT owner_user_id FROM workspace_cache WHERE workspace_id = ?", (ws,)
                )
                owner = row["owner_user_id"] if row else None
        result.append({
            "trace_id": r.get("trace_id", ""),
            "workspace_id": r.get("workspace_id") or "",
            "owner_user_id": owner,
            "started_at": r.get("started_at"),
            "usage_total": ((r.get("metrics") or {}).get("total_tokens") if "metrics" in r else r.get("usage_total")),
            "total_partial": bool((r.get("metrics") or {}).get("total_partial", False)),
        })
    return result


def _usage_totals(group: str, active: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Choose live usage once per trace, retaining missing-data information."""
    active_ids = {row["trace_id"] for row in active}
    rows = db.query_all(
        f"""SELECT r.trace_id, r.{group} AS group_id,
                   SUM(COALESCE(n.usage_total,n.usage_input+n.usage_output)) AS tokens,
                   COUNT(n.node_id) AS calls,
                   COUNT(COALESCE(n.usage_total,n.usage_input+n.usage_output)) AS known
            FROM runs r LEFT JOIN nodes n ON n.trace_id=r.trace_id AND n.kind='llm'
            WHERE {scope_sql('user')} GROUP BY r.trace_id"""
    )
    contributions = [{"group_id": row["group_id"], "tokens": row["tokens"],
                      "partial": row["known"] == 0 or row["known"] < row["calls"]}
                     for row in rows if row["trace_id"] not in active_ids]
    contributions += [{"group_id": row[group], "tokens": row["usage_total"],
                       "partial": row["usage_total"] is None or row.get("total_partial", False)} for row in active]
    totals: dict[str, dict[str, Any]] = {}
    for row in contributions:
        value = totals.setdefault(row["group_id"], {"total_tokens": None, "total_partial": False})
        if row["tokens"] is not None:
            value["total_tokens"] = (value["total_tokens"] or 0) + row["tokens"]
        value["total_partial"] |= row["partial"]
    return totals


@router.get("/users", response_model=list[UserUsageRow])
def list_user_usage() -> list[UserUsageRow]:
    """用户层：全部注册用户的创作活动汇总（FR-002，AC-001/002/003）。

    排序：上次创作时间倒序，从未创作者排最后（AC-002）。
    """
    active = _active_user_rows()
    tokens_by_owner = _usage_totals("owner_user_id", active)

    last_rows = db.query_all(
        f"""SELECT r.owner_user_id AS owner, MAX(r.started_at) AS last_at
            FROM runs r WHERE {scope_sql('user')}
            GROUP BY r.owner_user_id"""
    )
    last_by_owner = {r["owner"]: r["last_at"] for r in last_rows}

    rows: list[UserUsageRow] = []
    for u in db.query_all("SELECT user_id, username FROM user_cache"):
        uid = u["user_id"]
        own_active = [a for a in active if a["owner_user_id"] == uid]
        # 上次创作时间（DEC-006）：已入库与运行中 trace 的 started_at 取最大
        # ——运行中的 trace 是最新一次创作，尽管尚未入库。
        last = last_by_owner.get(uid)
        for a in own_active:
            last = _later(last, a["started_at"])
        rows.append(UserUsageRow(
            user_id=uid,
            username=u["username"],
            running=bool(own_active),
            last_created_at=last,
            **tokens_by_owner.get(uid, {"total_tokens": 0, "total_partial": False}),
        ))
    rows.sort(key=lambda r: (r.last_created_at is None, r.last_created_at or ""))
    return rows


@router.get("/workspaces", response_model=list[WorkspaceUsageRow])
def list_workspace_usage(
    owner_user_id: str = Query(..., description="按归属用户过滤"),
) -> list[WorkspaceUsageRow]:
    """作品层：该用户全部作品（含已删除）的用量与状态（FR-003，AC-004/005）。

    作品源 = workspace_cache 归属行 UNION runs 中出现但缓存缺失的 workspace
    （对账窗口内的新作品不得消失，书名降级空串）。
    """
    active = _active_user_rows()
    tokens_by_ws = _usage_totals("workspace_id", active)

    last_rows = db.query_all(
        f"""SELECT r.workspace_id AS ws, MAX(r.started_at) AS last_at
            FROM runs r WHERE {scope_sql('user')}
            GROUP BY r.workspace_id"""
    )
    last_by_ws = {r["ws"]: r["last_at"] for r in last_rows}

    # 各作品最近一条 user_generation trace 的状态（aborted 判定依据）。
    status_rows = db.query_all(
        f"""SELECT r.workspace_id AS ws, r.status
            FROM runs r
            JOIN (SELECT r2.workspace_id AS workspace_id, MAX(r2.started_at) AS mx
                  FROM runs r2 WHERE {scope_sql('user', 'r2')}
                  GROUP BY r2.workspace_id) m
              ON r.workspace_id = m.workspace_id AND r.started_at = m.mx
            WHERE {scope_sql('user')}"""
    )
    status_by_ws = {r["ws"]: r["status"] for r in status_rows}

    # 活跃缓存按 workspace 归户（owner 回退已在 _active_user_rows 处理）。
    active_ws_ids = {a["workspace_id"] for a in active if a["workspace_id"]}
    active_last_by_ws: dict[str, str] = {}
    for a in active:
        if a["owner_user_id"] == owner_user_id and a["workspace_id"]:
            started = a["started_at"]
            if started:
                cur = active_last_by_ws.get(a["workspace_id"])
                if cur is None or started > cur:
                    active_last_by_ws[a["workspace_id"]] = started

    # 作品全集：缓存行 + runs 遗漏 workspace。
    cached = db.query_all(
        "SELECT workspace_id, title, deleted FROM workspace_cache WHERE owner_user_id = ?",
        (owner_user_id,),
    )
    known_ids = {r["workspace_id"] for r in cached}
    orphans = db.query_all(
        f"""SELECT DISTINCT r.workspace_id AS ws
            FROM runs r
            WHERE r.owner_user_id = ? AND {scope_sql('user')}""",
        (owner_user_id,),
    )
    # 活跃但未入库的新作品（对账窗口内）也补进来，运行中状态才可见。
    for a in active:
        ws = a["workspace_id"]
        if ws and ws not in known_ids and a["owner_user_id"] == owner_user_id:
            orphans.append({"ws": ws})
    orphan_ids = sorted({r["ws"] for r in orphans} - known_ids)

    rows: list[WorkspaceUsageRow] = []
    for c in cached:
        ws = c["workspace_id"]
        deleted = bool(c["deleted"])
        # 状态派生优先级（DEC-005）：已删除 > 进行中 > 异常中断 > 正常。
        if deleted:
            status: Literal["running", "normal", "aborted", "deleted"] = "deleted"
        elif ws in active_ws_ids:
            status = "running"
        elif status_by_ws.get(ws) == "failed":
            status = "aborted"
        else:
            status = "normal"
        rows.append(WorkspaceUsageRow(
            workspace_id=ws,
            title=c["title"] or "",
            owner_user_id=owner_user_id,
            status=status,
            last_created_at=_later(last_by_ws.get(ws), active_last_by_ws.get(ws)),
            **tokens_by_ws.get(ws, {"total_tokens": 0, "total_partial": False}),
        ))
    for ws in orphan_ids:
        rows.append(WorkspaceUsageRow(
            workspace_id=ws,
            title="",
            owner_user_id=owner_user_id,
            status="running" if ws in active_ws_ids
            else ("aborted" if status_by_ws.get(ws) == "failed" else "normal"),
            last_created_at=_later(last_by_ws.get(ws), active_last_by_ws.get(ws)),
            **tokens_by_ws.get(ws, {"total_tokens": 0, "total_partial": False}),
        ))
    rows.sort(key=lambda r: (r.status == "deleted", r.last_created_at is None, r.last_created_at or ""))
    return rows
