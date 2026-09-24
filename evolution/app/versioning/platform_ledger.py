"""platform_ledger —— Platform 账本只读访问层（系统资产页数据源）。

Phase A（REQ-20260919-202344）后 registry.json 发版写线退役、内容冻结，
版本的唯一活跃数据源是 Platform 账本（GET {platform_url}/api/versions）。
系统资产两页（Harness 要素 / 版本谱系）与要素视图的 version→commit 解析
统一经本模块读取账本，不再读冻结 registry（REQ-20260923-145931）。

账本语义（与评测页 fetch_platform_versions 一致，另做观测富化）：
- 账本版本号 = 发版流水号，非内容版本：同 commit 可出现多次
  （重复晋升 / 回滚演练），照单全收不去重（DEC-001）
- same_code_as：与更早版本同 commit 时，标注最早持有该 commit 的版本号
- based_on：该 commit 在 git first-parent 祖先链上最近的账本版本
  （「代码基于」）。git 血缘与版本号顺序可能倒挂（实测 v13 代码基于 v14），
  照实解析不排序（DEC-002）；based_on_status 区分 resolved/root/error
- 不可达 / 非 200 / 响应畸形 → RuntimeError（调用方转 502，宁拒勿错不回退
  冻结 registry，DEC-005）

进程内 TTL 缓存：要素/源码/diff 三类端点同版本切换会连续解析 commit，
窗口内共用一次账本拉取；新版本号由 TTL 过期后自然可见。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.settings import settings

logger = logging.getLogger("evolution.platform_ledger")

_LEDGER_TTL_S = 10.0
_FETCH_TIMEOUT_S = 5.0

_ledger_cache: tuple[float, dict[str, Any]] | None = None

# commit → first-parent 祖先链 memo：commit 不可变 → rev-list 结果恒定，
# 进程内永久 memo（review r1：TTL 过期后富化不重 fork git，命中 0 子进程）。
_ancestry_memo: dict[str, list[str]] = {}


def clear_cache() -> None:
    """清空账本缓存与祖先 memo（测试用）。"""
    global _ledger_cache
    _ledger_cache = None
    _ancestry_memo.clear()


def fetch_ledger() -> dict[str, Any]:
    """拉 Platform 账本版本流水（校验后返回原始结构）。

    Returns:
        {"items": [{version, commit, note, created_at}...]（账本原序）,
         "production_version": int | None}

    Raises:
        RuntimeError: Platform 不可达 / 非 200 / 响应畸形。
    """
    global _ledger_cache
    if _ledger_cache and (time.monotonic() - _ledger_cache[0]) < _LEDGER_TTL_S:
        return _ledger_cache[1]

    url = f"{settings.platform_url.rstrip('/')}/api/versions"
    try:
        resp = httpx.get(url, timeout=_FETCH_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Platform 账本不可达：{exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"Platform 账本查询异常：HTTP {resp.status_code}")
    try:
        data = resp.json()
        items = data["items"]
        if not isinstance(items, list) or not all(
            isinstance(v, dict) and isinstance(v.get("version"), int)
            and isinstance(v.get("commit"), str)
            for v in items
        ):
            raise ValueError(f"账本版本列表畸形: {data!r}")
        production = data.get("production_version")
        if production is not None and not isinstance(production, int):
            raise ValueError(f"production_version 畸形: {production!r}")
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Platform 账本响应解析失败：{exc}") from exc

    _ledger_cache = (time.monotonic(), data)
    return data


def _git_rev_list(commit: str) -> list[str]:
    """某 commit 的 first-parent 祖先链（含自身，新→旧）。失败 raise。

    commit 不可变 → 按 commit 永久 memo，重复富化不再 fork git。
    """
    memo = _ancestry_memo.get(commit)
    if memo is not None:
        return memo
    from app.core.git_ops import _git, read_dir
    out = _git(["rev-list", "--first-parent", commit], read_dir())
    ancestry = out.split()
    _ancestry_memo[commit] = ancestry
    return ancestry


def _earliest_version_per_commit(items: list[dict[str, Any]]) -> dict[str, int]:
    """commit → 最早持有该 commit 的版本号（same_code / based_on 共用基准）。"""
    mapping: dict[str, int] = {}
    for entry in sorted(items, key=lambda v: v["version"]):
        commit = entry["commit"]
        if commit not in mapping:
            mapping[commit] = entry["version"]
    return mapping


def _same_code_map(items: list[dict[str, Any]]) -> dict[int, int]:
    """version → 同 commit 更早版本号（无则缺省）。

    基准 = 最早持有者：v9/v11（同 v8 commit）→ 8；v8 自身不标注。
    """
    earliest = _earliest_version_per_commit(items)
    result: dict[int, int] = {}
    for entry in items:
        base = earliest.get(entry["commit"])
        if base is not None and base != entry["version"]:
            result[entry["version"]] = base
    return result


def _resolve_based_on(
    commit: str,
    earliest: dict[str, int],
) -> tuple[int | None, str]:
    """解析「代码基于」：祖先链上最近账本版本。

    同 commit 条目不参与（same_code_as 已表达，based_on 置 None + root 占位）。
    Returns:
        (based_on, status)：status ∈ resolved | root | error
    """
    try:
        ancestry = _git_rev_list(commit)
    except Exception:  # noqa: BLE001
        logger.warning("based_on 解析失败: commit=%s", commit, exc_info=True)
        return None, "error"
    for ancestor in ancestry:
        if ancestor == commit:
            continue
        hit = earliest.get(ancestor)
        if hit is not None:
            return hit, "resolved"
    return None, "root"


def list_versions() -> list[dict[str, Any]]:
    """列全部账本版本（倒序 + status + same_code_as + based_on 富化）。"""
    data = fetch_ledger()
    items = data["items"]
    prod = data.get("production_version")
    same_code = _same_code_map(items)
    earliest = _earliest_version_per_commit(items)

    versions = []
    for entry in sorted(items, key=lambda v: v["version"], reverse=True):
        same = same_code.get(entry["version"])
        if same is not None:
            based_on, status = None, "root"
        else:
            based_on, status = _resolve_based_on(entry["commit"], earliest)
        versions.append({
            "version": entry["version"],
            "status": "production" if entry["version"] == prod else "retired",
            "change_summary": entry.get("note") or "",
            "created_at": entry.get("created_at") or "",
            "commit": entry["commit"],
            "same_code_as": same,
            "based_on": based_on,
            "based_on_status": status,
        })
    return versions


def get_version(version: int) -> dict[str, Any] | None:
    """取指定账本版本（富化条目）。不存在返回 None。"""
    for entry in list_versions():
        if entry["version"] == version:
            return entry
    return None


def production_version_number() -> int | None:
    """当前 production 版本号（账本指针）。"""
    return fetch_ledger().get("production_version")


def resolve_commit(version: int) -> str | None:
    """版本 → 账本 commit（要素视图/源码/diff 共用）。不存在返回 None。"""
    data = fetch_ledger()
    for entry in data["items"]:
        if entry["version"] == version:
            return entry["commit"]
    return None


__all__ = [
    "fetch_ledger",
    "list_versions",
    "get_version",
    "production_version_number",
    "resolve_commit",
    "clear_cache",
]
