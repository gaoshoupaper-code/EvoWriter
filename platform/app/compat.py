"""resume 兼容判定（FR-005 / DEC-003/006）。

规则（保守优先，任何异常都按 incompatible 处理）：
1. 两版指纹逐文件 diff——增 / 删 / 改都算变化；文件跨层移动按两个层的归属并集计。
2. 变化文件的归属层集合，仅 a_text → compatible；含 b_param 或 c_code → incompatible。
3. 任一指纹缺失或比对异常 → incompatible（宁可拒绝恢复，不可带脏 State 续跑）。

changed_files 截断前 20 条（防止 diff 爆量撑爆响应与日志）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from contracts.platform import SurfaceFingerprint
from contracts.surface_types import SurfaceLayer

logger = logging.getLogger("platform.compat")

MAX_CHANGED_FILES = 20

# 判定输出用的层展示顺序（稳定，不随集合遍历序漂移）
_LAYER_ORDER = (SurfaceLayer.A_TEXT, SurfaceLayer.B_PARAM, SurfaceLayer.C_CODE)


@dataclass
class CompatVerdict:
    """兼容判定结果（ResumeCheck 的判定部分）。"""

    decision: str  # compatible / incompatible
    reason: str
    changed_layers: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)


def diff_fingerprints(
    old: SurfaceFingerprint, new: SurfaceFingerprint
) -> tuple[list[SurfaceLayer], list[str]]:
    """两版指纹 diff → （变化文件归属层集合，变化文件列表）。

    变化 = 内容 hash 不同 / 只在一版存在 / 两版归属层不同（跨层移动）。
    文件跨层时，新旧两个层都计入归属集合——保守口径。
    """
    def _flat(fp: SurfaceFingerprint) -> dict[str, tuple[SurfaceLayer, str]]:
        merged: dict[str, tuple[SurfaceLayer, str]] = {}
        for layer in _LAYER_ORDER:
            for path, digest in getattr(fp, layer.value).items():
                merged[path] = (layer, digest)
        return merged

    old_map = _flat(old)
    new_map = _flat(new)

    changed_layers: set[SurfaceLayer] = set()
    changed_files: list[str] = []
    for path in sorted(set(old_map) | set(new_map)):
        was, now = old_map.get(path), new_map.get(path)
        if was == now:
            continue
        changed_files.append(path)
        for entry in (was, now):
            if entry is not None:
                changed_layers.add(entry[0])
    return sorted(changed_layers, key=_LAYER_ORDER.index), changed_files


def judge_resume(
    bound: SurfaceFingerprint | None, current: SurfaceFingerprint | None
) -> CompatVerdict:
    """按 DEC-006 规则判定绑定版本 vs 当前生产版本能否续跑。"""
    if bound is None or current is None:
        missing = "绑定版本" if bound is None else "当前生产版本"
        return CompatVerdict(
            decision="incompatible",
            reason=f"{missing} surface 指纹缺失，保守拒绝恢复",
        )
    try:
        layers, files = diff_fingerprints(bound, current)
    except Exception as exc:  # noqa: BLE001 — 判定自身失败必须保守
        logger.error("指纹比对异常: %s", exc)
        return CompatVerdict(
            decision="incompatible", reason=f"指纹比对异常，保守拒绝恢复: {exc}"
        )

    layer_names = [layer.value for layer in layers]
    breaking = [l for l in layer_names if l in (SurfaceLayer.B_PARAM.value, SurfaceLayer.C_CODE.value)]
    if breaking:
        return CompatVerdict(
            decision="incompatible",
            reason=f"surface 变化落在 {'+'.join(breaking)} 层，State schema 可能漂移",
            changed_layers=layer_names,
            changed_files=files[:MAX_CHANGED_FILES],
        )
    reason = "仅 a_text 层变化" if layer_names else "两版 surface 完全一致"
    return CompatVerdict(
        decision="compatible",
        reason=reason,
        changed_layers=layer_names,
        changed_files=files[:MAX_CHANGED_FILES],
    )


__all__ = ["CompatVerdict", "diff_fingerprints", "judge_resume", "MAX_CHANGED_FILES"]
