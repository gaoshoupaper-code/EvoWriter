"""evolve_sessions 表的 CRUD。

单进化 Agent 的 session 记录（替换 adapt_rounds 的多轮语义）。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import app.core.db as db


def create_session(
    session_id: str,
    case_id: str = "",
    agent_id: str | None = None,
    work_context: dict[str, Any] | None = None,
) -> None:
    """创建一个 evolve session（启动时调用；agent_id/work_context 为作品绑定注入，REQ-20261001-131018）。"""
    now = datetime.now(UTC).isoformat()
    db.execute(
        """INSERT INTO evolve_sessions
           (session_id, case_id, status, agent_id, work_context_json, created_at, updated_at)
           VALUES (?, ?, 'running', ?, ?, ?, ?)""",
        (
            session_id, case_id, agent_id,
            json.dumps(work_context, ensure_ascii=False) if work_context else None,
            now, now,
        ),
    )


def update_session(
    session_id: str,
    *,
    status: str | None = None,
    phase: str | None = None,
    design_doc_path: str | None = None,
    change_log_path: str | None = None,
    eval_ref: str | None = None,
    bound_eval_dossier_id: str | None = None,
    benchmark_batch_id: str | None = None,
) -> None:
    """更新 session 字段（只更新非 None 的字段）。

    三功能解耦后精简：删除 baseline/candidate/score 等废弃字段参数（T9）。
    新增 eval_ref（关联评估报告 eval_id，S6/T2）。
    bound_eval_dossier_id（阶段 D，需求 §42：永久绑定评估卷宗，会话创建后不可变）。
    """
    sets: list[str] = []
    params: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if phase is not None:
        sets.append("phase = ?")
        params.append(phase)
    if design_doc_path is not None:
        sets.append("design_doc_path = ?")
        params.append(design_doc_path)
    if change_log_path is not None:
        sets.append("change_log_path = ?")
        params.append(change_log_path)
    if eval_ref is not None:
        sets.append("eval_ref = ?")
        params.append(eval_ref)
    if bound_eval_dossier_id is not None:
        sets.append("bound_eval_dossier_id = ?")
        params.append(bound_eval_dossier_id)
    if benchmark_batch_id is not None:
        sets.append("benchmark_batch_id = ?")
        params.append(benchmark_batch_id)

    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(datetime.now(UTC).isoformat())
    params.append(session_id)

    db.execute(
        f"UPDATE evolve_sessions SET {', '.join(sets)} WHERE session_id = ?",
        tuple(params),
    )


def get_session(session_id: str) -> dict[str, Any] | None:
    """查单个 session。report_json / work_context_json 自动反序列化。"""
    row = db.query_one(
        "SELECT * FROM evolve_sessions WHERE session_id = ?",
        (session_id,),
    )
    if row and row.get("report_json"):
        try:
            row["report"] = json.loads(row["report_json"])
        except (json.JSONDecodeError, TypeError):
            row["report"] = None
    if row and row.get("work_context_json"):
        try:
            row["work_context"] = json.loads(row["work_context_json"])
        except (json.JSONDecodeError, TypeError):
            row["work_context"] = None
    return row


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """列出 session（最新在前）。"""
    return db.query_all(
        "SELECT * FROM evolve_sessions ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def list_sessions_by_agent(agent_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """列出某进化 Agent 名下的 session（最新在前，Agent 档案页数据源）。

    旧会话（agent_id NULL）不会出现在任何 Agent 名下——归「未绑定」区（DEC-002）。
    """
    return db.query_all(
        "SELECT * FROM evolve_sessions WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
        (agent_id, limit),
    )


def count_unbound_sessions() -> int:
    """统计无绑定旧会话数（「未绑定」归档区角标）。"""
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM evolve_sessions WHERE agent_id IS NULL"
    )
    return row["c"] if row else 0


__all__ = [
    "create_session",
    "update_session",
    "get_session",
    "list_sessions",
]
