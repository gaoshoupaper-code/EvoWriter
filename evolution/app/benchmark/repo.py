"""benchmark_runs 表的数据访问层（数据闭环设计 C1/D13）。

benchmark_runs 存 case × 版本 × 评估 的矩阵数据，支撑跨版本 leaderboard 对比。
同一 golden_revision 的行之间分数可比（D8 重跑历史保证可比性）。

批次（batch）= 一次触发（发版/升级）产生的全部行，共享 batch_id。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import app.core.db as db

# 状态常量
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_EVALUATING = "evaluating"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"   # 终态：批次停止（用户/止损）后未完成或被中断的行

# 批次终止原因（benchmark_batch_meta.stop_reason，REQ-20260920-192126/DEC-007）
STOP_USER = "user_stop"
STOP_AUTO_FAIL = "auto_fail"

MAX_RETRIES = 3


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_batch_id() -> str:
    return uuid.uuid4().hex[:16]


# ── 批次创建 ────────────────────────────────────────────────


def create_batch(
    *,
    case_ids: list[str],
    versions: list[int],
    golden_revision: str,
    seeds: int = 3,
    rubric_version: str | None = None,
    judge_fp: str | None = None,
    concurrency: int = 1,
) -> str:
    """创建一个 benchmark 批次（case × 版本 × seed 的笛卡尔积），返回 batch_id。

    每个组合创建一行 benchmark_runs（pending 状态）。seeds = 每 case 独立
    重复次数（DEC-013 固定 3）。rubric_version / judge_fp 建批时采集（DEC-015）；
    model_fp / manifest_fp / harness_commit 由 runner 跑完后逐行回填。
    concurrency = 本批次执行并发度（FR-001，随批次持久化，AC-001）。
    """
    batch_id = _new_batch_id()
    now = _now()

    rows = []
    for version in versions:
        for case_id in case_ids:
            for seed in range(1, max(1, seeds) + 1):
                rows.append((
                    batch_id, case_id, version, golden_revision,
                    None, None, None, STATUS_PENDING, 0, None, now, None,
                    seed, rubric_version, judge_fp, None, None, None, None,
                    max(1, concurrency),
                ))

    if rows:
        db.executemany(
            """INSERT INTO benchmark_runs
               (batch_id, case_id, harness_version, golden_revision,
                trace_id, eval_id, scores_json, status, retries, error,
                ran_at, finished_at,
                seed, rubric_version, judge_fp, model_fp, manifest_fp,
                harness_commit, platform_manifest_id, concurrency)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    logger = _get_logger()
    logger.info(
        "创建 benchmark 批次 %s: %d case × %d 版本 × %d seed = %d 行 (golden_revision=%s rubric=%s judge=%s 并发=%d)",
        batch_id, len(case_ids), len(versions), max(1, seeds), len(rows),
        golden_revision, rubric_version, judge_fp, max(1, concurrency),
    )
    return batch_id


def set_fingerprints(
    run_id: int,
    *,
    harness_commit: str | None,
    model_fp: str | None,
    manifest_fp: str | None,
    platform_manifest_id: int | None = None,
) -> None:
    """单行跑完后回填指纹（源 = Platform Run 绑定，DEC-015 对齐）。

    Platform 不可达或绑定缺失时调用方传 manifest_fp=UNBOUND（身份未知，
    版本对比按指纹校验拒绝其所在批次）。
    """
    db.execute(
        """UPDATE benchmark_runs
           SET harness_commit=?, model_fp=?, manifest_fp=?, platform_manifest_id=? WHERE id=?""",
        (harness_commit, model_fp, manifest_fp, platform_manifest_id, run_id),
    )


def set_cost(
    run_id: int,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    llm_calls: int | None,
    wall_clock_ms: int | None,
) -> None:
    """单行评分完成后回填开销统计（REQ-20260922-162823 FR-006）。

    数据源 = trace 摄入库聚合（nodes.usage_* / runs.duration_ms）；聚合失败
    由调用方捕获后传全 None（失败语义：该行开销字段记 NULL，不影响质量评分）。
    """
    db.execute(
        """UPDATE benchmark_runs
           SET input_tokens=?, output_tokens=?, llm_calls=?, wall_clock_ms=?
           WHERE id=?""",
        (input_tokens, output_tokens, llm_calls, wall_clock_ms, run_id),
    )


# ── 行状态流转 ──────────────────────────────────────────────


def claim_next_pending(batch_id: str) -> dict[str, Any] | None:
    """原子抢占一行 pending 转 running（并发 worker 池用，FR-001）。

    单条 UPDATE...RETURNING 在 db 锁内原子完成「选中 + 占据」，
    多 worker 并发调用不会取到同一行（AC-001 竞态风险的处置）。
    本批次无 pending（含重试退回的行被重新抢占）返回 None。
    """
    with db.transaction() as conn:
        cur = conn.execute(
            """UPDATE benchmark_runs SET status=?
               WHERE id = (
                   SELECT id FROM benchmark_runs
                   WHERE status=? AND retries < ? AND batch_id=?
                   ORDER BY ran_at ASC, id ASC LIMIT 1
               )
               RETURNING *""",
            (STATUS_RUNNING, STATUS_PENDING, MAX_RETRIES, batch_id),
        )
        row = cur.fetchone()
        # 耗尽 RETURNING 结果集（语句 step 到完成），否则事务 commit 报
        # "SQL statements in progress"
        cur.fetchall()
    return dict(row) if row else None


def mark_running(run_id: int) -> None:
    db.execute(
        "UPDATE benchmark_runs SET status=? WHERE id=?",
        (STATUS_RUNNING, run_id),
    )


def set_trace(run_id: int, trace_id: str) -> None:
    """跑出 trace 后回填，转 evaluating。

    WHERE 限定非终态：批次停止把行转 cancelled 后，迟到写入不得复活该行。
    """
    db.execute(
        "UPDATE benchmark_runs SET status=?, trace_id=? WHERE id=? AND status IN (?, ?)",
        (STATUS_EVALUATING, trace_id, run_id, STATUS_PENDING, STATUS_RUNNING),
    )


def set_result(run_id: int, *, eval_id: str | None, scores_json: str | None) -> None:
    """评估完成，写分数，转 done（同样不得覆盖终态行，竞态见 set_trace）。"""
    db.execute(
        """UPDATE benchmark_runs
           SET status=?, eval_id=?, scores_json=?, finished_at=?
           WHERE id=? AND status=?""",
        (STATUS_DONE, eval_id, scores_json, _now(), run_id, STATUS_EVALUATING),
    )


def mark_failed(run_id: int, error: str) -> None:
    """失败：增加 retries，未超 MAX_RETRIES 则回退 pending（可重试）。

    WHERE 限定 running/evaluating：行已被取消时丢弃迟到失败写入。
    """
    row = db.query_one("SELECT retries FROM benchmark_runs WHERE id=?", (run_id,))
    retries = (row["retries"] if row else 0) + 1
    if retries >= MAX_RETRIES:
        status = STATUS_FAILED
    else:
        status = STATUS_PENDING  # 回退待重试
    db.execute(
        "UPDATE benchmark_runs SET status=?, retries=?, error=? WHERE id=? AND status IN (?, ?)",
        (status, retries, error[:500], run_id, STATUS_RUNNING, STATUS_EVALUATING),
    )


def mark_cancelled(run_id: int) -> None:
    """批次停止时 worker 把手中行转 cancelled（行已终态则不覆盖）。"""
    db.execute(
        "UPDATE benchmark_runs SET status=?, finished_at=? WHERE id=? AND status IN (?, ?, ?)",
        (STATUS_CANCELLED, _now(), run_id, STATUS_PENDING, STATUS_RUNNING, STATUS_EVALUATING),
    )


# ── 批次终止（REQ-20260920-192126 FR-001/FR-002）─────────────


def stop_batch(batch_id: str, *, reason: str) -> dict[str, Any]:
    """终止批次：全部非终态行转 cancelled + 落批次终止原因（幂等）。

    不依赖存活 worker（僵尸批次同样可清：running/evaluating 一并转终态）。
    存活 worker 感知取消事件后自行叫停 executor 任务（task_id 只在 worker
    手里）；其迟到的行状态写入被终态守卫拦截，不会复活 cancelled 行。
    重复调用返回当前状态不报错。
    """
    now = _now()
    with db.transaction() as conn:
        # 从未尝试 / 正在执行的行 → cancelled；失败过（retries>0，含待重试回退）
        # 的行 → failed，保留失败痕迹与 error 供报告归因
        cur = conn.execute(
            """UPDATE benchmark_runs SET status=?, finished_at=?
               WHERE batch_id=? AND status IN (?, ?, ?) AND retries=0""",
            (STATUS_CANCELLED, now, batch_id,
             STATUS_PENDING, STATUS_RUNNING, STATUS_EVALUATING),
        )
        cur.fetchall()
        conn.execute(
            """UPDATE benchmark_runs SET status=?, finished_at=?
               WHERE batch_id=? AND status IN (?, ?, ?) AND retries>0""",
            (STATUS_FAILED, now, batch_id,
             STATUS_PENDING, STATUS_RUNNING, STATUS_EVALUATING),
        ).fetchall()
        conn.execute(
            "INSERT OR IGNORE INTO benchmark_batch_meta (batch_id, stop_reason, stopped_at) VALUES (?, ?, ?)",
            (batch_id, reason, now),
        )
    logger = _get_logger()
    logger.info("批次 %s 终止（%s）：非终态行转 cancelled", batch_id, reason)
    return get_batch(batch_id)


def get_stop_reason(batch_id: str) -> str | None:
    """批次终止原因（user_stop / auto_fail）；无记录 = 未被终止。"""
    return _get_stop_reason(db.get_conn(), batch_id)


def _get_stop_reason(conn: Any, batch_id: str) -> str | None:
    row = conn.execute(
        "SELECT stop_reason FROM benchmark_batch_meta WHERE batch_id=?", (batch_id,)
    ).fetchone()
    return row["stop_reason"] if row else None


# ── 查询 ────────────────────────────────────────────────────


def _derive_batch_status(
    *, total: int, done: int, failed: int, cancelled: int, stop_reason: str | None,
) -> str:
    """批次状态推导（REQ-20260920-192126/DEC-007）。

    active>0 → running；有终止记录按原因分（user_stop=cancelled，
    auto_fail=failed/partial）；无终止记录时 cancelled 行兜底推导为
    cancelled；否则按 done/failed 组合。历史批次（无 cancelled、无 meta）
    与旧规则完全兼容。
    """
    active = total - done - failed - cancelled
    if active > 0:
        return "running"
    if stop_reason == STOP_USER:
        return "cancelled"
    if stop_reason == STOP_AUTO_FAIL:
        return "partial" if done > 0 else "failed"
    if cancelled > 0:
        return "cancelled"
    if failed > 0:
        return "partial" if done > 0 else "failed"
    return "done"


def get_recent_batches(limit: int = 20) -> list[dict[str, Any]]:
    """最近批次摘要列表（桌面端评测页用，FR-008 增量）。

    按 batch 聚合：进度计数（含 cancelled）+ 批次级指纹（取首行）+
    harness 版本 + 终止原因 + 均分（done 行 overall 平均，
    FR-002/REQ-20260923-131103——单 SQL 聚合，无逐行 N+1）。
    """
    rows = db.query_all(
        """SELECT r.batch_id, COUNT(*) AS total,
                  SUM(CASE WHEN r.status='done' THEN 1 ELSE 0 END) AS done,
                  SUM(CASE WHEN r.status='failed' THEN 1 ELSE 0 END) AS failed,
                  SUM(CASE WHEN r.status='cancelled' THEN 1 ELSE 0 END) AS cancelled,
                  AVG(CASE WHEN r.status='done'
                           THEN json_extract(r.scores_json, '$.overall') END) AS avg_raw,
                  MAX(ran_at) AS ran_at, MIN(ran_at) AS trigger_at,
                  MAX(r.harness_version) AS harness_version,
                  MAX(r.golden_revision) AS golden_revision,
                  MAX(r.rubric_version) AS rubric_version,
                  MAX(r.judge_fp) AS judge_fp,
                  MAX(r.concurrency) AS concurrency,
                  m.stop_reason AS stop_reason
           FROM benchmark_runs r
           LEFT JOIN benchmark_batch_meta m ON m.batch_id = r.batch_id
           GROUP BY r.batch_id
           ORDER BY trigger_at DESC
           LIMIT ?""",
        (limit,),
    )
    batches: list[dict[str, Any]] = []
    for row in rows:
        done, failed, cancelled = row["done"], row["failed"], row["cancelled"]
        total = row["total"]
        status = _derive_batch_status(
            total=total, done=done, failed=failed, cancelled=cancelled,
            stop_reason=row["stop_reason"],
        )
        avg_raw = row["avg_raw"]
        batches.append({
            "batch_id": row["batch_id"],
            "status": status,
            "progress": {
                "total": total, "done": done, "failed": failed,
                "cancelled": cancelled, "active": total - done - failed - cancelled,
            },
            "avg_overall": round(avg_raw, 2) if isinstance(avg_raw, (int, float)) else None,
            "harness_version": row["harness_version"],
            "golden_revision": row["golden_revision"],
            "rubric_version": row["rubric_version"],
            "judge_fp": row["judge_fp"],
            "concurrency": row["concurrency"],
            "triggered_at": row["trigger_at"],
            "stop_reason": row["stop_reason"],
        })
    return batches


def count_batches() -> int:
    """去重批次总数（批次列表「加载更多」可见性依据，FR-002）。"""
    row = db.query_one("SELECT COUNT(DISTINCT batch_id) AS n FROM benchmark_runs")
    return row["n"] if row else 0


def get_batch(batch_id: str) -> dict[str, Any]:
    """查批次状态 + 进度（含 cancelled 计数与终止原因）。"""
    rows = db.query_all(
        "SELECT * FROM benchmark_runs WHERE batch_id=? ORDER BY harness_version, case_id",
        (batch_id,),
    )
    if not rows:
        return {"batch_id": batch_id, "status": "not_found", "results": [], "progress": {}}

    total = len(rows)
    done = sum(1 for r in rows if r["status"] == STATUS_DONE)
    failed = sum(1 for r in rows if r["status"] == STATUS_FAILED)
    cancelled = sum(1 for r in rows if r["status"] == STATUS_CANCELLED)
    stop_reason = get_stop_reason(batch_id)
    status = _derive_batch_status(
        total=total, done=done, failed=failed, cancelled=cancelled,
        stop_reason=stop_reason,
    )

    return {
        "batch_id": batch_id,
        "status": status,
        "progress": {"total": total, "done": done, "failed": failed, "cancelled": cancelled, "active": total - done - failed - cancelled},
        "golden_revision": rows[0]["golden_revision"],
        "concurrency": rows[0].get("concurrency", 1) if "concurrency" in rows[0].keys() else 1,
        "stop_reason": stop_reason,
        "results": [_row_to_dict(r) for r in rows],
    }


def list_batch_runs(batch_id: str) -> dict[str, Any]:
    """批次全行明细（REQ-20260921-114943 FR-001/FR-004，桌面端 case 明细区）。

    行含解析后的完整 scores（scores/tags/reasons/overall/rule_delivery）；
    scores_json 缺失或解析失败降级 None，不阻塞其他行展示。
    前端按 case_id 分组渲染卡片；聚合口径不变（仅 done 行计入报告均分）。
    """
    import json

    rows = db.query_all(
        "SELECT * FROM benchmark_runs WHERE batch_id=? ORDER BY case_id, seed, id",
        (batch_id,),
    )
    items = []
    for row in rows:
        scores = None
        if row.get("scores_json"):
            try:
                parsed = json.loads(row["scores_json"])
                scores = parsed if isinstance(parsed, dict) else None
            except (ValueError, TypeError):
                scores = None
        items.append({
            "id": row["id"],
            "case_id": row["case_id"],
            "seed": row.get("seed"),
            "harness_version": row["harness_version"],
            "status": row["status"],
            "retries": row["retries"],
            "error": row["error"],
            "trace_id": row["trace_id"],
            "rubric_version": row.get("rubric_version"),
            "scores": scores,
            "ran_at": row["ran_at"],
            "finished_at": row["finished_at"],
        })
    return {"batch_id": batch_id, "items": items, "total": len(items)}


def _aggregate_dimension_means(rows: list[dict[str, Any]]) -> dict[str, float]:
    """按维度聚合均分（REQ-20260921-135543 FR-003：趋势视图逐维平均）。

    与 report.py/stats.py 同口径：只收 rubric 五维内的数值分且 > 0
    （0 = 无法判断，不作质量结论，不进均值）。
    损坏/缺失 scores_json 或非 dict/非数值维度分的行整行跳过。
    """
    import json

    from app.benchmark import rubric_v3

    totals: dict[str, list[float]] = {}
    for row in rows:
        raw = row.get("scores_json")
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        scores = parsed.get("scores") if isinstance(parsed, dict) else None
        if not isinstance(scores, dict):
            continue
        for dim, value in scores.items():
            if dim in rubric_v3.DIMENSION_KEYS and isinstance(value, (int, float)) and value > 0:
                totals.setdefault(dim, []).append(float(value))
    return {dim: round(sum(v) / len(v), 4) for dim, v in totals.items()}


def get_leaderboard(golden_revision: str | None = None) -> dict[str, Any]:
    """跨版本 leaderboard（按 golden_revision 过滤）。

    无 golden_revision 取当前锁定的 revision。
    结构：{ revision, versions: [{ version, cases: [...], avg_score,
    dimension_means, done_count }] }
    """
    if golden_revision is None:
        from app.dataset import repo as dataset_repo
        golden_revision = dataset_repo.get_golden_revision() or ""

    rows = db.query_all(
        """SELECT * FROM benchmark_runs
           WHERE golden_revision=? AND status=?
           ORDER BY harness_version DESC, case_id""",
        (golden_revision, STATUS_DONE),
    )
    if not rows:
        return {"revision": golden_revision, "versions": [], "case_count": 0}

    # 按 version 聚合（保留原始行：维度聚合需读 scores_json）
    versions_map: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        ver = row["harness_version"]
        versions_map.setdefault(ver, []).append(row)

    versions = []
    for ver in sorted(versions_map.keys(), reverse=True):
        raw_rows = versions_map[ver]
        cases = [_row_to_dict(r) for r in raw_rows]
        scores = [c["scores_avg"] for c in cases if c["scores_avg"] is not None]
        versions.append({
            "version": ver,
            "case_count": len(cases),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
            "dimension_means": _aggregate_dimension_means(raw_rows),
            "cases": cases,
        })

    return {
        "revision": golden_revision,
        "versions": versions,
        "case_count": len(versions_map.get(versions[0]["version"], [])) if versions else 0,
    }


def get_recent_versions(k: int = 3) -> list[int]:
    """取最近 K 个版本号（golden 升级重跑用，D18/D20）。从 registry.json。"""
    from app.versioning.registry_repo import list_versions
    versions = list_versions()
    return [v["version"] for v in versions[:k]]


# ── 辅助 ────────────────────────────────────────────────────


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    import json
    scores_avg = None
    if row.get("scores_json"):
        try:
            scores = json.loads(row["scores_json"])
            # 合法 JSON 但非 dict（如 "null"）时跳过——与维度聚合同一防御口径
            scores_avg = scores.get("overall") if isinstance(scores, dict) else None
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": row["id"],
        "batch_id": row["batch_id"],
        "case_id": row["case_id"],
        "harness_version": row["harness_version"],
        "golden_revision": row["golden_revision"],
        "trace_id": row["trace_id"],
        "eval_id": row["eval_id"],
        "status": row["status"],
        "retries": row["retries"],
        "error": row["error"],
        "scores_avg": scores_avg,
        "ran_at": row["ran_at"],
        "finished_at": row["finished_at"],
        "seed": row.get("seed"),
        "rubric_version": row.get("rubric_version"),
        "judge_fp": row.get("judge_fp"),
        "model_fp": row.get("model_fp"),
        "manifest_fp": row.get("manifest_fp"),
        "harness_commit": row.get("harness_commit"),
        "platform_manifest_id": row.get("platform_manifest_id"),
        "concurrency": row.get("concurrency"),
    }


def _get_logger():
    import logging
    return logging.getLogger("evolution.benchmark.repo")


__all__ = [
    "STATUS_PENDING", "STATUS_RUNNING", "STATUS_EVALUATING",
    "STATUS_DONE", "STATUS_FAILED", "STATUS_CANCELLED", "MAX_RETRIES",
    "STOP_USER", "STOP_AUTO_FAIL",
    "create_batch", "set_fingerprints", "claim_next_pending",
    "mark_running", "set_trace",
    "set_result", "mark_failed", "mark_cancelled",
    "stop_batch", "get_stop_reason",
    "get_batch", "get_recent_batches", "count_batches", "get_leaderboard", "get_recent_versions",
    "list_batch_runs",
]
