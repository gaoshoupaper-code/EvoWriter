"""snapshot API（去 DB 重构：数据源从 harness_snapshots 表 → registry.json）。

提供整包级的版本查询 API。数据源是 harness 独立仓库内的 registry.json
（版本注册表：版本列表 / 谱系 / production 指针）。

端点（/api/snapshots 前缀）：
  GET  /snapshots                 列版本（按版本倒序，含 status）
  GET  /snapshots/production      当前 production 版本
  GET  /snapshots/{version}       指定版本元数据
  POST /snapshots/rollback        回滚（Phase A：重新晋升旧 commit，走 Platform）

版本内容（源码文件）不在本端点返回——通过 /snapshots/{version}/elements 取
（elements_api 从 git 读取真实源文件）。

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
from app.versioning import registry_repo
from app.trace.facts import append_release_event

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


class RollbackRequest(BaseModel):
    to_version: int
    reason: str = ""


@router.get("")
def list_snapshots(status: str | None = None) -> list[dict[str, Any]]:
    """列版本（按版本倒序）。可按 status 过滤（production/retired）。"""
    versions = registry_repo.list_versions()
    if status:
        versions = [v for v in versions if v["status"] == status]
    return versions


@router.get("/production")
def get_production_snapshot() -> dict[str, Any]:
    """当前 production 版本（元数据）。无则 404。"""
    snap = registry_repo.get_production_version()
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
    """指定版本元数据。不存在则 404。"""
    snap = registry_repo.get_version(version)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")
    return snap
