"""节奏语义参数唯一落点（REQ-20261010-000638 FR-005/FR-007；DEC-004/DEC-013）。

storyline_observation_config.json 承载全部节奏语义参数，随 harness 包版本演进
（进化代际可调，不动平台代码）：
  - count_ranges / reference_types：事件数量参考区间与参考类型词（182730 既有）
  - tension_scale：张力刻度（提示词五档的机器侧声明）
  - payoff_rules：爽点间隔默认值（三小一大的事件层折算）
  - shape_min_events：形态分段对比的最小主线事件数（低于阈值降级为只报峰谷）
  - beat_checklist：节拍诊断清单（review 体检报告对照，机器可判定条目）

护栏观测（分布记录）与节奏体检报告（pacing_report）共用本加载器；
配置缺失/损坏 → None，调用方各自降级（只记实际数 / 跳过对应指标）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "storyline_observation_config.json"


@dataclass(frozen=True)
class PayoffRules:
    """爽点间隔默认值（FR-005；DEC-007 默认：小≤3 / 大≤8 / 大爽前张力≥4）。"""

    small_gap_max: int = 3
    big_gap_max: int = 8
    big_payoff_min_tension: int = 4
    big_payoff_lookback: int = 2


@dataclass(frozen=True)
class PacingConfig:
    """观测配置的结构化形态；语义口径全在 harness，不进 contracts。"""

    count_ranges: dict[str, tuple[int, int]] = field(default_factory=dict)
    reference_types: frozenset[str] = frozenset()
    payoff: PayoffRules = field(default_factory=PayoffRules)
    shape_min_events: int = 8
    beat_checklist: tuple[dict, ...] = ()


def load_pacing_config(path: Path | None = None) -> PacingConfig | None:
    """加载观测配置；缺失/损坏返回 None（调用方降级，不抛异常）。"""
    try:
        raw = json.loads((path or _CONFIG_PATH).read_text(encoding="utf-8"))
        ranges = {str(t): (int(lo), int(hi)) for t, (lo, hi) in raw["count_ranges"].items()}
        payoff_raw = raw.get("payoff_rules", {}) or {}
        payoff = PayoffRules(
            small_gap_max=int(payoff_raw.get("small_gap_max", 3)),
            big_gap_max=int(payoff_raw.get("big_gap_max", 8)),
            big_payoff_min_tension=int(payoff_raw.get("big_payoff_min_tension", 4)),
            big_payoff_lookback=int(payoff_raw.get("big_payoff_lookback", 2)),
        )
        return PacingConfig(
            count_ranges=ranges,
            reference_types=frozenset(str(w) for w in raw.get("reference_types", ())),
            payoff=payoff,
            shape_min_events=int(raw.get("shape_min_events", 8)),
            beat_checklist=tuple(raw.get("beat_checklist", ()) or ()),
        )
    except Exception:
        logger.exception("节奏观测配置加载失败，降级 config=%s", path or _CONFIG_PATH)
        return None


__all__ = ["PayoffRules", "PacingConfig", "load_pacing_config"]
