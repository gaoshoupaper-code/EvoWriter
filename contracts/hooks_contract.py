"""hooks.md 钩子登记契约判定器（REQ-20261010-000638 FR-004 合并版；卡①A）。

「判定器唯一实现」原则（沿用 storyline/object 契约，DEC-011）：契约判定放
contracts，harness 运行时护栏（HooksContractGuardMiddleware）与 executor
测试共用本模块，两侧不得各自实现。

v42 的 hooks.md 钩子登记表升级为 PPP 台账（期待分层 + 推进环节），九列：
  # 钩子登记
  | 编号 | 钩子 | 层级 | 类型 | 埋设事件 | 推进事件 | 兑现事件 | 状态 | 备注 |

校验口径（结构硬拦，语义口径归 harness 观测——DEC-010）：
  - 编号 H{n} 全文件唯一；层级三值（期待分层）；类型四值（v42 原枚举）；
    状态四值（未收/推进中/已收/已放弃——v42「转长期」由层级=主线大期待承载）
  - 「已放弃」必须在备注写原因（防悬置假账）；「已收」必须有兑现事件
  - 埋设/推进/兑现事件列逐字引用 storyline.md 事件表（extract_event_names 共用）
  - storyline 反查：修订删除（或改名——名称即锚点）的事件不得被登记引用
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .storyline_contract import GuardViolation, extract_event_names

# ── 枚举（harness specs §八 与护栏共用；改这里必须同步 specs）─────────

HOOK_LEVELS = ("主线大期待", "线级期待", "事件钩子")
HOOK_TYPES = ("悬念", "期待", "危机", "反转暗示")
HOOK_STATES = ("未收", "推进中", "已收", "已放弃")

_LEDGER_HEADER = re.compile(r"^\|.*编号.*\|.*钩子.*\|.*层级.*\|")
_TABLE_SEPARATOR = re.compile(r"\|[\s|:-]+\|")
_HOOK_ID = re.compile(r"^H\d+$")


def _clean(s: str) -> str:
    return s.replace("**", "").strip()


def _split_events(cell: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in re.split(r"[、,，/;；]", cell) if p.strip())


def _iter_raw_rows(md: str) -> list[list[str]]:
    """登记表数据行（表头定位后、分隔行跳过），返回原始单元格列表。"""
    rows: list[list[str]] = []
    in_ledger = False
    for ln in md.splitlines():
        s = ln.strip()
        if _LEDGER_HEADER.match(s):
            in_ledger = True
            continue
        if not in_ledger or not s.startswith("|") or _TABLE_SEPARATOR.fullmatch(s):
            continue
        rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows


@dataclass(frozen=True)
class HookRow:
    """一行钩子登记：PPP 状态机 + 期待分层（层级）+ v42 类型 + 事件锚点。"""

    id: str
    hook: str
    level: str
    type: str
    plant_events: tuple[str, ...] = ()
    progress_events: tuple[str, ...] = ()
    payoff_events: tuple[str, ...] = ()
    status: str = ""
    note: str = ""


def parse_hooks(md: str) -> list[HookRow]:
    """解析登记表数据行；列不足的行缺失列以空串补齐（列数问题由校验端点名）。"""
    rows: list[HookRow] = []
    for cells in _iter_raw_rows(md):
        vals = [_clean(c) for c in cells]
        vals += [""] * (9 - len(vals))  # 短行补齐，长行截断（列数由校验端拦）
        rows.append(HookRow(
            id=vals[0],
            hook=vals[1],
            level=vals[2],
            type=vals[3],
            plant_events=_split_events(vals[4]),
            progress_events=_split_events(vals[5]),
            payoff_events=_split_events(vals[6]),
            status=vals[7],
            note=vals[8],
        ))
    return rows


def check_hooks_write(
    hooks_markdown: str,
    storyline_markdown: str,
) -> list[GuardViolation]:
    """校验一次钩子登记写入（预估写入后内容），返回违规列表（空=放行）。

    Args:
        hooks_markdown:   预估写入后内容（write_file=args.content；
                          edit_file=磁盘内容按 replace 语义投影）
        storyline_markdown: storyline.md 磁盘当前内容（锚点来源）
    """
    violations: list[GuardViolation] = []

    raw_rows = _iter_raw_rows(hooks_markdown)
    if not raw_rows:
        violations.append(GuardViolation(
            "contract",
            "hooks.md 缺钩子登记表或登记表无数据行"
            "（九列：编号/钩子/层级/类型/埋设事件/推进事件/兑现事件/状态/备注）",
        ))
        return violations

    event_names = set(extract_event_names(storyline_markdown))
    ids_seen: set[str] = set()

    for cells in raw_rows:
        if len(cells) != 9:
            violations.append(GuardViolation(
                "contract",
                f"登记行列数不齐（须为九列）：{'|'.join(cells)[:40]}…",
            ))
            continue
        hid, hook, level, htype, plant_ev, progress_ev, payoff_ev, status, note = (
            _clean(c) for c in cells
        )
        rowname = hid or (hook[:12] or "未命名")

        if not _HOOK_ID.match(hid):
            violations.append(GuardViolation(
                "contract", f"登记行「{rowname}」编号「{hid}」非法（须为 H{{n}} 形式，如 H1）",
            ))
        if hid in ids_seen:
            violations.append(GuardViolation(
                "contract", f"登记编号「{hid}」重复（全文件唯一）",
            ))
        ids_seen.add(hid)

        if not hook:
            violations.append(GuardViolation("contract", f"登记行「{rowname}」钩子内容为空"))
        if level not in HOOK_LEVELS:
            violations.append(GuardViolation(
                "contract",
                f"登记行「{rowname}」层级「{level}」非法（须为 {'/'.join(HOOK_LEVELS)}）",
            ))
        if htype not in HOOK_TYPES:
            violations.append(GuardViolation(
                "contract",
                f"登记行「{rowname}」类型「{htype}」非法（须为 {'/'.join(HOOK_TYPES)}）",
            ))
        if status not in HOOK_STATES:
            violations.append(GuardViolation(
                "contract",
                f"登记行「{rowname}」状态「{status}」非法（须为 {'/'.join(HOOK_STATES)}）",
            ))
        if status == "已放弃" and not note:
            violations.append(GuardViolation(
                "contract", f"登记行「{rowname}」状态为「已放弃」但备注为空（放弃须写原因，防悬置假账）",
            ))
        if status == "已收" and not _split_events(payoff_ev):
            violations.append(GuardViolation(
                "contract", f"登记行「{rowname}」状态为「已收」但兑现事件为空",
            ))
        if not _split_events(plant_ev):
            violations.append(GuardViolation(
                "contract", f"登记行「{rowname}」埋设事件为空（每条钩子必有埋设事件）",
            ))

        for ev in (*_split_events(plant_ev), *_split_events(progress_ev), *_split_events(payoff_ev)):
            if ev not in event_names:
                violations.append(GuardViolation(
                    "contract",
                    f"登记行「{rowname}」引用的事件「{ev}」在 storyline.md 事件表中不存在"
                    f"（事件名须逐字引用、创建后不改）",
                ))

    return violations


def find_dangling_hook_refs(
    storyline_current: str,
    storyline_projected: str,
    hooks_markdown: str,
) -> list[GuardViolation]:
    """storyline 写入反查：本次修订删除（或改名）的事件不得被登记引用。

    事件改名视为删除（名称即锚点，与物品卡同规）。
    """
    removed = (
        set(extract_event_names(storyline_current))
        - set(extract_event_names(storyline_projected))
    )
    if not removed:
        return []

    violations: list[GuardViolation] = []
    for row in parse_hooks(hooks_markdown):
        for ev in (*row.plant_events, *row.progress_events, *row.payoff_events):
            if ev in removed:
                violations.append(GuardViolation(
                    "contract",
                    f"本次 storyline 修订删除了事件「{ev}」，但钩子登记 {row.id} 仍引用它。"
                    f"删除事件：先清掉登记行对它的引用再删；改名不支持（事件名即锚点，创建后不改）——"
                    f"确需改名请三步走：先删登记行全部引用 → 改 storyline 事件名 → 重写登记行",
                ))
    return violations


__all__ = [
    "HOOK_LEVELS",
    "HOOK_STATES",
    "HOOK_TYPES",
    "HookRow",
    "check_hooks_write",
    "find_dangling_hook_refs",
    "parse_hooks",
]
