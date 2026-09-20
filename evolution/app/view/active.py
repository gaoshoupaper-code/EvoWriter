"""活跃 trace 轮询（Phase 6 T15）。

定期轮询执行端 /internal/active-runs，缓存到内存（不存 DB，D22 进行中不入库）。
页面读内存缓存展示活跃大盘。

设计依据：T15（轮询拉取，只展示不存储）+ D21（只看活跃大盘）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter

from app.core.settings import settings

logger = logging.getLogger("evolution.active")

router = APIRouter(tags=["active"])

# 执行端 internal 接点（拉活跃 trace）。
# 执行端地址优先用 evolution 配置里的 executor_url，否则默认 localhost:8000。
# REQ-20260920-193428 FR-001：1s 轮询 + diff 产生 SSE 事件（5s 延迟预算的主通路）。
_POLL_INTERVAL = 1.0

# 内存缓存（进程级，重启丢失——无妨，活跃大盘是实时观测，不需持久）。
_active_cache: list[dict[str, Any]] = []
_task: asyncio.Task | None = None

# 最近一次成功轮询的列表（review R2：失败只清展示缓存，diff 基线保留——
# 否则 executor 宕机窗口内结束的运行在恢复后无从对比，幽灵行悬挂）。
_last_good_runs: list[dict[str, Any]] = []

# 已发过 run_finished 的 trace_id（notify 主路径与 poller 兜底去重）。
# 有界：超过上限整批清理（极端量级下允许一次重复事件，前端按行幂等处理）。
_finished_emitted: set[str] = set()
_FINISHED_EMITTED_LIMIT = 1000


def start_active_poller() -> None:
    """启动轮询后台任务（幂等）。在 lifespan 启动时调用。"""
    global _task
    executor_url = getattr(settings, "executor_url", "") or "http://localhost:8000"
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop(executor_url))


def get_active_runs() -> list[dict[str, Any]]:
    """读取缓存的活跃 trace列表（页面用）。"""
    return list(_active_cache)


async def _poll_loop(executor_url: str) -> None:
    """周期轮询执行端活跃 trace。失败静默（执行端不可用不影响 evolution）。"""
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            await asyncio.to_thread(_poll_once, executor_url)
        except Exception:
            logger.debug("活跃 trace 轮询失败", exc_info=True)


def _poll_once(executor_url: str) -> None:
    """轮询一次执行端 /internal/active-runs，diff 出增删并发布 SSE 事件。"""
    global _active_cache, _last_good_runs
    try:
        import httpx

        url = f"{executor_url.rstrip('/')}/internal/active-runs"
        resp = httpx.get(url, timeout=3.0)
        resp.raise_for_status()
        runs = resp.json()
    except Exception:
        # 执行端不可用 → 清空缓存（活跃大盘显示空，不报错）。
        # 不发 run_finished：来源不可达 ≠ 运行结束（FR-004 失败语义，
        # 误发会把大盘行提前踢进近期区）。
        _active_cache = []
        return
    _emit_diff_events(_last_good_runs, runs)
    _last_good_runs = runs
    _active_cache = runs


def _mark_finished_published(trace_id: str) -> None:
    """登记某 trace 的 run_finished 已发（notify 主路径调用，poller 兜底跳过）。"""
    if len(_finished_emitted) >= _FINISHED_EMITTED_LIMIT:
        _finished_emitted.clear()
    _finished_emitted.add(trace_id)


def _emit_diff_events(prev: list[dict[str, Any]], current: list[dict[str, Any]]) -> None:
    """对比前后两轮 executor 活跃列表，发布 run_started / run_finished。

    run_started 携带运行中元数据（executor 侧已透传 session_name/workload 等，
    FR-003）；run_finished 是兜底路径——executor 终态 notify（ingestion 主路径）
    丢失时，活跃列表消失在 1 个轮询周期内补发，不带终态摘要（run=None），
    前端把行移出活跃区，近期区数据靠入库后的列表查询补齐。
    """
    from app.view.events import get_event_bus

    bus = get_event_bus()
    prev_ids = {r.get("trace_id") for r in prev if r.get("trace_id")}
    current_ids = {r.get("trace_id") for r in current if r.get("trace_id")}
    for row in current:
        tid = row.get("trace_id")
        if tid and tid not in prev_ids:
            bus.publish("run_started", {"trace_id": tid, "source": "executor", "run": row})
    for tid in prev_ids - current_ids:
        if tid in _finished_emitted:
            continue
        _mark_finished_published(tid)
        bus.publish("run_finished", {"trace_id": tid, "source": "executor", "run": None})


def _reset_diff_state_for_test() -> None:
    """测试隔离：清空 diff 状态（不影响生产路径）。"""
    global _active_cache
    _active_cache = []
    _last_good_runs.clear()
    _finished_emitted.clear()


# ── D7：富化 JSON 端点（供监测前端轮询）──


@router.get("/active-runs")
def active_runs_api() -> list[dict[str, Any]]:
    """活跃 trace 富化列表（D7 + D9）。

    数据源合并：
    - executor 活跃 trace（轮询缓存）：创作端正在跑的 trace
    - evolution recorder 活跃 trace（D9 新增）：进化端正在跑的评估/进化 trace

    join evolution.db runs 表补 session_name + run_purpose + ingested 标记。
    未摄入的活跃 trace join 不到 → session_name/run_purpose=null 降级，ingested=false。
    """
    # ── 合并两个数据源 ──
    runs = get_active_runs()  # executor 活跃 trace（轮询缓存）

    # D9：合并 evolution recorder 自己的活跃 trace
    evo_runs = _get_evolution_active_runs()
    all_trace_ids = {r.get("trace_id", "") for r in runs if r.get("trace_id")}
    for er in evo_runs:
        if er.get("trace_id") and er["trace_id"] not in all_trace_ids:
            runs.append(er)
            all_trace_ids.add(er["trace_id"])

    if not runs:
        return []

    # 批量查 evolution.db，一次拿全部活跃 trace_id 的 session_name + run_purpose
    import app.core.db as db

    trace_ids = [r.get("trace_id", "") for r in runs if r.get("trace_id")]
    enriched: list[dict[str, Any]] = []
    if trace_ids:
        placeholders = ",".join("?" * len(trace_ids))
        rows = db.query_all(
            f"""SELECT r.trace_id, r.session_name, r.run_purpose, r.workload,
                       r.integrity_status, r.coverage_json,
                       (SELECT COUNT(*) FROM event_payloads e
                        WHERE e.trace_id=r.trace_id AND e.type='skill_activation')
                           AS skill_activation_count,
                       (SELECT COUNT(*) FROM event_payloads e
                        WHERE e.trace_id=r.trace_id AND e.type='middleware_intervention')
                           AS middleware_intervention_count,
                       (SELECT COUNT(*) FROM event_payloads e
                        WHERE e.trace_id=r.trace_id AND e.type='hitl') AS hitl_count
                FROM runs r WHERE r.trace_id IN ({placeholders})""",
            tuple(trace_ids),
        )
    else:
        rows = []
    ingested_map = {
        r["trace_id"]: {
            "session_name": r.get("session_name"),
            "run_purpose": r.get("run_purpose"),
            "workload": r.get("workload"),
            "integrity_status": r.get("integrity_status"),
            "coverage": json.loads(r.get("coverage_json") or "{}"),
            "skill_activation_count": int(r.get("skill_activation_count") or 0),
            "middleware_intervention_count": int(r.get("middleware_intervention_count") or 0),
            "hitl_count": int(r.get("hitl_count") or 0),
        }
        for r in rows
    }

    for r in runs:
        tid = r.get("trace_id", "")
        meta = ingested_map.get(tid, {})
        enriched.append({
            "trace_id": tid,
            "workspace_id": r.get("workspace_id", ""),
            "thread_id": r.get("thread_id"),
            "endpoint": r.get("endpoint"),
            "status": r.get("status", "running"),
            "started_at": r.get("started_at"),
            "duration_ms": r.get("duration_ms"),
            "event_count": r.get("event_count", 0),
            # D7 富化：优先 executor 运行中透传的元数据（FR-003，不等入库 join），
            # 透传缺失时回退库 join（evolution 源 create_run 即写库）。
            "session_name": r.get("session_name") or meta.get("session_name"),
            "service": r.get("service") or "evolution",
            "ingested": tid in ingested_map,
            # D9：run_purpose（executor trace 优先用 r 自带的，join 不到时降级）
            # evolution recorder 的活跃 trace 已自带 run_purpose（recorder.list_active_runs 返回）
            "run_purpose": r.get("run_purpose") or meta.get("run_purpose") or "user_generation",
            "workload": r.get("workload") or meta.get("workload"),
            "integrity_status": r.get("integrity_status") or meta.get("integrity_status") or "legacy",
            "coverage": r.get("coverage") or meta.get("coverage") or {},
            "skill_activation_count": r.get("skill_activation_count") or meta.get("skill_activation_count") or 0,
            "middleware_intervention_count": r.get("middleware_intervention_count") or meta.get("middleware_intervention_count") or 0,
            "hitl_count": r.get("hitl_count") or meta.get("hitl_count") or 0,
        })
    return enriched


def _get_evolution_active_runs() -> list[dict[str, Any]]:
    """获取 evolution recorder 自己的活跃 trace（D9）。

    evolution 端的评估/进化 agent 运行时，trace 由 EvolutionTraceRecorder 记录，
    不经过 executor 的 active-runs 轮询。这里从 app.state.trace_recorder 取内存中活跃列表。
    """
    try:
        from app.main import app
        recorder = getattr(app.state, "trace_recorder", None)
        if recorder is None:
            return []
        return recorder.list_active_runs()
    except Exception:
        return []
