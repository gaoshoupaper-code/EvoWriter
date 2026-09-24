"""versions API —— 版本谱系视图（数据源：Platform 账本，REQ-20260923-145931）。

端点（/api/versions 前缀）：
  GET /versions            版本列表（账本全量 + same_code_as/based_on 富化）
  GET /versions/{version}  单版本详情（账本元数据；要素级 diff 见 upgrade-diff 端点）

谱系语义（DEC-001/DEC-002）：
- 账本版本号 = 发版流水号，同 commit 重复条目照单全收，same_code_as 标注最早持有者
- 无 parent_version：based_on = git first-parent 链上最近账本版本（可与版本号倒挂）
- 旧 adapt_rounds（reward/轮出处）富化随数据源切换一并退役——前端早已忽略该字段

账本不可达返回 502（宁拒勿错，不回退冻结 registry，DEC-005）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.versioning import platform_ledger

logger = logging.getLogger("evolution.versions_api")

router = APIRouter(prefix="/versions", tags=["versions"])


@router.get("")
def list_versions(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """版本列表（按版本号倒序，账本富化条目）。账本不可达统一 502。"""
    try:
        versions = platform_ledger.list_versions()
        production = platform_ledger.production_version_number()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "items": versions[offset: offset + limit],
        "total": len(versions),
        "production_version": production,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{version}")
def get_version(version: int) -> dict[str, Any]:
    """单版本详情（账本元数据 + 谱系标注）。不存在则 404，账本不可达 502。"""
    try:
        entry = platform_ledger.get_version(version)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")

    detail = dict(entry)
    detail["is_bootstrap"] = (
        entry["same_code_as"] is None and entry["based_on_status"] == "root"
    )
    return detail


__all__ = ["router"]
