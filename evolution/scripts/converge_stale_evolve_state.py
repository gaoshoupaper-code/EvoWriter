"""一次性收敛脚本：进化会话观测断裂期的存量脏数据。

需求依据：REQ-20261001-170207 FR-004 / DEC-005 / AC-004。

背景（2026-10-01 线上回归）：
  - stop 端点曾在同步函数里建协程，必炸 500 且收敛协程 never awaited，
    会话永久卡 cancelling（RC3，代码已修）。
  - 进化点 seq 原按会话编号，跨会话撞号；已改按 Agent 全局编号（RC2，代码已修），
    存量点需一次性重排（DEC-005：浮窗编号变一次，换引用唯一）。

动作（幂等，可重复执行）：
  1. evolve_sessions.status='cancelling' → 'cancelled'
  2. 上述会话的自观测 trace（runs.status='running'）→ 'cancelled' 终态
  3. evolve_points 按 agent_id 分组重排 seq（created_at 升序 → 1..N）；
     旧链路（agent_id NULL，只读归档 DEC-002）不动

用法（容器内 /app/evolution 目录）：
    python -m scripts.converge_stale_evolve_state
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

logger = logging.getLogger("converge_stale_evolve_state")


def converge_cancelling_sessions(conn: sqlite3.Connection) -> list[dict]:
    """cancelling 会话 → cancelled。返回被收敛的会话清单（含 self_trace_id）。"""
    now = datetime.now(UTC).isoformat()
    rows = conn.execute(
        "SELECT session_id, self_trace_id FROM evolve_sessions WHERE status = 'cancelling'"
    ).fetchall()
    converged = []
    for r in rows:
        conn.execute(
            "UPDATE evolve_sessions SET status = 'cancelled', updated_at = ? WHERE session_id = ?",
            (now, r["session_id"]),
        )
        converged.append(
            {"session_id": r["session_id"], "self_trace_id": r["self_trace_id"]}
        )
    return converged


def converge_stuck_self_traces(
    conn: sqlite3.Connection, sessions: list[dict]
) -> list[str]:
    """被收敛会话的自观测 trace 若仍 running → cancelled 终态。返回 trace id 清单。"""
    now = datetime.now(UTC).isoformat()
    converged = []
    for s in sessions:
        trace_id = s.get("self_trace_id")
        if not trace_id:
            continue
        cur = conn.execute(
            """UPDATE runs SET status = 'cancelled', ended_at = ?,
                   error = 'maintenance: session stuck in cancelling (RC3 converge)'
               WHERE trace_id = ? AND status = 'running'""",
            (now, trace_id),
        )
        if cur.rowcount > 0:
            converged.append(trace_id)
    return converged


BACKUP_TABLE = "evolve_points_seq_backup_20261001"


def backup_agent_points(conn: sqlite3.Connection) -> bool:
    """重排前备份 agent 名下点的旧 seq（review finding-5：可回滚性）。

    CREATE TABLE IF NOT EXISTS ... AS SELECT 幂等：二次运行时表已存在即跳过，
    保留的是首次运行前（重排前）的旧值。
    """
    existed = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (BACKUP_TABLE,),
    ).fetchone()
    if existed:
        return False
    conn.execute(
        f"""CREATE TABLE {BACKUP_TABLE} AS
            SELECT id, session_id, agent_id, seq, created_at
            FROM evolve_points WHERE agent_id IS NOT NULL"""
    )
    return True


def renumber_agent_points(conn: sqlite3.Connection) -> dict[str, list[tuple[str, int, int]]]:
    """按 Agent 重排进化点 seq（DEC-005）。返回 {agent_id: [(point_id, old, new)]}。

    规则：created_at 升序（同刻按 id 破坏平局）编号 1..N；seq 已正确的点不写
    （幂等：第二次运行为全量 no-op）。agent_id NULL 的旧行不动。

    逐行 UPDATE 会与已存在的唯一索引 idx_ep_agent_seq 冲突（如存量序号与
    创建顺序不一致时，中间态撞未更新行的现值 → IntegrityError → 整事务回滚
    且重跑复现）。重排前先 DROP 该索引（同事务），结束后由
    ensure_agent_seq_unique_index 重建——原子且幂等。
    """
    conn.execute("DROP INDEX IF EXISTS idx_ep_agent_seq")
    rows = conn.execute(
        """SELECT id, agent_id, seq FROM evolve_points
           WHERE agent_id IS NOT NULL
           ORDER BY agent_id, created_at, id"""
    ).fetchall()
    changes: dict[str, list[tuple[str, int, int]]] = {}
    current_agent = None
    next_seq = 0
    for r in rows:
        if r["agent_id"] != current_agent:
            current_agent = r["agent_id"]
            next_seq = 1
        if r["seq"] != next_seq:
            conn.execute(
                "UPDATE evolve_points SET seq = ? WHERE id = ?",
                (next_seq, r["id"]),
            )
            changes.setdefault(current_agent, []).append(
                (r["id"], r["seq"], next_seq)
            )
        next_seq += 1
    return changes


def ensure_agent_seq_unique_index(conn: sqlite3.Connection) -> bool:
    """重排后补建 Agent 内序号唯一索引（FR-002 / review finding-3）。

    与 app/core/db.py 的 _migrate_ep_agent_seq_unique 同语句；此处建失败视为
    异常（重排后不应再有撞号），由调用方事务回滚。
    """
    before = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ep_agent_seq'"
    ).fetchone()
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ep_agent_seq "
        "ON evolve_points(agent_id, seq) WHERE agent_id IS NOT NULL"
    )
    return before is None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    from app.core.settings import settings

    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            sessions = converge_cancelling_sessions(conn)
            traces = converge_stuck_self_traces(conn, sessions)
            backup_created = backup_agent_points(conn)
            renumber = renumber_agent_points(conn)
            ensure_agent_seq_unique_index(conn)
    except Exception:
        # 事务已原子回滚（含 DROP/CREATE INDEX）。本脚本幂等——与在线服务并发
        # 窗期的 propose 竞态会让建索引失败，排查后直接重跑即可收敛。
        logging.getLogger("converge_stale_evolve_state").exception(
            "收敛事务回滚（常见原因：与在线 propose 竞态）。脚本幂等，可直接重跑"
        )
        raise
    finally:
        conn.close()

    print(f"[1] cancelling → cancelled：{len(sessions)} 个会话")
    for s in sessions:
        print(f"    - {s['session_id']}")
    print(f"[2] 卡 running 的自观测 trace → cancelled：{len(traces)} 条")
    for t in traces:
        print(f"    - {t}")
    total = sum(len(v) for v in renumber.values())
    print(f"[3] 进化点 seq 按 Agent 重排：{total} 个点重编号")
    for agent, items in renumber.items():
        print(f"    - agent {agent}: {len(items)} 个点")
        for point_id, old, new in items:
            print(f"        {point_id} #{old} → #{new}")
    print(f"[4] 重排前备份表 {BACKUP_TABLE}：{'本次创建' if backup_created else '已存在（保留首次旧值）'}")
    # renumber 总是先 DROP 再重建唯一索引，此处以实际在场为准（而非创建动作）
    idx_present = True
    try:
        conn2 = sqlite3.connect(settings.db_path)
        idx_present = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ep_agent_seq'"
        ).fetchone() is not None
        conn2.close()
    except sqlite3.Error:
        pass
    print(f"[5] 唯一索引 idx_ep_agent_seq：{'在场' if idx_present else '缺失（重查日志）'}")
    print("收敛完成（幂等，可重复执行）")


if __name__ == "__main__":
    main()
