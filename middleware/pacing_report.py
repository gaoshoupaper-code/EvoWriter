"""PacingReportMiddleware — 节奏体检报告（REQ-20261010-000638 FR-005）。

程序算硬指标，review 判语义（DEC-006）：挂在 storybuilding_review 子代理上，
before_agent 时从工作区读 storyline.md + hooks.md + 观测配置，计算四类指标：

  1. 形态偏差：目标形态槽位 vs 主线实际张力序列分段对比（DEC-005/008）。
     主线事件数 < shape_min_events 时降级为峰谷摘要（防短主线分段噪声）。
  2. 爽点间隔：全局明线事件序列上（暗线不计入，读者看不见），相邻小爽
     间隔 ≤ small_gap_max、相邻大爽间隔 ≤ big_gap_max、大爽前
     big_payoff_lookback 个事件内峰值张力 ≥ big_payoff_min_tension（DEC-007）。
  3. 许诺健康：已许诺却无推进的清单；未兑现且未放弃的悬置清单（FR-004）。
  4. 节拍诊断清单：观测配置 beat_checklist 的机器可判定条目对照（DEC-013）。

报告以 [节奏体检报告] HumanMessage 注入 review 上下文，只列超标项
（紧凑清单，防挤爆 review 上下文）；同时 logger.info 落观测日志喂进化。
自身任何异常 → logger.error + 不注入（降级放行，绝不阻断审查，AC-005）。

诊断只建议、不拦截（DEC-006：缺哪拍补哪拍，不是缺拍不许交）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage

from contracts.hooks_contract import parse_hooks
from contracts.storyline_contract import (
    EventRow,
    LineBlock,
    extract_rhythm_field,
    iter_line_blocks,
    parse_shape_slots,
)

from .pacing_config import PacingConfig, load_pacing_config

logger = logging.getLogger(__name__)


# ── 事件序列（张力/爽点/类型，带线归属与全局 T 序）────────────────


def _tension(row: EventRow) -> int | None:
    try:
        return int(row.tension)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SeqEvent:
    """诊断序列中的一个事件（明线/暗线标记 + 张力/爽点/类型）。"""

    name: str
    line: str
    line_type: str
    type_word: str
    tension: int | None
    payoff: str
    pos: int  # 序列内位置（1-based）


def _ordered_rows(block: LineBlock) -> list[EventRow]:
    """区块内事件行按 T 号稳定排序（缺 T 号保持文档序）。"""
    indexed = [(i, r) for i, r in enumerate(block.events) if r.name]
    ordered = sorted(
        indexed, key=lambda ir: (ir[1].t_num if ir[1].t_num is not None else float("inf"), ir[0]),
    )
    return [r for _i, r in ordered]


def _build_sequences(md: str) -> tuple[list[SeqEvent], list[SeqEvent]]:
    """构建（主线序列, 全局明线序列），均按 T 号稳定排序、位置 1-based。

    暗线不计入读者体验序列（DEC-009）；跨线合并中每事件只出现一次
    （物理写在主承载线区块）。
    """
    blocks = iter_line_blocks(md)
    mainline: list[SeqEvent] = []
    merged_raw: list[tuple[float, int, SeqEvent]] = []
    doc_i = 0
    for blk in blocks:
        for row in _ordered_rows(blk):
            t_num = row.t_num if row.t_num is not None else float("inf")
            ev = SeqEvent(
                name=row.name, line=blk.name, line_type=blk.type,
                type_word=row.type_word, tension=_tension(row), payoff=row.payoff,
                pos=0,
            )
            if blk.type == "主线":
                mainline.append(ev)
            if blk.type != "暗线":  # 暗线不计入读者体验序列（DEC-009）
                merged_raw.append((t_num, doc_i, ev))
            doc_i += 1
    merged_raw.sort(key=lambda x: (x[0], x[1]))
    merged = [
        SeqEvent(name=ev.name, line=ev.line, line_type=ev.line_type,
                 type_word=ev.type_word, tension=ev.tension, payoff=ev.payoff, pos=pos)
        for pos, (_t, _i, ev) in enumerate(merged_raw, start=1)
    ]
    # 主线序列位置（形态分段用主线自身序）
    mainline = [
        SeqEvent(name=ev.name, line=ev.line, line_type=ev.line_type,
                 type_word=ev.type_word, tension=ev.tension, payoff=ev.payoff, pos=pos)
        for pos, ev in enumerate(mainline, start=1)
    ]
    return mainline, merged


# ── 四类指标 ────────────────────────────────────────────────


def _segments(n: int) -> tuple[range, range, range]:
    """三段切分：前段 / 中段 / 终局段（各约 1/3，至少 1 个）。"""
    k = max(1, n // 3)
    head = range(0, k)
    middle = range(k, n - k) if n - k > k else range(k, n)
    final = range(max(k, n - k), n) if n > k else range(0, 0)
    return head, middle, final


def check_shape(mainline: list[SeqEvent], md: str, config: PacingConfig) -> list[str]:
    """形态偏差：目标槽位 vs 主线实际张力；短主线降级为峰谷摘要。"""
    issues: list[str] = []
    tensions = [e.tension for e in mainline]
    if not tensions or any(t is None for t in tensions):
        return issues  # 无张力数据（旧 schema）——不诊断

    n = len(tensions)
    slots = parse_shape_slots(extract_rhythm_field(md) or "")
    if n < config.shape_min_events:
        peak = max(tensions)
        valley = min(tensions)
        issues.append(
            f"形态对比降级：主线仅 {n} 个事件（阈值 {config.shape_min_events}），"
            f"峰 {peak}/谷 {valley} 供参考"
        )
        return issues
    if not slots:
        issues.append("「节奏曲线」缺槽位目标形态（或语法非法）——形态无法对比")
        return issues

    head, middle, final = _segments(n)
    by_slot = {s.slot: s for s in slots}

    def _fmt(op: str, target: tuple[int, ...], actual: int | None, where: str) -> str:
        t = "/".join(str(v) for v in target)
        a = "?" if actual is None else str(actual)
        return f"目标{op}{t} 实际{a}（{where}）"

    s = by_slot.get("首事件")
    if s is not None:
        actual = tensions[0]
        if not _slot_ok(s, actual):
            issues.append(f"形态偏差·首事件：{_fmt(s.op, s.values, actual, '第 1 事件')}")
    s = by_slot.get("前段末")
    if s is not None and len(head) > 0:
        actual = tensions[head[-1]]
        if not _slot_ok(s, actual):
            issues.append(f"形态偏差·前段末：{_fmt(s.op, s.values, actual, f'第 {head[-1] + 1} 事件')}")
    s = by_slot.get("中点谷")
    if s is not None and len(middle) > 0:
        actual = min(tensions[i] for i in middle)
        if not _slot_ok(s, actual):
            pos = next(i for i in middle if tensions[i] == actual) + 1
            issues.append(f"形态偏差·中点谷：{_fmt(s.op, s.values, actual, f'中段最低，第 {pos} 事件')}")
    s = by_slot.get("终局")
    if s is not None and len(final) > 0:
        actual = max(tensions[i] for i in final)
        if s.twin_peak:
            # 双峰本义即「≥target×2」：终局段须有两个达标峰
            target = s.values[-1]
            peaks = [tensions[i] for i in final if tensions[i] is not None and tensions[i] >= target]
            if len(peaks) < 2:
                issues.append(f"形态偏差·终局双峰：目标 ≥{target}×2 实际达标 {len(peaks)} 峰（终局段最高 {actual}）")
        elif not _slot_ok(s, actual):
            # 非双峰终局与其余槽同口径（≈/= 取等值、≥/≤ 按比较）
            issues.append(f"形态偏差·终局峰：{_fmt(s.op, s.values, actual, '终局段最高')}")
    return issues


def _slot_ok(slot: Any, actual: int) -> bool:
    target = slot.values[0]
    if slot.op == ">=":
        return actual >= target
    if slot.op == "<=":
        return actual <= target
    if slot.op == "=":
        return actual == target
    return actual == target  # ≈ 按整数刻度取等值


def check_payoff(merged: list[SeqEvent], config: PacingConfig) -> list[str]:
    """爽点间隔与铺垫（三小一大对照，DEC-007 默认值）。"""
    issues: list[str] = []
    rules = config.payoff
    smalls = [e for e in merged if e.payoff == "小"]
    bigs = [e for e in merged if e.payoff == "大"]

    def _gap_pair(evs: list[SeqEvent]) -> list[tuple[SeqEvent, SeqEvent, int]]:
        return [
            (a, b, b.pos - a.pos)
            for a, b in zip(evs, evs[1:])
        ]

    for a, b, gap in _gap_pair(smalls):
        if gap > rules.small_gap_max:
            issues.append(
                f"爽点间隔·小：{a.name} → {b.name} 间隔 {gap} 个事件（阈值 ≤{rules.small_gap_max}）"
            )
    for a, b, gap in _gap_pair(bigs):
        if gap > rules.big_gap_max:
            issues.append(
                f"爽点间隔·大：{a.name} → {b.name} 间隔 {gap} 个事件（阈值 ≤{rules.big_gap_max}）"
            )
    for big in bigs:
        window = [e for e in merged if big.pos - rules.big_payoff_lookback <= e.pos < big.pos]
        peak = max((e.tension for e in window if e.tension is not None), default=None)
        if peak is not None and peak < rules.big_payoff_min_tension:
            issues.append(
                f"爽点铺垫·大：{big.name} 前 {rules.big_payoff_lookback} 个事件峰值张力 {peak}"
                f"（阈值 ≥{rules.big_payoff_min_tension}）——大爽前应有张力爬升"
            )
    return issues


def check_hooks(hooks_md: str) -> list[str]:
    """钩子健康：无推进 / 悬置清单（已放弃不算悬置，DEC-014）。"""
    issues: list[str] = []
    rows = parse_hooks(hooks_md)
    for row in rows:
        if row.status == "未收" and not row.progress_events and not row.payoff_events:
            issues.append(f"钩子无推进：{row.id} {row.hook}")
    pending = [row for row in rows if row.status in ("未收", "推进中")]
    if pending:
        names = "、".join(f"{r.id} {r.hook}" for r in pending)
        issues.append(f"钩子悬置（未收且未放弃，判断是蓄势还是遗忘）：{names}")
    return issues


def check_beats(merged: list[SeqEvent], config: PacingConfig) -> list[str]:
    """节拍诊断清单对照（机器可判定条目；清单在观测配置，DEC-013）。"""
    issues: list[str] = []
    n = len(merged)
    if n == 0:
        return issues
    _head, middle, final = _segments(n)
    for item in config.beat_checklist:
        kind = item.get("kind")
        desc = item.get("desc", item.get("id", "未知条目"))
        if kind == "types_in_segment":
            types = set(item.get("types", ()))
            seg = middle if item.get("segment") == "middle" else final
            hit = any(e.type_word in types for e in merged[seg.start:seg.stop] if seg)
            if not hit:
                issues.append(f"节拍对照：{desc}——未命中")
        elif kind == "valley":
            seg = middle if item.get("segment") == "middle" else _head
            vals = [e.tension for e in merged[seg.start:seg.stop] if e.tension is not None]
            limit = int(item.get("max_tension", 2))
            if vals and min(vals) > limit:
                issues.append(f"节拍对照：{desc}——中段最低张力 {min(vals)}")
        elif kind == "peak":
            seg = final
            vals = [e.tension for e in merged[seg.start:seg.stop] if e.tension is not None]
            need = int(item.get("min_tension", 4))
            if vals and max(vals) < need:
                issues.append(f"节拍对照：{desc}——终局段最高张力 {max(vals)}")
        elif kind == "post_victory_hook":
            window = int(item.get("window", 2))
            for i, e in enumerate(merged):
                if e.type_word != "胜利":
                    continue
                nxt = merged[i + 1:i + 1 + window]
                if not any(x.type_word == "悬念" for x in nxt):
                    issues.append(f"节拍对照：{desc}——胜利事件 {e.name} 后未开新钩子")
        # 未知 kind：配置可扩展，未知条目跳过
    return issues


# ── 报告构建与注入 ──────────────────────────────────────────


def build_pacing_report(
    storyline_md: str,
    hooks_md: str,
    config: PacingConfig | None,
) -> str | None:
    """计算四类指标并渲染紧凑报告；无事件数据返回 None。"""
    mainline, merged = _build_sequences(storyline_md)
    if not mainline and not merged:
        return None
    cfg = config or PacingConfig()

    issues: list[str] = []
    issues.extend(check_shape(mainline, storyline_md, cfg))
    issues.extend(check_payoff(merged, cfg))
    if hooks_md.strip():
        issues.extend(check_hooks(hooks_md))
    issues.extend(check_beats(merged, cfg))

    lines = ["[节奏体检报告] 程序计算的节奏硬指标，只列超标项；语义判断（是否刻意设计）由你负责。"]
    if issues:
        lines.extend(f"- {item}" for item in issues)
    else:
        lines.append("- 全部指标达标")
    return "\n".join(lines)


class PacingReportMiddleware(AgentMiddleware):
    """review 子代理的节奏体检报告注入器（FR-005；异常降级不注入）。"""

    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = Path(workspace_path).resolve()

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._inject()

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._inject()

    def _inject(self) -> dict[str, Any] | None:
        try:
            storyline = self._read("storyline.md")
            if not storyline.strip():
                return None
            hooks = self._read("hooks.md")
            report = build_pacing_report(storyline, hooks, load_pacing_config())
            if report is None:
                return None
            issue_lines = [
                ln for ln in report.splitlines()
                if ln.startswith("- ") and "全部指标达标" not in ln
            ]
            logger.info("节奏体检报告注入 review：%d 项超标", len(issue_lines))
            logger.info("节奏体检报告内容：%s", report)
            return {"messages": [HumanMessage(content=report)]}
        except Exception:  # noqa: BLE001 — 诊断故障不得阻断审查（AC-005 降级语义）
            logger.exception("节奏体检报告计算失败，降级跳过注入")
            return None

    def _read(self, filename: str) -> str:
        path = self.workspace_path / filename
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="gb18030", errors="replace")


__all__ = ["PacingReportMiddleware", "build_pacing_report"]


def build(abc):
    """架构清单挂载钩子：review 子代理——节奏体检报告注入。"""
    return PacingReportMiddleware(abc.workspace_path)
