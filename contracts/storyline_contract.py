"""storyline.md 结构契约判定器（REQ-20260930-194437 FR-003/004/005）。

REQ-20261009-182730：移除事件数量等值校验（原 DEC-007 口径）与事件类型词
白名单——数量/类型词改由 harness 侧观测模式承载（只记日志不拦截），参考
区间与参考词表随 harness 包演进；contracts 只保留结构语法判定。

REQ-20261010-000638 新增结构契约（FR-001/002/003；v42 谱系合并版）：
  - 故事核心六字段（Logline/设计原则/核心主题/类型基调/节奏曲线/最终结局）
    初构必齐；「设计原则」初构落盘后钉死
  - 「最终结局」为草稿制（v42 进化口径，卡②A）：不钉死、可随叙事修订，
    契约不再校验结局不可变
  - 「节奏曲线」四槽位锚点语法（首事件/前段末/中点谷/终局，值 1~5）：
    初构与改写该字段时校验（字段本身不钉死，语法必须合法）
  - 新增/变更区块事件表须含「张力」（1~5 整数）「爽点」（—/小/大）列

「判定器唯一实现」原则（沿用 DEC-011）：契约判定放 contracts，
executor 测试（``assert_storyline_v2_contract``）与 harness 运行时护栏
（StorylineContractGuardMiddleware）共用本模块，两侧不得各自实现。

运行时口径（DEC-007 / DEC-010；数量口径由 REQ-20261009-182730 取代）：
  - 结构规则（区块头类型词、线头两字段、事件表存在且有数据行、列数一致、
    T 号合法、禁 S/E/G 与旧字段残留、张力/爽点列）只校验**新增或变更区块**——
    resume 场景的存量历史瑕疵（含 DEC-015 旧 schema 八列区块）不误伤合法续写。
  - 唯一性规则（线名、事件名）全局生效。
  - 事件数量与事件类型词不校验、不拦截（harness 侧观测，见护栏观测模式）。
  - 设计原则：初构首次落盘后不得修改或删除（磁盘态对比，跨装配幂等）。
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

# ── REQ-20261010-000638：故事核心六字段 / 设计原则钉死 / 槽位形态 / 张力爽点 ──

CORE_FIELDS = ("Logline", "设计原则", "核心主题", "类型基调", "节奏曲线", "最终结局")

_DESIGN_PRINCIPLE_RE = re.compile(
    r"(?m)^(?:-\s*)?(?:\*\*)?设计原则(?:\*\*)?\s*[：:][ \t]*([^\n]+?)[ \t]*$"
)
_RHYTHM_FIELD_RE = re.compile(
    r"(?m)^(?:-\s*)?(?:\*\*)?节奏曲线(?:\*\*)?\s*[：:][ \t]*([^\n]+?)[ \t]*$"
)

# 槽位锚点：`首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5`
# 槽名固定四个；值 1~5；双值（双峰）只允许终局槽；运算符缺省视为 ≈
_SHAPE_SLOT_NAMES = ("首事件", "前段末", "中点谷", "终局")
_SHAPE_SLOT_TOKEN = re.compile(
    r"^(首事件|前段末|中点谷|终局)\s*(双峰)?\s*(>=|<=|≥|≤|≈|=)?\s*([1-5])(?:\s*[,，]\s*([1-5]))?$"
)
_SHAPE_EXAMPLE = "首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5"
_OP_NORMALIZE = {">=": ">=", "≥": ">=", "<=": "<=", "≤": "<=", "≈": "≈", "=": "=", None: "≈"}

_TENSION_RE = re.compile(r"^[1-5]$")
PAYOFF_VALUES = ("—", "-", "–", "小", "大")  # 空串=普通事件，也合法


@dataclass(frozen=True)
class ShapeSlot:
    """一个形态槽位：槽名 + 运算符 + 目标值（终局槽允许双值=双峰）。"""

    slot: str  # 首事件 / 前段末 / 中点谷 / 终局
    op: str  # >= | <= | ≈ | =（缺省 ≈）
    values: tuple[int, ...]  # 1~2 个 1~5 值
    twin_peak: bool = False


def parse_shape_slots(value: str) -> list[ShapeSlot] | None:
    """解析「节奏曲线」字段值为四槽位锚点；任一 token 非法返回 None。"""
    tokens = [t.strip() for t in re.split(r"[·•；;]", value) if t.strip()]
    if not tokens:
        return None
    slots: list[ShapeSlot] = []
    seen: set[str] = set()
    for tok in tokens:
        m = _SHAPE_SLOT_TOKEN.match(tok)
        if m is None:
            return None
        name, twin, op, v1, v2 = m.groups()
        if name in seen:
            return None
        seen.add(name)
        if v2 is not None and (name != "终局" or not twin):
            return None  # 双值只允许终局槽且须带「双峰」标记
        if twin and v2 is None:
            return None  # 「双峰」必须给两个值
        values = (int(v1),) if v2 is None else (int(v1), int(v2))
        slots.append(ShapeSlot(name, _OP_NORMALIZE[op], values, bool(twin)))
    if seen != set(_SHAPE_SLOT_NAMES):
        return None  # 四槽位必须齐
    return slots


def _shape_violation() -> GuardViolation:
    return GuardViolation(
        "contract",
        f"「节奏曲线」槽位语法非法（四槽位：{'/'.join(_SHAPE_SLOT_NAMES)}，值 1~5；"
        f"合法写法如：{_SHAPE_EXAMPLE}）",
    )


def extract_design_principle(md: str) -> str | None:
    """提取故事核心「设计原则」字段值；缺失返回 None。"""
    m = _DESIGN_PRINCIPLE_RE.search(md)
    return _clean(m.group(1)) if m else None


def extract_rhythm_field(md: str) -> str | None:
    """提取故事核心「节奏曲线」字段值；缺失返回 None。"""
    m = _RHYTHM_FIELD_RE.search(md)
    return _clean(m.group(1)) if m else None


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
class EventRow:
    """事件表数据行的结构化解析（纯结构，无语义口径）。

    t_num 为时序号数值（T1 → 1.0；缺时序为 None）；tension/payoff 为列原文
    （缺失/留空为空串）——刻度与合法值域等语义口径不进 contracts，由
    harness 观测配置持有。
    """

    name: str
    type_word: str = ""
    crossing: bool = False
    t_num: float | None = None
    tension: str = ""
    payoff: str = ""


@dataclass(frozen=True)
class LineBlock:
    """一个线区块的结构化解析结果（纯结构，无语义口径）。

    events 为事件表数据行按序的 EventRow；参考区间、参考词表、张力刻度等
    语义口径不进 contracts，由 harness 观测配置持有。
    """

    name: str
    type: str
    text: str
    events: tuple[EventRow, ...]


def iter_line_blocks(md: str) -> list[LineBlock]:
    """按区块头切分 storyline.md，返回各线区块的结构解析（含事件行）。

    与判定器共用同一套区块/表格切分（唯一实现）；无表区块 events 为空元组。
    """
    blocks: list[LineBlock] = []
    for b in _parse_blocks(md):
        data, header_row = _data_rows(b.text)
        events: list[EventRow] = []
        if header_row is not None:
            name_idx = _col_index(header_row, "事件")
            t_idx = _col_index(header_row, "时序")
            type_idx = _col_index(header_row, "类型")
            crossing_idx = _col_index(header_row, "交汇")
            tension_idx = _col_index(header_row, "张力")
            payoff_idx = _col_index(header_row, "爽点")

            def _cell(cells: list[str], idx: int | None) -> str:
                return _clean(cells[idx]) if idx is not None and idx < len(cells) else ""

            for ln in data:
                cells = _split_cells(ln)
                t_raw = _cell(cells, t_idx)
                t_num = None
                if t_raw:
                    digits = re.search(r"\d+(?:\.\d+)?", t_raw)
                    t_num = float(digits.group(0)) if digits else None
                events.append(EventRow(
                    name=_cell(cells, name_idx),
                    type_word=_cell(cells, type_idx),
                    crossing=crossing_idx is not None
                    and crossing_idx < len(cells)
                    and bool(_clean(cells[crossing_idx])),
                    t_num=t_num,
                    tension=_cell(cells, tension_idx),
                    payoff=_cell(cells, payoff_idx),
                ))
        blocks.append(LineBlock(b.name, b.type, b.text, tuple(events)))
    return blocks


# ── 运行时写入校验（写前拦截判定入口）────────────────────────


def check_storyline_write(current: str, projected: str) -> list[GuardViolation]:
    """对比磁盘现状与预估写入后内容，返回护栏违规列表（空=放行）。

    Args:
        current:   storyline.md 磁盘当前内容（初构时为空串）
        projected: 预估写入后内容（write_file=args.content；
                   edit_file=磁盘内容.replace(old, new, 1)）
    """
    violations: list[GuardViolation] = []

    # ── 设计原则不可变（FR-001 合并版；最终结局为草稿制不校验，见 docstring）──
    cur_principle = extract_design_principle(current)
    if cur_principle is not None:
        proj_principle = extract_design_principle(projected)
        if proj_principle is None:
            violations.append(GuardViolation(
                "design_principle", "storyline.md 的「设计原则」不可删除（初构后钉死）",
            ))
        elif proj_principle != cur_principle:
            violations.append(GuardViolation(
                "design_principle",
                f"storyline.md 的「设计原则」不可修改（初构后钉死，当前为「{cur_principle}」）",
            ))

    # ── 初构六字段必齐（值非空）+ 槽位形态语法（FR-001/002）──
    if not current.strip():
        for field in CORE_FIELDS:
            if not re.search(
                rf"(?m)^(?:-\s*)?(?:\*\*)?{re.escape(field)}(?:\*\*)?\s*[：:][ \t]*[^\n]", projected
            ):
                violations.append(GuardViolation(
                    "contract",
                    f"故事核心缺「{field}」字段或值为空（六字段：{'/'.join(CORE_FIELDS)}，值须与字段同行非空）",
                ))
        proj_rhythm = extract_rhythm_field(projected)
        if proj_rhythm is not None and parse_shape_slots(proj_rhythm) is None:
            violations.append(_shape_violation())
    else:
        # 写入「节奏曲线」字段（新增或改写）：值必须槽位合法（字段本身不钉死）
        cur_rhythm = extract_rhythm_field(current)
        proj_rhythm = extract_rhythm_field(projected)
        if proj_rhythm is not None and proj_rhythm != cur_rhythm:
            if parse_shape_slots(proj_rhythm) is None:
                violations.append(_shape_violation())

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

        # 张力/爽点列（FR-003；新增/变更区块须含且取值合法）
        tension_idx = _col_index(header_row, "张力")
        payoff_idx = _col_index(header_row, "爽点")
        if tension_idx is None or payoff_idx is None:
            violations.append(GuardViolation(
                "contract",
                f"区块「{b.name}」事件表缺「张力」「爽点」列"
                f"（新增/变更区块须为十列：…/交汇/张力/爽点/描述）",
            ))
        else:
            for ln in data:
                cells = _split_cells(ln)
                tension = _clean(cells[tension_idx]) if tension_idx < len(cells) else ""
                payoff = _clean(cells[payoff_idx]) if payoff_idx < len(cells) else ""
                if not _TENSION_RE.match(tension):
                    violations.append(GuardViolation(
                        "contract",
                        f"区块「{b.name}」张力取值「{tension}」非法（张力须为 1~5 整数）：{ln[:30]}…",
                    ))
                    break
                if payoff != "" and payoff not in PAYOFF_VALUES:
                    violations.append(GuardViolation(
                        "contract",
                        f"区块「{b.name}」爽点取值「{payoff}」非法（爽点须为 —/小/大，普通事件留空）：{ln[:30]}…",
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
    "CORE_FIELDS",
    "PAYOFF_VALUES",
    "EventRow",
    "GuardViolation",
    "LineBlock",
    "ShapeSlot",
    "assert_storyline_v2_contract",
    "check_storyline_write",
    "extract_design_principle",
    "extract_event_names",
    "extract_rhythm_field",
    "iter_line_blocks",
    "parse_shape_slots",
]
