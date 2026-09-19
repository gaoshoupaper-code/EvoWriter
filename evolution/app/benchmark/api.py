"""评测 API（REQ-20260919-172934，评测主链路）。

端点：
  POST /api/benchmark/run              触发评测批次（手动，含 3 seed 展开）
  POST /api/benchmark/rerun-golden     golden 升级后重跑最近 K=3（D8/D18）
  GET  /api/benchmark/leaderboard      跨版本对比（按 golden_revision）
  GET  /api/benchmark/batches/{id}     查批次状态
  GET  /api/benchmark/batches/{id}/report   弱点报告（FR-005）
  POST /api/benchmark/compare          两批次 CI 三态对比（FR-004）
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.benchmark import repo, runner, stats, report

logger = logging.getLogger("evolution.benchmark.api")

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


class RunRequest(BaseModel):
    """触发评测请求。"""
    version: int | None = None       # None=当前 production
    versions: list[int] | None = None  # 多版本（优先于 version）
    case_ids: list[str] | None = None  # None=golden 全 case
    seeds: int = runner.DEFAULT_SEEDS  # 每 case 独立重复次数（DEC-013）


@router.post("/run")
def trigger_run(req: RunRequest) -> dict[str, Any]:
    """手动触发评测批次。"""
    versions = req.versions
    if versions is None and req.version is not None:
        versions = [req.version]
    try:
        batch_id = runner.trigger_run(
            versions=versions, case_ids=req.case_ids, seeds=req.seeds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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
