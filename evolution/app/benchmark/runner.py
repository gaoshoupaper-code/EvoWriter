"""Benchmark Runner — 后台矩阵执行（评测主链路，REQ-20260919-172934）。

对 case × 版本 × seed 笛卡尔积逐个执行：
  1. 调 executor /internal/ab/run（传 demand_md + source_commit）
  2. 轮询 /internal/ab/status 直到 done/failed
  3. 评测评分（scorer：直读 ArtifactRevision + rubric v3 judge，不走卷宗/eval_agent）
  4. 写 benchmark_runs（含四类指纹绑定，DEC-015）

后台线程执行（daemon thread），不阻塞触发 API。
失败自动重试（MAX_RETRIES=3），超过转 failed。

批次可控性（REQ-20260920-192126）：
- 手动停止：request_stop 设批次取消事件（worker 在轮询/评分等待点感知，
  中断手中行并尽力叫停 executor 任务）+ repo 清 pending 行，不依赖 worker 存活
- 自动止损：连续 ≥FAIL_STREAK_LIMIT 次尝试失败且涉及 ≥2 个不同行 → 停批
  （单 case 系统性失败仍走行级隔离重试，不误伤整批）
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx

import app.core.db as db
from app.core.settings import settings
from app.common import evalset
from app.dataset import repo as dataset_repo
from app.dataset import revision
from app.benchmark import repo as bench_repo
from app.benchmark import manifest as bench_manifest
from app.benchmark import rubric_v3
from app.benchmark import scorer

logger = logging.getLogger("evolution.benchmark.runner")

# executor 调用配置（与 tests/api 对齐）
_EXEC_TIMEOUT = 30.0
_POLL_INTERVAL = 5.0
# 轮询不设时间上限（DEC-004 修订，2026-09-21）：单 case 生成实测 >18 分钟，
# 固定预算会在 executor 正常干活时把行判死——重试再开一个全新生成，旧任务
# 不停，最多 3 个重叠生成白烧 API。终止条件=executor 终态（done/failed/
# cancelled）或批次手动停止（每轮检查取消事件）；executor 重启后任务表丢失
# 会以 404 快速失败兜底，无死循环风险。

# 每 case 独立重复次数（DEC-013 固定 3 seed）
DEFAULT_SEEDS = 3

# 批次默认并发度（FR-001/DEC-004：可选 1/3/5，默认 3；API 层限制取值，
# runner 层只做下界保护——直接调用方（rerun_golden 等）不受取值集合约束）
DEFAULT_CONCURRENCY = 3

# 评分重试在 score_case 内按维度进行（DEC-013：单维 1+1 次）；
# 行级不再整体重试——任一维重试用尽即整行 failed，error 含维度名。

# 行失败回退 pending 后的 worker 退避（FR-004：避免零间隔立即重抢）
_RETRY_BACKOFF_S = 10.0

# 自动止损阈值（FR-002/DEC-002）：连续失败达此数且涉及 ≥2 个不同 case 才停批。
# 按 case（而非 run 行）去重——同 case 的 3 个 seed 是同一失败模式的三次重复，
# 按 run 计数会让单 case 系统性失败（如 case-001 持续超时）在 seed 期就误停整批。
_FAIL_STREAK_LIMIT = 3


class _BatchCancelled(Exception):
    """worker 感知到批次停止的信号；task_id 携带生成中任务号供 executor 叫停。"""

    def __init__(self, task_id: str | None = None):
        super().__init__("batch cancelled")
        self.task_id = task_id


# 批次取消事件注册表（本进程 worker 的停止通知；API 与 runner 同进程）。
# 僵尸批次（服务重启残留）无注册事件，停止只走 repo 层清理。
_cancel_events: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


class _FailStreak:
    """连续失败计数（FR-002）：任一次尝试成功清零；跨行累计。

    触发条件 = 连续失败次数 ≥ FAIL_STREAK_LIMIT 且失败涉及 ≥2 个不同 case
    （同 case 的多 seed 连败只计 1 个 case，单 case 系统性失败走行级隔离
    重试，不误伤整批；跨 case 连败才视为系统性故障停批）。
    仅 runner 进程内使用，不持久化——进程消亡后批次即僵尸，由停止清理。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streak = 0
        self._cases: set[str] = set()

    def record_success(self) -> None:
        with self._lock:
            self._streak = 0
            self._cases.clear()

    def record_failure(self, case_id: str) -> bool:
        """记一次失败，返回是否应触发止损。"""
        with self._lock:
            self._streak += 1
            self._cases.add(case_id)
            return self._streak >= _FAIL_STREAK_LIMIT and len(self._cases) >= 2


def _check_cancel(cancel_event: threading.Event | None, task_id: str | None = None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise _BatchCancelled(task_id=task_id)


def _executor_url(path: str) -> str:
    return f"{settings.executor_url.rstrip('/')}{path}"


# ── 触发 ────────────────────────────────────────────────────


def trigger_run(
    *,
    versions: list[int] | None = None,
    case_ids: list[str] | None = None,
    seeds: int = DEFAULT_SEEDS,
    concurrency: int = DEFAULT_CONCURRENCY,
    judge_config_id: int | None = None,
) -> str:
    """触发一个评测批次（同步建表，异步执行）。返回 batch_id。

    Args:
        versions: 要跑的版本号列表；None=当前 production 版本
        case_ids: 要跑的 case；None=golden 全 case
        seeds: 每 case 独立重复次数（DEC-013 默认 3）
        concurrency: 批次内并发执行的行数上限（FR-001/DEC-004 默认 3）
        judge_config_id: 指定 judge 配置（FR-003/DEC-005）；None=默认解析
            （eval scope 激活项，降级 evolution）
    """
    # 默认：当前 production 版本
    if versions is None:
        prod = _get_production_version()
        versions = [prod] if prod else []
    if not versions:
        raise ValueError("无可执行的 harness 版本（无 production 快照）")

    # 默认：golden 全 case
    if case_ids is None:
        case_ids = dataset_repo.get_golden_case_ids()
    if not case_ids:
        raise ValueError("golden 集为空，无 case 可跑")

    # golden revision（锁定值或实时计算）
    golden_revision = dataset_repo.get_golden_revision() or revision.compute_golden_revision()

    # 校验 golden 未被篡改
    locked = dataset_repo.get_golden_revision()
    if locked and not revision.verify_golden_intact(locked):
        logger.warning("golden 内容与锁定 revision 不一致（可能被篡改），仍用锁定值跑")

    # 评分配置指纹（DEC-012/015：建批时采集 judge 指纹 + rubric 版本；
    # FR-003：config_id 指定时校验该配置完整，无效即拒——不产生半配置批次）
    judge_cfg = bench_manifest.resolve_judge_config(judge_config_id)
    if judge_cfg["fingerprint"] == "unconfigured":
        if judge_config_id is not None:
            raise ValueError(
                f"所选 judge 配置 #{judge_config_id} 无效（不存在或缺 api_key/base_url/model），"
                "请在「进化端模型」页检查后再试"
            )
        raise ValueError(
            "评测 judge LLM 未配置：请在桌面端「进化端模型」页配置（eval 或 evolution scope）"
        )
    if judge_cfg.get("degraded"):
        logger.warning("eval scope 未配置，judge 降级使用 evolution scope 配置")
    if bench_manifest.same_family_as_executor(judge_cfg["model"]):
        logger.warning(
            "判评分离告警：judge 模型 %s 与 executor 被测模型同家族，"
            "评测存在自我偏好风险（arXiv:2502.01534）", judge_cfg["model"],
        )

    batch_id = bench_repo.create_batch(
        case_ids=case_ids,
        versions=versions,
        golden_revision=golden_revision,
        seeds=seeds,
        rubric_version=rubric_v3.RUBRIC_VERSION,
        judge_fp=judge_cfg["fingerprint"],
        concurrency=concurrency,
    )

    # 后台执行（不阻塞）。直接起 daemon 线程——本函数在 FastAPI sync 端点
    # （线程池 worker）里被调，无 running event loop，asyncio.create_task 会抛
    # RuntimeError（review P0：触发即 500、批次永久卡 running）。
    _dispatch_batch(batch_id, concurrency, judge_config_id)
    logger.info("评测批次 %s 已触发，后台执行（并发 %d）", batch_id, concurrency)
    return batch_id


def _dispatch_batch(batch_id: str, concurrency: int, judge_config_id: int | None) -> None:
    """起后台线程执行批次（trigger_run 唯一的派发出口，测试 seam）。"""
    import threading

    threading.Thread(
        target=_run_batch_sync,
        args=(batch_id, concurrency, judge_config_id),
        daemon=True, name=f"bench-runner-{batch_id[:8]}",
    ).start()


def trigger_golden_upgrade_rerun(k: int = 3) -> str:
    """golden 升级后重跑最近 K 个版本（D8/D18/D20）。"""
    versions = bench_repo.get_recent_versions(k)
    if not versions:
        raise ValueError("无可用快照版本")
    return trigger_run(versions=versions)


# ── 后台执行 ────────────────────────────────────────────────


def _run_batch_sync(
    batch_id: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    judge_config_id: int | None = None,
) -> None:
    """并发执行批次：concurrency 个 worker 各自原子抢占 pending 行直到取空。

    抢占经 claim_next_pending（单条 UPDATE...RETURNING，锁内原子），多 worker
    不会取到同一行；单行失败由 _worker_loop 记 failed/退回重试，不传染其他行。
    judge_config_id 为全批次统一 judge（FR-003），None=评分走默认 scope 解析。
    批次级取消事件与连续失败计数随批次生命周期创建/清理。
    """
    cancel_event = threading.Event()
    with _cancel_lock:
        _cancel_events[batch_id] = cancel_event
    streak = _FailStreak()
    workers = max(1, concurrency)
    logger.info("开始执行 benchmark 批次 %s（并发 %d）", batch_id, workers)
    threads = [
        threading.Thread(
            target=_worker_loop,
            args=(batch_id, judge_config_id, cancel_event, streak),
            daemon=True, name=f"bench-{batch_id[:8]}-{i}",
        )
        for i in range(workers)
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        with _cancel_lock:
            if _cancel_events.get(batch_id) is cancel_event:
                del _cancel_events[batch_id]
    logger.info("benchmark 批次 %s 执行完毕", batch_id)


def _worker_loop(
    batch_id: str,
    judge_config_id: int | None = None,
    cancel_event: threading.Event | None = None,
    streak: _FailStreak | None = None,
) -> None:
    """单 worker 主循环：抢占 → 执行 → 失败记账，直到本批次无 pending 或被停止。"""
    streak = streak if streak is not None else _FailStreak()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return
        row = bench_repo.claim_next_pending(batch_id)
        if row is None:
            return
        try:
            _execute_one(row, judge_config_id, cancel_event=cancel_event)
            streak.record_success()
        except _BatchCancelled as exc:
            # 批次停止：尽力叫停 executor 侧生成任务，手中行转 cancelled 后退出
            if exc.task_id:
                _stop_executor_task(exc.task_id)
            bench_repo.mark_cancelled(row["id"])
            logger.info("benchmark 行 %d 因批次停止转 cancelled", row["id"])
            return
        except Exception as exc:
            logger.exception("benchmark 行 %d 执行异常", row["id"])
            bench_repo.mark_failed(row["id"], str(exc))
            if streak.record_failure(row["case_id"]):
                logger.warning(
                    "批次 %s 连续 %d 次失败（涉及 ≥2 行），触发自动止损",
                    batch_id, _FAIL_STREAK_LIMIT,
                )
                _halt_batch(batch_id, cancel_event)
                return
            time.sleep(_RETRY_BACKOFF_S)


def _halt_batch(batch_id: str, cancel_event: threading.Event | None) -> None:
    """停批（止损路径）：清 pending 行 + 落终止原因 + 通知全部 worker。"""
    if cancel_event is not None:
        cancel_event.set()
    bench_repo.stop_batch(batch_id, reason=bench_repo.STOP_AUTO_FAIL)


def request_stop(batch_id: str) -> dict[str, Any]:
    """停止批次（API 入口，FR-001）：先通知存活 worker，再清 pending 行。

    顺序保证竞态安全：事件先 set，worker 迟到的状态写入被 repo 终态守卫拦下。
    幂等：重复调用返回当前批次状态；僵尸批次（无注册事件）只走 repo 清理。
    """
    with _cancel_lock:
        event = _cancel_events.get(batch_id)
    if event is not None:
        event.set()
    result = bench_repo.stop_batch(batch_id, reason=bench_repo.STOP_USER)
    logger.info("批次 %s 收到停止请求（user_stop）", batch_id)
    return result


def _stop_executor_task(task_id: str) -> None:
    """尽力叫停 executor 侧生成任务（FR-001 失败语义：调用失败不阻塞行取消）。"""
    try:
        httpx.post(_executor_url(f"/internal/ab/stop/{task_id}"), timeout=10.0)
    except Exception:
        logger.warning("executor stop 调用失败（task=%s），生成侧任务将自行结束", task_id)


def _execute_one(
    row: dict[str, Any],
    judge_config_id: int | None = None,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    """执行单行：调 executor → 轮询 → 评测评分 → 回填指纹 → 写结果。

    轮询与评分等待点检查批次取消（_BatchCancelled 上抛由 worker 收尾）；
    行状态写入均有终态守卫，取消后迟到写入不会覆盖 cancelled。
    """
    run_id = row["id"]
    case_id = row["case_id"]
    version = row["harness_version"]

    bench_repo.mark_running(run_id)
    logger.info("评测 [%d] case=%s version=%s seed=%s", run_id, case_id, version, row.get("seed"))

    # 1. 取 demand_md + 版本快照
    demand_md = evalset.load_case_demand(case_id, layer="golden")
    snapshot = _get_snapshot(version)
    if snapshot is None:
        raise RuntimeError(f"harness v{version} 快照不存在")

    # 2. 调 executor（传 commit 供隔离装配；实际身份以 Platform 绑定回填为准）
    task_id = _trigger_executor(demand_md, snapshot)

    # 3. 轮询完成（失败时尽力叫停 executor 生成任务，不白烧 API；
    #    _BatchCancelled 走 worker 收尾的 task_id 叫停语义，不在此重复停）
    _check_cancel(cancel_event, task_id=task_id)
    try:
        trace_id = _poll_until_done(task_id, run_id, cancel_event=cancel_event)
    except _BatchCancelled:
        raise
    except Exception:
        _stop_executor_task(task_id)
        raise
    if not trace_id:
        raise RuntimeError(f"executor task {task_id} 无 trace_id")

    bench_repo.set_trace(run_id, trace_id)

    # 4. 回填指纹：查 Platform Run 绑定（DEC-015 对齐——ab_run 已补签，
    #    run_purpose=optimization）。fail-static：查不到记 unbound，不阻塞评分。
    binding = bench_manifest.fetch_platform_binding(trace_id)
    if binding is not None:
        bench_repo.set_fingerprints(
            run_id,
            harness_commit=binding.get("harness_commit"),
            model_fp=bench_manifest.llm_snapshot_fingerprint(binding.get("llm_config")),
            manifest_fp=bench_manifest.binding_manifest_fingerprint(binding),
            platform_manifest_id=binding.get("manifest_id"),
        )
    else:
        bench_repo.set_fingerprints(
            run_id, harness_commit=None, model_fp=None,
            manifest_fp=bench_manifest.UNBOUND, platform_manifest_id=None,
        )
        logger.warning("评测行 %d trace=%s 无 Platform 绑定，manifest 记 unbound", run_id, trace_id)

    # 5. 评测评分（等 trace 摄入完成后再评；judge_config_id 为本批次统一 judge，FR-003）
    _check_cancel(cancel_event)
    scores = _score_with_retry(demand_md, trace_id, judge_config_id, cancel_event=cancel_event)
    bench_repo.set_result(
        run_id,
        eval_id=None,
        scores_json=json.dumps(scores, ensure_ascii=False) if scores else None,
    )
    logger.info("评测 [%d] 完成: case=%s v=%s overall=%s",
                run_id, case_id, version, scores.get("overall") if scores else "N/A")

    # 6. 开销统计回填（FR-006）：trace 已入库（评分等待点保证），聚合 nodes/runs。
    #    失败语义：记 NULL 不影响已写入的质量评分（fail-open，开销是参考数据）。
    try:
        cost = _aggregate_run_cost(trace_id)
        bench_repo.set_cost(run_id, **cost)
    except Exception:
        logger.warning("评测行 %d 开销聚合失败（记 NULL）", run_id, exc_info=True)
        bench_repo.set_cost(
            run_id, input_tokens=None, output_tokens=None,
            llm_calls=None, wall_clock_ms=None,
        )


def _aggregate_run_cost(trace_id: str) -> dict[str, int | None]:
    """单次运行开销聚合：token（nodes.usage_*）+ LLM 调用数 + 墙钟（runs.duration_ms）。

    usage 由供应商返回，缺失时列为 NULL（COALESCE 0 求和，计数不受影响）。
    """
    row = db.query_one(
        """SELECT
             COALESCE(SUM(n.usage_input), 0)  AS input_tokens,
             COALESCE(SUM(n.usage_output), 0) AS output_tokens,
             SUM(CASE WHEN n.kind='llm' THEN 1 ELSE 0 END) AS llm_calls
           FROM nodes n WHERE n.trace_id=?""",
        (trace_id,),
    )
    run_row = db.query_one(
        "SELECT duration_ms FROM runs WHERE trace_id=?", (trace_id,),
    )
    return {
        "input_tokens": row["input_tokens"] if row else None,
        "output_tokens": row["output_tokens"] if row else None,
        "llm_calls": row["llm_calls"] if row else None,
        "wall_clock_ms": run_row["duration_ms"] if run_row else None,
    }


# ── executor 调用（与 tests/api 平行，复用端点契约）─────────


def _get_production_version() -> int | None:
    """当前 production 版本号（Platform 账本；registry.json 已冻结退役）。"""
    data = bench_manifest.fetch_platform_versions()
    return data["production_version"]


def _get_snapshot(version: int) -> dict[str, Any] | None:
    """版本元数据（Platform 账本流水；registry.json 已冻结退役）。"""
    data = bench_manifest.fetch_platform_versions()
    for item in data["items"]:
        if item["version"] == version:
            return item
    return None


def _trigger_executor(demand_md: str, snapshot: dict[str, Any]) -> str:
    """调 executor /internal/ab/run，返回 task_id。"""
    payload = {
        "demand_md": demand_md,
        "baseline": False,
        "source_commit": snapshot["commit"] or "",
    }
    resp = httpx.post(_executor_url("/internal/ab/run"), json=payload, timeout=_EXEC_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["task_id"]


def _poll_until_done(
    task_id: str,
    run_id: int,
    cancel_event: threading.Event | None = None,
) -> str | None:
    """轮询 executor task 直到完成，返回 trace_id（不设时间上限，见模块配置注释）。

    4xx 快速失败（FR-004：任务不存在/无效秒级判败）；5xx 与网络错误维持退避
    重试。每轮检查批次取消（FR-001）。
    """
    while True:
        time.sleep(_POLL_INTERVAL)
        _check_cancel(cancel_event, task_id=task_id)
        try:
            resp = httpx.get(_executor_url(f"/internal/ab/status/{task_id}"), timeout=10.0)
        except Exception:
            continue
        if 400 <= resp.status_code < 500:
            raise RuntimeError(
                f"executor task {task_id} 轮询收到 {resp.status_code}（任务缺失或无效），快速失败"
            )
        if resp.status_code != 200:
            continue
        data = resp.json()
        trace_ids = data.get("trace_ids", [])
        status = data.get("status", "")

        if status == "done":
            return trace_ids[0] if trace_ids else None
        if status == "running":
            continue
        if status == "failed":
            raise RuntimeError(f"executor task failed: {data.get('error', 'unknown')}")
        if status == "cancelled":
            raise RuntimeError("executor task cancelled")
        # 未知终态（如 evidence_capture_failed 及未来新增）按失败上抛，
        # 不得当 running 死等——DEC-004 取消轮询上限后，漏认终态会让行
        # 永久卡 running（线上批次 12494f65 实际踩中）。
        raise RuntimeError(
            f"executor task 终止于未知状态 {status}: {data.get('error', 'unknown')}"
        )



def _score_with_retry(
    demand_md: str, trace_id: str, judge_config_id: int | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any] | None:
    """评测评分：直读 ArtifactRevision 三件套 + rubric v4 按维 judge（FR-002/003）。

    重试语义（DEC-013）：单维失败在 score_case 内重试该维 1 次；任一维重试
    用尽即整行上抛（DimensionScoreError，message 含维度名 → 行 failed）。
    先等 trace 摄入完成（runs 表出现终态），再等产物事件数稳定（连续两轮
    轮询 artifact_revision 计数不变）——大 trace（多 Agent 连续增量 138+
    产物事件）的 ingestion 分批搬运慢于 runs 终态写入，只等终态会在半截
    产物上评分（pilot 批次 8d0c198e 行 130/131 实测：评分时仅 2/7 条线入库）；
    等待与评分前检查批次取消（FR-001 硬停语义：评分结果不再等待/写回）。
    """
    # 等 trace 入库（executor done 后 ingestion 异步拉取，可能稍慢）
    for _ in range(20):  # 最多等 60s
        row = db.query_one("SELECT status FROM runs WHERE trace_id=?", (trace_id,))
        if row and row["status"] in ("completed", "failed"):
            break
        _check_cancel(cancel_event)
        time.sleep(3.0)

    # 等产物事件稳定（REQ-20260922-162823 pilot 修复）：连续两轮计数不变才评分。
    # 上限 90s：终态后 ingestion 正常秒级补齐；超时按当前数据评（不因摄入卡死阻塞批次）。
    def _artifact_count() -> int:
        r = db.query_one(
            "SELECT COUNT(*) AS c FROM event_payloads WHERE trace_id=? AND type='artifact_revision'",
            (trace_id,),
        )
        return r["c"] if r else 0

    prev_count, stable_rounds = -1, 0
    for _ in range(30):  # 30 × 3s = 90s 上限
        _check_cancel(cancel_event)
        count = _artifact_count()
        if count > 0 and count == prev_count:
            stable_rounds += 1
            if stable_rounds >= 2:  # 连续两轮（6s）不变视为稳定
                break
        else:
            stable_rounds = 0
        prev_count = count
        time.sleep(3.0)

    deliveries = scorer.load_outline_deliveries(trace_id)
    if not deliveries:
        raise RuntimeError(f"trace {trace_id} 无大纲三件套产物（ArtifactRevision）")

    _check_cancel(cancel_event)
    return scorer.score_case(demand_md, deliveries, judge_config_id=judge_config_id)


__all__ = [
    "trigger_run",
    "trigger_golden_upgrade_rerun",
    "request_stop",
]
