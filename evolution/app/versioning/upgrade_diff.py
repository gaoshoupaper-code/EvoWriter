"""upgrade_diff —— 升级总览实时 diff（REQ-20260923-145931 FR-003 / DEC-003）。

在请求时从两个 commit 的 git 快照现算要素变更，不依赖已废弃的
version_changes 预计算管道。基线规则（DEC-003）：
- 同代码版本（same_code_as 非空）→ 基线即自身，changes 为空（前端显示「无差异」）
- 否则基线 = 「代码基于」版本（git 最近账本祖先，DEC-002）的 commit
- 根版本 / 基线解析失败 → base_kind=root / error，changes 为空

diff 复用 config_diff 的纯函数原语（prompt 行级 hunks / skills 集合差）；
middleware 按 class_name 配对比较 params（projection 挂载形态无 (hook, group)
唯一键，同类多次挂载取首实例配对）。

缓存：commit 不可变 → (base_commit, target_commit) 结果永久有效，
进程内缓存 + TTL 60s 防御性兜底（与 elements_api 视图缓存同模式）。

失败语义（FR-003）：compute 前 cat-file -e 校验两侧 commit 本地可读；
计算过程异常（含 git 读取失败）降级 base_kind=error 且不写缓存——
要素视图的容错辅助函数（读失败返回空串）会产出虚构 diff，diff 路径
必须严格失败（review r1 finding：correctness+reliability 独立确认）。

whole_agent 在跨架构版本对（v7 ↔ v14）产出：旧架构 agent 整体退役
（removed）、新架构 agent 整体登场（added），同时保留具体要素 diff。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.versioning import platform_ledger
from app.versioning.config_diff import _diff_prompt, _diff_skills
from app.versioning.elements_api import (
    _agent_skills,
    _agent_specs_for_commit,
    _build_middleware_stacks,
    _build_skill_infos,
    read_prompt_body,
)

logger = logging.getLogger("evolution.upgrade_diff")

_CACHE_TTL = 60.0
_diff_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    """清空 diff 缓存（测试用）。"""
    _diff_cache.clear()


def resolve_base(entry: dict[str, Any]) -> tuple[str, int | None, int | None]:
    """从账本富化条目解析 diff 基线（纯函数，不触账本/git）。

    Returns:
        (base_kind, base_version, same_code_as)
        base_kind ∈ ancestor | same_code | root | error
    """
    if entry.get("same_code_as") is not None:
        return "same_code", None, entry["same_code_as"]
    if entry.get("based_on_status") == "error":
        return "error", None, None
    if entry.get("based_on_status") != "resolved" or entry.get("based_on") is None:
        return "root", None, None
    return "ancestor", entry["based_on"], None


def _require_readable_commit(commit: str) -> None:
    """校验 commit 本地可读（cat-file -e，bare 仓库）。失败 raise——diff 路径严格失败。"""
    from app.core.git_ops import _git, read_dir
    _git(["cat-file", "-e", f"{commit}^{{commit}}"], read_dir())


def _diff_processors(
    old_stack: list[dict[str, Any]],
    new_stack: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """middleware 挂载 diff：按 class_name 首实例配对，added/removed/modified。

    输出对齐前端 ProcessorChange 形状（key.hook 取 hooks[0]）。
    首实例 = 栈序在前的挂载（base 骨架先于 agent 追加，语义更稳）。
    """
    old_map: dict[str, dict[str, Any]] = {}
    new_map: dict[str, dict[str, Any]] = {}
    for m in old_stack:
        old_map.setdefault(m["class_name"], m)
    for m in new_stack:
        new_map.setdefault(m["class_name"], m)
    changes: list[dict[str, Any]] = []
    for cls in sorted(set(old_map) | set(new_map)):
        old_m = old_map.get(cls)
        new_m = new_map.get(cls)
        key = {
            "hook": (new_m or old_m)["hooks"][0] if (new_m or old_m)["hooks"] else "",
            "group": (new_m or old_m).get("group") or "",
        }
        if old_m is None:
            changes.append({
                "key": key,
                "change_type": "added",
                "class_change": {"old": None, "new": cls},
                "params_change": {"old": None, "new": new_m.get("params", {})},
            })
        elif new_m is None:
            changes.append({
                "key": key,
                "change_type": "removed",
                "class_change": {"old": cls, "new": None},
                "params_change": {"old": old_m.get("params", {}), "new": None},
            })
        elif old_m.get("params") != new_m.get("params"):
            changes.append({
                "key": key,
                "change_type": "modified",
                "class_change": {"old": cls, "new": cls},
                "params_change": {"old": old_m.get("params", {}), "new": new_m.get("params", {})},
            })
    return changes


def compute_agent_diffs(base_commit: str, target_commit: str) -> dict[str, dict[str, Any]]:
    """两个 commit 的要素快照 → 按 agent 聚合的 diff（只含有变化的 agent）。

    Agent 结构按各自 commit 的布局探测（v7 两泳道 / v14 多 Agent），取并集对比：
    跨架构版本对（如 v14 vs v10）会产出 whole_agent added/removed——
    旧架构 agent 整体退役、新架构 agent 整体登场，同时保留具体要素 diff。
    """
    base_specs, base_layout = _agent_specs_for_commit(base_commit)
    target_specs, target_layout = _agent_specs_for_commit(target_commit)
    base_map = {name: (kind, files) for name, kind, files in base_specs}
    target_map = {name: (kind, files) for name, kind, files in target_specs}

    skills = {c: _build_skill_infos(c) for c in (base_commit, target_commit)}
    stacks = {c: _build_middleware_stacks(c) for c in (base_commit, target_commit)}

    # 展示顺序：目标侧架构在前，仅存于基线的退役 agent 殿后
    ordered = [name for name, _, _ in target_specs]
    ordered.extend(name for name in base_map if name not in target_map)

    result: dict[str, dict[str, Any]] = {}
    for name in ordered:
        in_base, in_target = name in base_map, name in target_map
        kind = (target_map.get(name) or base_map.get(name))[0]

        diff: dict[str, Any] = {}
        base_body = read_prompt_body(base_commit, base_map[name][1]) if in_base else None
        target_body = read_prompt_body(target_commit, target_map[name][1]) if in_target else None
        prompt_diff = _diff_prompt(base_body, target_body)
        if prompt_diff:
            diff["prompt"] = prompt_diff

        base_skill_paths = (
            [s["path"] for s in _agent_skills(skills[base_commit], name, kind, base_layout)]
            if in_base else []
        )
        target_skill_paths = (
            [s["path"] for s in _agent_skills(skills[target_commit], name, kind, target_layout)]
            if in_target else []
        )
        skill_diff = _diff_skills(base_skill_paths, target_skill_paths)
        if skill_diff:
            diff["skills"] = skill_diff

        processor_changes = _diff_processors(
            stacks[base_commit].get(name, []) if in_base else [],
            stacks[target_commit].get(name, []) if in_target else [],
        )
        if processor_changes:
            diff["processors"] = processor_changes

        if in_target and not in_base:
            diff["whole_agent"] = "added"
        elif in_base and not in_target:
            diff["whole_agent"] = "removed"

        if diff:
            result[name] = diff
    return result


def build_upgrade_diff(version: int) -> dict[str, Any]:
    """组装升级总览响应（含基线解析 + diff + 缓存）。

    账本不可达向上抛 RuntimeError（路由层转 502）；
    基线解析失败不抛——返回 base_kind=error，前端显示「变更计算失败」占位。
    """
    entry = platform_ledger.get_version(version)
    if entry is None:
        raise LookupError(f"版本 v{version} 不存在")

    kind, base_version, same_code_as = resolve_base(entry)

    base_commit: str | None = None
    agents_by_name: dict[str, dict[str, Any]] = {}
    if kind == "ancestor":
        base_commit = platform_ledger.resolve_commit(base_version)
        if base_commit is None:
            # 基线版本在账本流水里但 commit 解析失败（理论上不该发生）——按 error 降级
            logger.warning("based_on v%s commit 解析失败", base_version)
            kind = "error"
        else:
            cache_key = (base_commit, entry["commit"])
            hit = _diff_cache.get(cache_key)
            if hit and (time.monotonic() - hit[0]) < _CACHE_TTL:
                agents_by_name = hit[1]
            else:
                # FR-003 失败语义：compute 严格失败（commit 不可读/读取异常）→
                # 降级 error 占位，且不把失败结果写进缓存
                try:
                    _require_readable_commit(base_commit)
                    _require_readable_commit(entry["commit"])
                    agents_by_name = compute_agent_diffs(base_commit, entry["commit"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "升级 diff 计算失败（降级 error 占位）: v%s base=%s: %s",
                        version, base_commit, exc,
                    )
                    kind = "error"
                else:
                    _diff_cache[cache_key] = (time.monotonic(), agents_by_name)

    return {
        "version": version,
        "target_commit": entry["commit"],
        "base_kind": kind,
        "base_version": base_version,
        "base_commit": base_commit,
        "same_code_as": same_code_as,
        "changes": {
            "agents": [
                {"agent": name, "diff": diff}
                for name, diff in agents_by_name.items()
            ],
            "intent": None,
        },
    }


__all__ = [
    "resolve_base",
    "compute_agent_diffs",
    "build_upgrade_diff",
    "clear_cache",
]
