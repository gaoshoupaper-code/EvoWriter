"""Benchmark Runner — 后台矩阵执行（评测主链路，REQ-20260919-172934）。

对 case × 版本 × seed 笛卡尔积逐个执行：
  1. 调 executor /internal/ab/run（传 demand_md + source_commit）
  2. 轮询 /internal/ab/status 直到 done/failed
  3. 评测评分（scorer：直读 ArtifactRevision + rubric v3 judge，不走卷宗/eval_agent）
  4. 写 benchmark_runs（含四类指纹绑定，DEC-015）

后台线程执行（daemon thread），不阻塞触发 API。
失败自动重试（MAX_RETRIES=3），超过转 failed。

调用 executor 的逻辑与 tests/api 平行（不依赖其私有函数），复用相同的端点契约。
"""
from __future__ import annotations

import json
import logging
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
_POLL_TIMEOUT = 600.0   # 单 case 10 分钟
_POLL_INTERVAL = 5.0

# 每 case 独立重复次数（DEC-013 固定 3 seed）
DEFAULT_SEEDS = 3

# 批次默认并发度（FR-001/DEC-004：可选 1/3/5，默认 3；API 层限制取值，
# runner 层只做下界保护——直接调用方（rerun_golden 等）不受取值集合约束）
DEFAULT_CONCURRENCY = 3

# 评分失败重试次数（FR-002 失败语义：重试 1 次）
_SCORE_MAX_ATTEMPTS = 2


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
    """
    import threading

    workers = max(1, concurrency)
    logger.info("开始执行 benchmark 批次 %s（并发 %d）", batch_id, workers)
    threads = [
        threading.Thread(
            target=_worker_loop, args=(batch_id, judge_config_id),
            daemon=True, name=f"bench-{batch_id[:8]}-{i}",
        )
        for i in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    logger.info("benchmark 批次 %s 执行完毕", batch_id)


def _worker_loop(batch_id: str, judge_config_id: int | None = None) -> None:
    """单 worker 主循环：抢占 → 执行 → 失败记账，直到本批次无 pending。"""
    while True:
        row = bench_repo.claim_next_pending(batch_id)
        if row is None:
            return
        try:
            _execute_one(row, judge_config_id)
        except Exception as exc:
            logger.exception("benchmark 行 %d 执行异常", row["id"])
            bench_repo.mark_failed(row["id"], str(exc))


def _execute_one(row: dict[str, Any], judge_config_id: int | None = None) -> None:
    """执行单行：调 executor → 轮询 → 评测评分 → 回填指纹 → 写结果。"""
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

    # 3. 轮询完成
    trace_id = _poll_until_done(task_id, run_id)
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
    scores = _score_with_retry(demand_md, trace_id, judge_config_id)
    bench_repo.set_result(
        run_id,
        eval_id=None,
        scores_json=json.dumps(scores, ensure_ascii=False) if scores else None,
    )
    logger.info("评测 [%d] 完成: case=%s v=%s overall=%s",
                run_id, case_id, version, scores.get("overall") if scores else "N/A")


# ── executor 调用（与 tests/api 平行，复用端点契约）─────────


def _get_production_version() -> int | None:
    """当前 production 版本号（从 registry.json）。"""
    from app.versioning.registry_repo import get_production_version_number
    return get_production_version_number()


def _get_snapshot(version: int) -> dict[str, Any] | None:
    """版本元数据（从 registry.json）。"""
    from app.versioning.registry_repo import get_version
    return get_version(version)


def _trigger_executor(demand_md: str, snapshot: dict[str, Any]) -> str:
    """调 executor /internal/ab/run，返回 task_id。"""
    from app.versioning.registry_repo import get_version_commit
    payload = {
        "demand_md": demand_md,
        "baseline": False,
        "source_commit": get_version_commit(snapshot["version"]) or "",
    }
    resp = httpx.post(_executor_url("/internal/ab/run"), json=payload, timeout=_EXEC_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["task_id"]


def _poll_until_done(task_id: str, run_id: int) -> str | None:
    """轮询 executor task 直到完成，返回 trace_id。"""
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        try:
            resp = httpx.get(_executor_url(f"/internal/ab/status/{task_id}"), timeout=10.0)
        except Exception:
            continue
        if resp.status_code != 200:
            continue
        data = resp.json()
        trace_ids = data.get("trace_ids", [])
        status = data.get("status", "")

        if status == "done":
            return trace_ids[0] if trace_ids else None
        if status == "failed":
            raise RuntimeError(f"executor task failed: {data.get('error', 'unknown')}")
        if status == "cancelled":
            raise RuntimeError("executor task cancelled")
        # running：继续等

    raise RuntimeError(f"轮询超时（{_POLL_TIMEOUT}s 无结果）")


def _score_with_retry(
    demand_md: str, trace_id: str, judge_config_id: int | None = None,
) -> dict[str, Any] | None:
    """评测评分：直读 ArtifactRevision 三件套 + rubric v3 judge（FR-002/003）。

    解析/校验失败重试 1 次（FR-002 失败语义）；仍失败返回 None（该行转 failed）。
    先等 trace 摄入完成（runs 表出现终态），与旧评估路径同语义。
    """
    # 等 trace 入库（executor done 后 ingestion 异步拉取，可能稍慢）
    for _ in range(20):  # 最多等 60s
        row = db.query_one("SELECT status FROM runs WHERE trace_id=?", (trace_id,))
        if row and row["status"] in ("completed", "failed"):
            break
        time.sleep(3.0)

    deliveries = scorer.load_outline_deliveries(trace_id)
    if not deliveries:
        raise RuntimeError(f"trace {trace_id} 无大纲三件套产物（ArtifactRevision）")

    last_error: Exception | None = None
    for attempt in range(1, _SCORE_MAX_ATTEMPTS + 1):
        try:
            return scorer.score_case(demand_md, deliveries, judge_config_id=judge_config_id)
        except Exception as exc:
            last_error = exc
            logger.warning("评分第 %d 次失败 trace=%s: %s", attempt, trace_id, exc)
    raise RuntimeError(f"评分重试用尽（{_SCORE_MAX_ATTEMPTS} 次）: {last_error}")


__all__ = [
    "trigger_run",
    "trigger_golden_upgrade_rerun",
]
