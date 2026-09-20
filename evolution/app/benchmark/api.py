"""评测 API（REQ-20260919-172934，评测主链路；REQ-20260920-104714 增强）。

端点：
  POST /api/benchmark/run              触发评测批次（手动，含 3 seed 展开；并发度可选，FR-001）
  POST /api/benchmark/rerun-golden     golden 升级后重跑最近 K=3（D8/D18）
  GET  /api/benchmark/rubric           评分标准全文只读（FR-002）
  GET  /api/benchmark/judges           judge 候选列表 + 同源标记（FR-003）
  GET  /api/benchmark/versions         版本下拉数据源（Platform 账本；registry.json 已冻结退役）
  GET  /api/benchmark/leaderboard      跨版本对比（按 golden_revision）
  GET  /api/benchmark/batches/{id}     查批次状态
  GET  /api/benchmark/batches/{id}/report   弱点报告（FR-005）
  POST /api/benchmark/batches/{id}/stop     停止批次（REQ-20260920-192126/FR-001）
  POST /api/benchmark/compare          两批次 CI 三态对比（FR-004）
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.benchmark import repo, runner, stats, report
from app.benchmark.manifest import fetch_platform_versions

logger = logging.getLogger("evolution.benchmark.api")

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


class RunRequest(BaseModel):
    """触发评测请求。"""
    version: int | None = None       # None=当前 production
    versions: list[int] | None = None  # 多版本（优先于 version）
    case_ids: list[str] | None = None  # None=golden 全 case
    seeds: int = runner.DEFAULT_SEEDS  # 每 case 独立重复次数（DEC-013）
    # 批次内并发执行的行数上限（FR-001/DEC-004，AC-001：默认 3）
    concurrency: Literal[1, 3, 5] = runner.DEFAULT_CONCURRENCY
    # 指定 judge 配置（FR-003/DEC-005）；None=默认解析（eval 优先，降级 evolution）
    judge_config_id: int | None = None


@router.post("/run")
def trigger_run(req: RunRequest) -> dict[str, Any]:
    """手动触发评测批次。"""
    versions = req.versions
    if versions is None and req.version is not None:
        versions = [req.version]
    try:
        batch_id = runner.trigger_run(
            versions=versions, case_ids=req.case_ids, seeds=req.seeds,
            concurrency=req.concurrency, judge_config_id=req.judge_config_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        # Platform 账本不可达（默认版本解析）——无版本号可评，快败不给半配置批次
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/versions")
def list_versions() -> dict[str, Any]:
    """版本下拉数据源（Platform 账本；Phase A 后 registry.json 冻结退役）。

    Platform 不可达时 502——前端退化为仅「跟随 production」，不回退冻结
    registry（那是错误数据，宁缺勿错）。
    """
    try:
        data = fetch_platform_versions()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    prod = data["production_version"]
    items = [
        {
            "version": v["version"],
            "status": "production" if v["version"] == prod else "retired",
            "change_summary": v.get("note") or "",
            "commit": v["commit"],
            "created_at": v.get("created_at") or "",
        }
        for v in data["items"]
    ]
    return {"items": items, "production_version": prod, "total": len(items)}

    batch = repo.get_batch(batch_id)
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "progress": batch["progress"],
        "golden_revision": batch["golden_revision"],
    }


class RerunGoldenRequest(BaseModel):
    """golden 升级重跑请求。"""
    k: int = 3


@router.post("/rerun-golden")
def rerun_golden(req: RerunGoldenRequest) -> dict[str, Any]:
    """golden 升级后重跑最近 K 个版本（D8/D18/D20）。"""
    try:
        batch_id = runner.trigger_golden_upgrade_rerun(k=req.k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    batch = repo.get_batch(batch_id)
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "progress": batch["progress"],
        "golden_revision": batch["golden_revision"],
    }


@router.get("/leaderboard")
def get_leaderboard(
    golden_revision: str | None = Query(None, description="golden revision hash；空=当前锁定值"),
) -> dict[str, Any]:
    """跨版本 leaderboard（按 golden_revision 过滤）。"""
    return repo.get_leaderboard(golden_revision)


@router.get("/rubric")
def get_rubric() -> dict[str, Any]:
    """当前评分标准全文（FR-002/DEC-011：只读展示 + 草稿状态明示，AC-003）。

    直接返回 rubric_v3 常量（单一事实源）——前端不硬编码规则内容，
    代码改规则后展示自动跟上（防漂移）。
    """
    from app.benchmark import rubric_v3

    return {
        "rubric_version": rubric_v3.RUBRIC_VERSION,
        "calibration_status": rubric_v3.CALIBRATION_STATUS,
        "anchor_status": rubric_v3.ANCHOR_DRAFT_STATUS,
        "low_score_threshold": rubric_v3.LOW_SCORE_THRESHOLD,
        "dimensions": rubric_v3.DIMENSIONS,
        "rule_delivery": rubric_v3.RULE_DELIVERY_COMPLETE,
    }


@router.get("/judges")
def list_judges() -> dict[str, Any]:
    """judge 候选列表（FR-003/DEC-005/010，触发评测下拉用）。

    候选 = eval + evolution 两 scope 全部配置；排除 executor（生产写作模型，
    判评分离）。每条带 same_family_as_executor 标记（界面黄条告警数据源）。
    default = 未显式选择时的现有解析结果（eval 激活项，降级 evolution）。
    """
    import app.core.db as db

    from app.benchmark import manifest as bench_manifest

    judges = []
    for scope in ("eval", "evolution"):
        for cfg in db.LlmConfigsRepository.list_all(scope):
            judges.append({
                "config_id": cfg["id"],
                "name": cfg["name"],
                "model": cfg["model"],
                "base_url": cfg["base_url"],
                "scope": scope,
                "is_active": bool(cfg["is_active"]),
                "has_key": bool(cfg["has_key"]),
                "same_family_as_executor": bench_manifest.same_family_as_executor(cfg["model"]),
            })

    resolved = bench_manifest.resolve_judge_config()
    default = {
        "scope": resolved["scope"],
        "model": resolved["model"],
        "fingerprint": resolved["fingerprint"],
        "degraded": resolved["degraded"],
    }
    return {"judges": judges, "default": default}


@router.get("/batches")
def list_batches(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """最近批次摘要列表（桌面端评测页）。"""
    return {"batches": repo.get_recent_batches(limit)}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    """查批次状态。"""
    batch = repo.get_batch(batch_id)
    if batch["status"] == "not_found":
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


@router.get("/batches/{batch_id}/report")
def get_batch_report(batch_id: str) -> dict[str, Any]:
    """弱点报告（FR-005：维度均分 + 标签命中 + 低分 case）。"""
    result = report.build_report(batch_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="batch not found")
    return result


@router.post("/batches/{batch_id}/stop")
def stop_batch(batch_id: str) -> dict[str, Any]:
    """停止批次（FR-001：立即停 + 幂等清理，僵尸批次同样可清）。"""
    result = runner.request_stop(batch_id)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="batch not found")
    return result


class CompareRequest(BaseModel):
    """两批次对比请求（FR-004）。

    batch_b 为 production 基线，batch_a 为候选。
    """
    batch_a: str
    batch_b: str


@router.post("/compare")
def compare_batches(req: CompareRequest) -> dict[str, Any]:
    """两批次 CI 三态对比（win/tie/lose + 完整统计量）。"""
    result = stats.compare_batches(req.batch_a, req.batch_b)
    if not result.get("comparable"):
        raise HTTPException(status_code=400, detail=result.get("problems", ["不可比"]))
    return result


__all__ = ["router"]
