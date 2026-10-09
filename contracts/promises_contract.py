"""promises.md 许诺台账契约判定器（REQ-20261010-000638 FR-004）。

「判定器唯一实现」原则（沿用 storyline/object 契约，DEC-011）：契约判定放
contracts，harness 运行时护栏（PromisesContractGuardMiddleware）与 executor
测试共用本模块，两侧不得各自实现。

台账结构（单一文件 promises.md，DEC-002）：
  # 许诺台账
  | 编号 | 许诺 | 层级 | 所属线 | 状态 | 许诺事件 | 推进事件 | 兑现事件 | 备注 |

校验口径（结构硬拦，语义口径归 harness 观测——DEC-010）：
  - 编号 P{n} 全文件唯一；层级三值；状态四值
  - 「已放弃」必须在备注写原因（防悬置假账）；「已兑现」必须有兑现事件
  - 许诺/推进/兑现事件列逐字引用 storyline.md 事件表（extract_event_names 共用）
  - storyline 反查：修订删除（或改名——名称即锚点）的事件不得被台账引用
  - 台账至少一行数据（初构收尾建账时事件表已定稿）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .storyline_contract import GuardViolation, extract_event_names

# ── 枚举（提示词 §台账 与护栏共用；改这里必须同步提示词）─────────

PROMISE_LEVELS = ("主线大期待", "线级期待", "事件钩子")
PROMISE_STATES = ("已许诺", "推进中", "已兑现", "已放弃")

_LEDGER_HEADER = re.compile(r"^\|.*编号.*\|.*许诺.*\|.*层级.*\|")
_TABLE_SEPARATOR = re.compile(r"\|[\s|:-]+\|")
_PROMISE_ID = re.compile(r"^P\d+$")


def _clean(s: str) -> str:
    return s.replace("**", "").strip()


def _split_events(cell: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in re.split(r"[、,，/;；]", cell) if p.strip())


def _iter_raw_rows(md: str) -> list[list[str]]:
    """台账表数据行（表头定位后、分隔行跳过），返回原始单元格列表。"""
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
class PromiseRow:
    """一行许诺：PPP 状态机 + 期待分层（层级）+ 事件锚点。"""

    id: str
    text: str
    level: str
    line: str
    status: str
    promise_events: tuple[str, ...] = ()
    progress_events: tuple[str, ...] = ()
    payoff_events: tuple[str, ...] = ()
    note: str = ""


def parse_promises(md: str) -> list[PromiseRow]:
    """解析台账数据行；列不足的行缺失列以空串补齐（列数问题由校验端点名）。"""
    rows: list[PromiseRow] = []
    for cells in _iter_raw_rows(md):
        vals = [_clean(c) for c in cells]
        vals += [""] * (9 - len(vals))  # 短行补齐，长行截断（列数由校验端拦）
        rows.append(PromiseRow(
            id=vals[0],
            text=vals[1],
            level=vals[2],
            line=vals[3],
            status=vals[4],
            promise_events=_split_events(vals[5]),
            progress_events=_split_events(vals[6]),
            payoff_events=_split_events(vals[7]),
            note=vals[8],
        ))
    return rows


def check_promises_write(
    promises_markdown: str,
    storyline_markdown: str,
) -> list[GuardViolation]:
    """校验一次台账写入（预估写入后内容），返回违规列表（空=放行）。

    Args:
        promises_markdown:  预估写入后内容（write_file=args.content；
                           edit_file=磁盘内容.replace(old, new, 1)）
        storyline_markdown: storyline.md 磁盘当前内容（锚点来源）
    """
    violations: list[GuardViolation] = []

    raw_rows = _iter_raw_rows(promises_markdown)
    if not raw_rows:
        violations.append(GuardViolation(
            "contract",
            "promises.md 缺许诺台账表或台账无数据行"
            "（九列：编号/许诺/层级/所属线/状态/许诺事件/推进事件/兑现事件/备注）",
        ))
        return violations

    event_names = set(extract_event_names(storyline_markdown))
    ids_seen: set[str] = set()

    for cells in raw_rows:
        if len(cells) != 9:
            violations.append(GuardViolation(
                "contract",
                f"台账行列数不齐（须为九列）：{'|'.join(cells)[:40]}…",
            ))
            continue
        pid, text, level, line, status, promise_ev, progress_ev, payoff_ev, note = (
            _clean(c) for c in cells
        )
        rowname = pid or (text[:12] or "未命名")

        if not _PROMISE_ID.match(pid):
            violations.append(GuardViolation(
                "contract", f"台账行「{rowname}」编号「{pid}」非法（须为 P{{n}} 形式，如 P1）",
            ))
        if pid in ids_seen:
            violations.append(GuardViolation(
                "contract", f"台账编号「{pid}」重复（全文件唯一）",
            ))
        ids_seen.add(pid)

        if not text:
            violations.append(GuardViolation("contract", f"台账行「{rowname}」许诺内容为空"))
        if level not in PROMISE_LEVELS:
            violations.append(GuardViolation(
                "contract",
                f"台账行「{rowname}」层级「{level}」非法（须为 {'/'.join(PROMISE_LEVELS)}）",
            ))
        if status not in PROMISE_STATES:
            violations.append(GuardViolation(
                "contract",
                f"台账行「{rowname}」状态「{status}」非法（须为 {'/'.join(PROMISE_STATES)}）",
            ))
        if status == "已放弃" and not note:
            violations.append(GuardViolation(
                "contract", f"台账行「{rowname}」状态为「已放弃」但备注为空（放弃须写原因，防悬置假账）",
            ))
        if status == "已兑现" and not _split_events(payoff_ev):
            violations.append(GuardViolation(
                "contract", f"台账行「{rowname}」状态为「已兑现」但兑现事件为空",
            ))
        if not _split_events(promise_ev):
            violations.append(GuardViolation(
                "contract", f"台账行「{rowname}」许诺事件为空（每条许诺必有埋设事件）",
            ))

        for ev in (*_split_events(promise_ev), *_split_events(progress_ev), *_split_events(payoff_ev)):
            if ev not in event_names:
                violations.append(GuardViolation(
                    "contract",
                    f"台账行「{rowname}」引用的事件「{ev}」在 storyline.md 事件表中不存在"
                    f"（事件名须逐字引用、创建后不改）",
                ))

    return violations


def find_dangling_promise_refs(
    storyline_current: str,
    storyline_projected: str,
    promises_markdown: str,
) -> list[GuardViolation]:
    """storyline 写入反查：本次修订删除（或改名）的事件不得被台账引用。

    事件改名视为删除（名称即锚点，与物品卡同规）。
    """
    removed = (
        set(extract_event_names(storyline_current))
        - set(extract_event_names(storyline_projected))
    )
    if not removed:
        return []

    violations: list[GuardViolation] = []
    for row in parse_promises(promises_markdown):
        for ev in (*row.promise_events, *row.progress_events, *row.payoff_events):
            if ev in removed:
                violations.append(GuardViolation(
                    "contract",
                    f"本次 storyline 修订删除了事件「{ev}」，但许诺台账 {row.id} 仍引用它。"
                    f"删除事件：先清掉台账该行对它的引用再删；改名不支持（事件名即锚点，创建后不改）——"
                    f"确需改名请三步走：先删台账该行全部引用 → 改 storyline 事件名 → 重写台账行",
                ))
    return violations


__all__ = [
    "PROMISE_LEVELS",
    "PROMISE_STATES",
    "PromiseRow",
    "check_promises_write",
    "find_dangling_promise_refs",
    "parse_promises",
]
