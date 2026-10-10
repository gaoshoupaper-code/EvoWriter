"""storyline.md 结构契约判定器（REQ-20260930-194437 FR-003/004/005）。

REQ-20261009-182730：移除事件数量等值校验（原 DEC-007 口径）与事件类型词
白名单——数量/类型词改由 harness 侧观测模式承载（只记日志不拦截），参考
区间与参考词表随 harness 包演进；contracts 只保留结构语法判定。

「判定器唯一实现」原则（沿用 DEC-011）：契约判定放 contracts，
executor 测试（``assert_storyline_v2_contract``）与 harness 运行时护栏
（StorylineContractGuardMiddleware）共用本模块，两侧不得各自实现。

运行时口径（DEC-007 / DEC-010；数量口径由 REQ-20261009-182730 取代）：
  - 结构规则（区块头类型词、线头两字段、事件表存在且有数据行、列数一致、
    T 号合法、禁 S/E/G 与旧字段残留）只校验**新增或变更区块**——resume
    场景的存量历史瑕疵不误伤合法续写。
  - 唯一性规则（线名、事件名）全局生效。
  - 事件数量与事件类型词不校验、不拦截（harness 侧观测，见护栏观测模式）。
  - 最终结局：初构首次落盘后不得修改或删除（磁盘态对比，跨装配幂等）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .storybuilding_quota import LINE_TYPES

# ── 共享正则 ────────────────────────────────────────────────

_BLOCK_HEADER = re.compile(
    r"(?m)^##\s+([^#·•\n]+?)\s*[·•]\s*\**(" + "|".join(LINE_TYPES) + r")\**(?:\s*[·•]|\s*$)"
)
# 通用区块头（类型词任意，用于运行时识别「写了区块但类型词非法」的场景；
# 类型段贪婪且不越过 ·，状态段必须带 · 分隔符——防「暗线 · 暂伏」被截成类型「暗」）
_GENERIC_BLOCK_HEADER = re.compile(
    r"(?m)^##\s+([^#\n·•]+?)\s*[·•]\s*([^·•\n]+?)(?:\s*[·•]\s*([^\n]*))?\s*$"
)
_T_NUM = re.compile(r"^[Tt]\s*\d+(?:\.\d+)?$")
_LEGACY_IDS = re.compile(r"\b[Ss]\d{2}\b|\b[Ee]\d{3}\b|\b[Gg]\d{2}\b")
_LEGACY_FIELDS = re.compile(r"(?m)^(?:-\s*)?(?:\*\*)?(事件组|所属故事线|关键事件)(?:\*\*)?\s*[：:]")
_TABLE_HEADER_HINT = re.compile(r"^\|.*时序.*\|.*事件.*\|")
_TABLE_SEPARATOR = re.compile(r"\|[\s|:-]+\|")
_ENDING_RE = re.compile(r"(?m)^(?:-\s*)?(?:\*\*)?最终结局(?:\*\*)?\s*[：:]\s*(.+?)\s*$")

# （REQ-20261009-182730）事件类型词白名单 EVENT_TYPES 与事件数模板
# EVENT_COUNT_TEMPLATE 已移除：数量/类型词不再工程校验，参考区间与词表
# 由 harness 包观测配置持有（DEC-004/DEC-006），观测逻辑见护栏中间件。


@dataclass(frozen=True)
class GuardViolation:
    """一条护栏违规：rule 用于防死循环计数，message 用于拒绝提示。"""

    rule: str  # "contract" | "ending"
    message: str


# ── 区块解析 ────────────────────────────────────────────────


def _split_cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _clean(cell: str) -> str:
    return cell.replace("**", "").strip()


@dataclass(frozen=True)
class _Block:
    name: str
    type: str  # 区块头类型段原文（未归一）；非法词在此暴露
    text: str


def _parse_blocks(md: str) -> list[_Block]:
    """按通用区块头切分（类型词非法的区块也纳入，供结构规则点名）。"""
    headers = list(_GENERIC_BLOCK_HEADER.finditer(md))
    blocks: list[_Block] = []
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(md)
        blocks.append(_Block(
            name=_clean(m.group(1)),
            type=_clean(m.group(2)),
            text=md[m.start():end],
        ))
    return blocks


def _data_rows(block_text: str) -> tuple[list[str], str | None]:
    """返回（数据行列表, 表头行）；无表头时数据行为空。"""
    rows = [ln.strip() for ln in block_text.splitlines() if ln.strip().startswith("|")]
    header_row = next((ln for ln in rows if _TABLE_HEADER_HINT.match(ln)), None)
    if header_row is None:
        return [], None
    data = [
        ln for ln in rows
        if not _TABLE_HEADER_HINT.match(ln) and not _TABLE_SEPARATOR.fullmatch(ln)
    ]
    return data, header_row


def _col_index(header_row: str, keyword: str) -> int | None:
    for ci, cell in enumerate(_split_cells(header_row)):
        if keyword in _clean(cell):
            return ci
    return None


# ── 终稿契约断言（executor 测试沿用，行为与原实现一致）─────────


def assert_storyline_v2_contract(md: str) -> list[str]:
    """校验 storyline.md 是否满足 FR-001 结构契约，返回违规描述列表。"""
    violations: list[str] = []

    if not md.strip():
        return ["storyline.md 为空"]

    # 1. 至少一个线区块头
    headers = list(_BLOCK_HEADER.finditer(md))
    if not headers:
        violations.append("未找到线区块头（## {线名} · {类型} · {状态}）")

    # 2. 编号残留（S01 / E001 / G12）
    if _LEGACY_IDS.search(md):
        violations.append("存在 S/E/G 旧编号残留（名称即锚点，不应有编号）")

    # 3. 旧字段残留（事件组/所属故事线/关键事件）
    if _LEGACY_FIELDS.search(md):
        violations.append("存在旧字段残留（事件组/所属故事线/关键事件）")

    # 4. 逐区块校验：线头两字段 + 事件表八列 + 时序合法
    for i, m in enumerate(headers):
        name = m.group(1).strip()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(md)
        block = md[m.start():end]

        if "主要地点" not in block:
            violations.append(f"区块「{name}」缺线头字段「主要地点」")
        if "全局走向" not in block:
            violations.append(f"区块「{name}」缺线头字段「全局走向」")

        rows = [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]
        data_rows = [
            ln for ln in rows
            if not _TABLE_HEADER_HINT.match(ln) and not re.fullmatch(r"\|[\s|:-]+\|", ln)
        ]
        header_row = next((ln for ln in rows if _TABLE_HEADER_HINT.match(ln)), None)
        if header_row is None:
            violations.append(f"区块「{name}」缺事件表（表头须含「时序」「事件」列）")
            continue
        if len(data_rows) == 0:
            violations.append(f"区块「{name}」事件表无数据行")
            continue

        expect_cols = header_row.strip().strip("|").count("|") + 1
        for ln in data_rows:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) != expect_cols:
                violations.append(f"区块「{name}」事件行列数不齐：{ln[:40]}…")
                break
            t_raw = cells[0].replace("**", "")
            if t_raw and not _T_NUM.match(t_raw):
                violations.append(f"区块「{name}」时序号非法（{t_raw}，应为 T1/T12.5 形式）")
                break

    # 5. 名称唯一：线名与事件名不得重复
    line_names = [m.group(1).strip().replace("**", "") for m in headers]
    if len(line_names) != len(set(line_names)):
        violations.append("线名重复")
    event_names = extract_event_names(md)
    if len(event_names) != len(set(event_names)):
        violations.append("事件名重复")

    return violations


# ── 事件名提取（公共：唯一性检查与 object_contract 锚点校验共用，唯一实现）──


def extract_event_names(md: str) -> list[str]:
    """提取 storyline.md 全部事件名（事件表数据行「事件」列，按出现序）。

    表头含「时序」「事件」的行是表头，全竖线分隔行是分隔线，均跳过；
    其余表格行取第二列为事件名（与原唯一性检查同口径）。
    """
    names: list[str] = []
    for ln in md.splitlines():
        s = ln.strip()
        if not s.startswith("|") or _TABLE_HEADER_HINT.match(s) or _TABLE_SEPARATOR.fullmatch(s):
            continue
        cells = _split_cells(s)
        if len(cells) >= 2 and _clean(cells[1]):
            names.append(_clean(cells[1]))
    return names


# ── 公共结构解析（REQ-20261009-182730：harness 观测侧复用，判定器唯一原则）──


@dataclass(frozen=True)
class LineBlock:
    """一个线区块的结构化解析结果（纯结构，无语义口径）。

    events 为事件表数据行按序的 (类型列原文, 是否交汇)——类型列缺失/留空
    时原文为空串；「交汇」列非空即 True。参考区间与参考词表等语义口径
    不进 contracts，由 harness 观测配置持有。
    """

    name: str
    type: str
    text: str
    events: tuple[tuple[str, bool], ...]


def iter_line_blocks(md: str) -> list[LineBlock]:
    """按区块头切分 storyline.md，返回各线区块的结构解析（含事件行）。

    与判定器共用同一套区块/表格切分（唯一实现）；无表区块 events 为空元组。
    """
    blocks: list[LineBlock] = []
    for b in _parse_blocks(md):
        data, header_row = _data_rows(b.text)
        events: list[tuple[str, bool]] = []
        if header_row is not None:
            type_idx = _col_index(header_row, "类型")
            crossing_idx = _col_index(header_row, "交汇")
            for ln in data:
                cells = _split_cells(ln)
                type_word = (
                    _clean(cells[type_idx])
                    if type_idx is not None and type_idx < len(cells)
                    else ""
                )
                crossing = (
                    crossing_idx is not None
                    and crossing_idx < len(cells)
                    and bool(_clean(cells[crossing_idx]))
                )
                events.append((type_word, crossing))
        blocks.append(LineBlock(b.name, b.type, b.text, tuple(events)))
    return blocks


# ── 运行时写入校验（写前拦截判定入口）────────────────────────


def extract_final_ending(md: str) -> str | None:
    """提取故事核心「最终结局」字段值；缺失返回 None。"""
    m = _ENDING_RE.search(md)
    return _clean(m.group(1)) if m else None


def check_storyline_write(current: str, projected: str) -> list[GuardViolation]:
    """对比磁盘现状与预估写入后内容，返回护栏违规列表（空=放行）。

    Args:
        current:   storyline.md 磁盘当前内容（初构时为空串）
        projected: 预估写入后内容（write_file=args.content；
                   edit_file=磁盘内容.replace(old, new, 1)）
    """
    violations: list[GuardViolation] = []

    # ── 最终结局不可变（FR-005；磁盘态对比，初构无基线不校验）──
    cur_ending = extract_final_ending(current)
    if cur_ending is not None:
        proj_ending = extract_final_ending(projected)
        if proj_ending is None:
            violations.append(GuardViolation(
                "ending", "storyline.md 的「最终结局」不可删除（初构后钉死）",
            ))
        elif proj_ending != cur_ending:
            violations.append(GuardViolation(
                "ending", f"storyline.md 的「最终结局」不可修改（初构后钉死，当前为「{cur_ending}」）",
            ))

    # ── 区块切分与范围判定 ──
    cur_blocks = _parse_blocks(current)
    proj_blocks = _parse_blocks(projected)
    cur_texts = {b.name: b.text for b in cur_blocks}
    cur_names = set(cur_texts)

    # 初构写入必须包含至少一个线区块（后续写入由完整性护栏防丢区块）
    if not current.strip() and not proj_blocks:
        violations.append(GuardViolation(
            "contract", "初构写入 storyline.md 必须包含至少一个线区块（## {线名} · {类型} · {状态}）",
        ))

    # ── 结构规则：仅新增或变更区块（DEC-010 范围化口径）──
    for b in proj_blocks:
        is_new = b.name not in cur_names
        is_changed = (not is_new) and cur_texts[b.name] != b.text
        if not (is_new or is_changed):
            continue

        if b.type not in LINE_TYPES:
            violations.append(GuardViolation(
                "contract", f"区块「{b.name}」类型词非法（{b.type}，须为 主线/支线/角色线/暗线）",
            ))

        if "主要地点" not in b.text:
            violations.append(GuardViolation("contract", f"区块「{b.name}」缺线头字段「主要地点」"))
        if "全局走向" not in b.text:
            violations.append(GuardViolation("contract", f"区块「{b.name}」缺线头字段「全局走向」"))

        if _LEGACY_IDS.search(b.text):
            violations.append(GuardViolation(
                "contract", f"区块「{b.name}」存在 S/E/G 旧编号残留（名称即锚点，不应有编号）",
            ))
        if _LEGACY_FIELDS.search(b.text):
            violations.append(GuardViolation(
                "contract", f"区块「{b.name}」存在旧字段残留（事件组/所属故事线/关键事件）",
            ))

        data, header_row = _data_rows(b.text)
        if header_row is None:
            violations.append(GuardViolation(
                "contract", f"区块「{b.name}」缺事件表（表头须含「时序」「事件」列）",
            ))
            continue
        if not data:
            violations.append(GuardViolation("contract", f"区块「{b.name}」事件表无数据行"))
            continue

        expect_cols = header_row.strip().strip("|").count("|") + 1
        for ln in data:
            cells = _split_cells(ln)
            if len(cells) != expect_cols:
                violations.append(GuardViolation(
                    "contract", f"区块「{b.name}」事件行列数不齐：{ln[:40]}…",
                ))
                break
            t_raw = _clean(cells[0])
            if t_raw and not _T_NUM.match(t_raw):
                violations.append(GuardViolation(
                    "contract", f"区块「{b.name}」时序号非法（{t_raw}，应为 T1/T12.5 形式）",
                ))
                break

    # ── 唯一性规则：全局（线名、事件名）──
    proj_line_names = [b.name for b in proj_blocks]
    if len(proj_line_names) != len(set(proj_line_names)):
        violations.append(GuardViolation("contract", "线名重复（全文件唯一，创建后不改）"))

    all_event_names = extract_event_names(projected)
    if len(all_event_names) != len(set(all_event_names)):
        violations.append(GuardViolation("contract", "事件名重复（全文件唯一，创建后不改）"))

    return violations


__all__ = [
    "GuardViolation",
    "LineBlock",
    "assert_storyline_v2_contract",
    "check_storyline_write",
    "extract_event_names",
    "extract_final_ending",
    "iter_line_blocks",
]
