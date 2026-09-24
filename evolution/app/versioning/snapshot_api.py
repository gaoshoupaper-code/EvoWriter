"""snapshot API（数据源：Platform 账本；registry.json 已冻结退役）。

提供整包级的版本查询 API。version→commit 与版本列表统一走 Platform 账本
（platform_ledger，REQ-20260923-145931）；账本不可达返回 502（宁拒勿错，
不回退冻结 registry——那是错误数据，DEC-005）。

端点（/api/snapshots 前缀）：
  GET  /snapshots                 列版本（按版本倒序，含 status/same_code_as/based_on）
  GET  /snapshots/production      当前 production 版本
  GET  /snapshots/{version}       指定版本元数据
  POST /snapshots/rollback        回滚（Phase A：重新晋升旧 commit，走 Platform）

rollback 注意：目标版本解析仍读冻结 registry（无 UI 消费，REQ-20260923-145931
风险清单记录在案）；对账本新版本（v9+）目标会 404，做回滚 UI 前必须先切账本。

Phase A（REQ-20260919-202344）：registry.json 只读（Platform 账本是仲裁源）。
rollback = 查本地 registry（只读）拿目标 version 的 commit → POST Platform
/api/release/promote 重新晋升旧 commit，不再移动本地 production 指针、不再
直连 executor reload（Platform 全包）。

设计依据：设计文档 20260713_003000（去 DB 轻量化重构）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core import db
from app.trace.facts import append_release_event
from app.versioning import platform_ledger, registry_repo, upgrade_diff

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


class RollbackRequest(BaseModel):
    to_version: int
    reason: str = ""


@router.get("")
def list_snapshots(status: str | None = None) -> list[dict[str, Any]]:
    """列版本（按版本倒序，账本全量不去重 + 观测富化）。

    可按 status 过滤（production/retired）。同 commit 条目带 same_code_as 标注，
    非同代码条目带 based_on（git 最近账本祖先，可能与版本号倒挂）。
    """
    try:
        versions = platform_ledger.list_versions()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if status:
        versions = [v for v in versions if v["status"] == status]
    return versions


@router.get("/production")
def get_production_snapshot() -> dict[str, Any]:
    """当前 production 版本（元数据）。无则 404。"""
    try:
        prod = platform_ledger.production_version_number()
        snap = platform_ledger.get_version(prod) if prod is not None else None
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if snap is None:
        raise HTTPException(status_code=404, detail="无 production 版本")
    return snap


@router.post("/rollback")
def rollback_snapshot(body: RollbackRequest, request: Request) -> dict[str, Any]:
    """回滚 = 重新晋升旧 commit（Phase A）：registry 只读查目标 commit → Platform promote。

    旧写线（移动本地 production 指针 + git 提交 + 直连 executor reload）已退役。
    Platform promote 原子：失败时账本未动，无需旧线的恢复补偿。
    """
    current = registry_repo.get_production_version()
    if current is None:
        raise HTTPException(status_code=409, detail="无 production 版本可回滚")
    if current["version"] == body.to_version:
        raise HTTPException(status_code=409, detail="目标版本已是 production")

    source_candidate_id = f"harness-version-{current['version']}"
    release = db.query_one(
        """SELECT release_id FROM release_events_v2
           WHERE candidate_id=? AND status IN ('activated', 'activation_failed')
           ORDER BY rowid DESC LIMIT 1""",
        (source_candidate_id,),
    )

    from app.versioning.release_gate import ReleasePromoteError, promote_release

    try:
        # 只读校验目标版本可执行（校验口径与旧 registry_repo.rollback 一致）
        target = registry_repo.get_version(body.to_version)
        if target is None:
            raise ValueError(f"版本 v{body.to_version} 不存在于 registry")
        if target.get("promotion_status") == "candidate":
            raise ValueError(
                f"candidate v{body.to_version} 未经发布门禁，不可直接回滚为 production"
            )
        target_commit = target.get("commit_hash")
        if not target_commit:
            raise ValueError(f"版本 v{body.to_version} 缺少不可变 commit 绑定，不可回滚")

        note = f"rollback v{current['version']} -> v{body.to_version}"
        if body.reason:
            note = f"{note}: {body.reason}"
        promoted = promote_release(target_commit, version_note=note)
        if promoted.commit != target_commit:
            raise RuntimeError(
                f"platform promote commit mismatch: {promoted.commit} != {target_commit}"
            )
        if release:
            append_release_event(
                release_id=release["release_id"],
                status="rollback_activated",
                candidate_id=source_candidate_id,
                actor_user_id=getattr(request.state, "user_id", None),
            )
        return {
            "status": "rollback_activated",
            "from_version": current["version"],
            "to_version": target["version"],
            "source_commit": target_commit,
            "platform_version": promoted.version,
            "release_id": release["release_id"] if release else None,
            "release_tracking": "v2" if release else "legacy",
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ReleasePromoteError, RuntimeError) as exc:
        # Platform promote 原子：失败时账本未动，无需旧线的恢复补偿
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"rollback 激活失败（Platform promote）：{exc}",
                "executor_restore_error": None,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"回滚失败：{exc}") from exc


@router.get("/{version}")
def get_snapshot(version: int) -> dict[str, Any]:
    """指定版本元数据（账本富化条目）。不存在则 404，账本不可达 502。"""
    try:
        snap = platform_ledger.get_version(version)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if snap is None:
        raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")
    return snap


@router.get("/{version}/upgrade-diff")
def get_upgrade_diff(version: int) -> dict[str, Any]:
    """升级总览实时 diff（要素变更摘要 + prompt 行级 hunks，FR-003）。

    基线 = 「代码基于」版本（DEC-002/003）；同代码/根/计算失败（含 git 读取
    异常，降级不缓存）→ changes 空 + base_kind 标注，前端显示对应占位，
    不阻断五要素展示。账本不可达 502、版本不存在 404。
    """
    try:
        return upgrade_diff.build_upgrade_diff(version)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
