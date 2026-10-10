"""StorylineGraph — 从 storyline.md（单文件区块格式）派生 timeline 与全景事件数据。

纯后端、确定性生成（不依赖 LLM）：
  读 workspace/storyline.md（故事核心 + 一线一区块 + 事件表）→ 解析线/事件/交汇
  → 按事件「时序」列的 T 号排序 → 生成全景时间轴（timeline.md，agent 只读上下文）
  与跨线全景事件列表（前端大纲全景表数据源）。

设计契约（REQ-20260930-002231 建立，REQ-20260930-163019 FR-002 收敛）：
  - 直接操作 workspace 真实磁盘，绕过 agent 的 virtual fs / 权限系统（agent 权限零改动）；
  - storyline.md 只读不改；派生失败 → 跳过 + 日志，绝不抛异常（派生视图不能拖累编故事主流程）；
  - 旧格式（storyline/ 目录多文件）不再解析——降级返回 None；
  - 泳道图（storyline_graph.md）已停产——泳道页签随 FR-002 下线，timeline.md 因
    harness prompt 将其列为 agent 只读产物而保留派生。

解析依据 storybuilding_system.md 规范格式（线区块头 `## {线名} · {类型} · {状态}`
+ 线头两字段 + 事件表 `| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |`），
表头按列名定位，兼容列序漂移与粗体包裹；时序号直接读 T 号（含小数插入）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from contracts.hooks_contract import parse_hooks
from contracts.storyline_contract import extract_rhythm_field, parse_shape_slots

# ---------------------------------------------------------------------------
# 正则：宽松匹配规范格式，兼容 LLM 产出的常见漂移
# ---------------------------------------------------------------------------

# 线区块头：## {线名} · {类型} · {状态}（类型词允许粗体；分隔符宽容 · 与 •）
# 与 contracts._BLOCK_HEADER_RE 同构，但此处还需捕获线名/类型/状态三段。
_BLOCK_HEADER = re.compile(
    r"^##\s+(?P<name>[^#·•\n]+?)\s*[·•]\s*\**(?P<type>主线|支线|角色线|暗线)\**\s*(?:[·•]\s*(?P<status>.+?))?\s*$"
)

# 线头字段：- 主要地点：x / - 全局走向：x（兼容粗体）
_F_LOCATIONS = re.compile(r"^(?:-\s*)?(?:\*\*)?主要地点(?:\*\*)?\s*[：:]\s*(.+)")
_F_DIRECTION = re.compile(r"^(?:-\s*)?(?:\*\*)?全局走向(?:\*\*)?\s*[：:]\s*(.+)")

# 时序号：T1 / t1 / T12.5 / 12.5
_T_NUM = re.compile(r"^[Tt]?\s*(\d+(?:\.\d+)?)\s*$")

# 事件表表头特征：行内同时含「时序」「事件」列名
_TABLE_HEADER_HINT = re.compile(r"^\|.*时序.*\|.*事件.*\|")
_TABLE_SEPARATOR = re.compile(r"^\|[\s|:-]+\|$")


def _split_cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


# ---------------------------------------------------------------------------
# 数据模型（名称即锚点：id 承载线名/事件名，schema 字段含义不变——FR-005）
# ---------------------------------------------------------------------------


@dataclass
class Event:
    """一个事件节点。"""

    id: str  # 事件名（名称即锚点）
    name: str  # 同 id（兼容展示）
    type: str = ""  # 冲突/危机/反转…
    stage: str = ""  # 发展/终局…
    location: str = ""
    characters: str = ""
    storylines: tuple[str, ...] = ()  # 全部参与线名（主属线 + 交汇线），多条=交汇事件
    t_num: float = 0.0  # 时序号数值（T1 → 1.0；T12.5 → 12.5；缺时序按 0 兜底）
    t_raw: str = ""  # 时序号原文（"T1"/"T12.5"）——全景表按原文展示（DEC-010）
    desc: str = ""
    doc_order: int = 0  # 在 storyline.md 中的行号（解析兜底与稳定 tiebreak）
    tension: int | None = None  # 张力标注 1~5（REQ-20261010-000638；旧大纲缺失为 None）
    payoff: str = ""  # 爽点标注 ""/—/小/大


@dataclass
class Storyline:
    """一条故事线。"""

    id: str  # 线名（名称即锚点）
    name: str  # 同 id
    type: str  # 主线/支线/角色线/暗线
    status: str = ""  # 活跃/已收束…
    direction: str = ""  # 全局走向
    locations: str = ""  # 主要地点（线级）
    key_events: list[str] = field(default_factory=list)  # 线内事件名序列（按 T 号排序）


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


def _parse_table_header_map(header_row: str) -> dict[str, int] | None:
    """按列名定位事件表列索引；非事件表表头返回 None。"""
    if not _TABLE_HEADER_HINT.match(header_row.strip()):
        return None
    col_map: dict[str, int] = {}
    for ci, cell in enumerate(_split_cells(header_row)):
        cell_clean = cell.replace("**", "").strip()
        if "时序" in cell_clean:
            col_map["时序"] = ci
        elif "事件" in cell_clean:
            col_map["事件"] = ci
        elif "类型" in cell_clean:
            col_map["类型"] = ci
        elif "阶段" in cell_clean:
            col_map["阶段"] = ci
        elif "地点" in cell_clean:
            col_map["地点"] = ci
        elif "角色" in cell_clean:
            col_map["角色"] = ci
        elif "交汇" in cell_clean:
            col_map["交汇"] = ci
        elif "张力" in cell_clean:
            col_map["张力"] = ci
        elif "爽点" in cell_clean:
            col_map["爽点"] = ci
        elif "描述" in cell_clean:
            col_map["描述"] = ci
    if "事件" not in col_map:
        return None
    return col_map


def _parse_storyline_md(text: str) -> tuple[list[Storyline], dict[str, Event]]:
    """解析 storyline.md → (故事线列表[按区块出现顺序], 事件字典 {事件名: Event})。"""
    storylines: dict[str, Storyline] = {}
    story_order: list[str] = []
    events: dict[str, Event] = {}
    current_line: str | None = None
    table_cols: dict[str, int] | None = None  # 当前事件表的列映射

    def cell(cells: list[str], key: str) -> str:
        idx = table_cols.get(key) if table_cols else None
        if idx is None or idx >= len(cells):
            return ""
        v = cells[idx].replace("**", "").strip()
        return "" if v in ("—", "-", "–") else v

    for idx, raw in enumerate(text.splitlines()):
        line = raw.strip()

        # 线区块头
        m = _BLOCK_HEADER.match(line)
        if m:
            name = m.group("name").strip().replace("**", "")
            current_line = name
            if name not in storylines:
                storylines[name] = Storyline(
                    id=name,
                    name=name,
                    type=m.group("type").strip(),
                    status=(m.group("status") or "").strip().replace("**", ""),
                )
                story_order.append(name)
            table_cols = None
            continue

        # 事件表表头 / 分隔行 / 数据行
        if line.startswith("|"):
            if _TABLE_SEPARATOR.match(line):
                continue
            header_map = _parse_table_header_map(line)
            if header_map is not None:
                table_cols = header_map
                continue
            if table_cols is None or current_line is None:
                continue  # 非事件表（如故事核心里的杂表）——跳过
            cells = _split_cells(line)
            name = cell(cells, "事件")
            if not name:
                continue
            t_raw = cell(cells, "时序")
            t_match = _T_NUM.match(t_raw)
            cross_raw = cell(cells, "交汇")
            cross_names = tuple(
                p.strip().replace("**", "") for p in re.split(r"[、,，/]", cross_raw) if p.strip()
            ) if cross_raw else ()
            ev = events.get(name)
            if ev is None:
                ev = Event(id=name, name=name, doc_order=idx)
                events[name] = ev
            ev.type = ev.type or cell(cells, "类型")
            ev.stage = ev.stage or cell(cells, "阶段")
            ev.location = ev.location or cell(cells, "地点")
            ev.characters = ev.characters or cell(cells, "角色")
            ev.desc = ev.desc or cell(cells, "描述")
            tension_raw = cell(cells, "张力")
            if tension_raw and tension_raw.isdigit() and 1 <= int(tension_raw) <= 5:
                ev.tension = int(tension_raw)
            ev.payoff = ev.payoff or cell(cells, "爽点")
            if t_match:
                ev.t_num = float(t_match.group(1))
            if t_raw:
                ev.t_raw = ev.t_raw or t_raw
            # 主属线 = 事件行所在区块；交汇线补充进参与线集合
            members = [current_line, *cross_names]
            seen: list[str] = []
            for s in members:
                if s and s not in seen:
                    seen.append(s)
            ev.storylines = tuple(seen)
            continue

        # 线头字段（区块内、表格外的列表行）
        if current_line is not None and current_line in storylines:
            sl = storylines[current_line]
            if not sl.locations and (fm := _F_LOCATIONS.match(line)):
                sl.locations = fm.group(1).strip()
                continue
            if not sl.direction and (fm := _F_DIRECTION.match(line)):
                sl.direction = fm.group(1).strip()
                continue

    # 线内事件序列：主属事件（区块内出现的）+ 交汇进入的事件，按 T 号排序
    for sl in storylines.values():
        own = [ev.id for ev in events.values() if ev.storylines[:1] == (sl.id,)]
        joined = [ev.id for ev in events.values() if sl.id in ev.storylines[1:]]
        sl.key_events = sorted(
            [*own, *joined],
            key=lambda eid: (events[eid].t_num, events[eid].doc_order),
        )

    return [storylines[n] for n in story_order], events


# ---------------------------------------------------------------------------
# 全局时序：直接读 T 号 → 排序重整化为连续序号
# ---------------------------------------------------------------------------


def _assign_global_t(events: dict[str, Event]) -> dict[str, int]:
    """按 T 号升序（tiebreak=文档出现序）生成连续全局序号（1-based）。

    主产物中的 T 号只是排序键（允许小数插入碎化）；这里输出重整化后的展示序。
    """
    ordered = sorted(events.values(), key=lambda e: (e.t_num, e.doc_order))
    return {ev.id: rank for rank, ev in enumerate(ordered, start=1)}


# ---------------------------------------------------------------------------
# 全景时间轴（timeline.md 派生，agent 只读上下文）
# ---------------------------------------------------------------------------


def build_timeline_markdown(
    storylines: list[Storyline], events: dict[str, Event], t_map: dict[str, int]
) -> str:
    """生成全景时间轴 markdown：全部事件按 T 号升序的索引视图（无描述列）。

    列 = 序 | 事件 | 所属线 | 地点 | 阶段；交汇事件的所属线列出全部参与线（顿号分隔）。
    """
    lines = [
        "# 全景时间轴",
        "",
        "> 程序派生视图：由 storyline.md 自动生成，请勿手改。",
        "",
        "| 序 | 事件 | 所属线 | 地点 | 阶段 |",
        "|---|------|--------|------|------|",
    ]
    ordered = sorted(events.values(), key=lambda e: (t_map.get(e.id, 0), e.doc_order))
    for ev in ordered:
        seq = t_map.get(ev.id, 0)
        lines_of = "、".join(ev.storylines) if ev.storylines else "—"
        lines.append(f"| {seq} | {ev.name} | {lines_of} | {ev.location or '—'} | {ev.stage or '—'} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


@dataclass
class StorylineGraphData:
    """storyline.md 的结构化解析结果。

    timeline_markdown = 全景时间轴文本（timeline.md 派生源，agent 只读上下文）；
    storylines/events/t_map = 结构化数据（全景表/内部消费）。
    """

    timeline_markdown: str
    storylines: list[Storyline]
    events: dict[str, Event]
    t_map: dict[str, int]


def _read_text(path: Path) -> str:
    """读取文件文本，UTF-8 失败时回退 GB18030（GBK 超集）。

    agent 在中文 Windows 下偶尔会写入 GBK 字节，严格 UTF-8 解码会抛 UnicodeDecodeError，
    让派生链路整体失败。与 thread_store._read_text 保持一致的容错策略。
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030", errors="replace")


def build_storyline_graph_data(workspace_path: Path) -> StorylineGraphData | None:
    """解析 storyline.md（单文件）→ 结构化数据 + timeline markdown。

    确定性、纯后端。无产物、旧格式（无区块）或解析失败返回 None
    （派生视图，绝不因自身问题上抛）。
    """
    source = workspace_path / "storyline.md"
    if not source.exists():
        return None
    try:
        storylines, events = _parse_storyline_md(_read_text(source))
        if not storylines or not events:
            return None
        t_map = _assign_global_t(events)
        return StorylineGraphData(
            timeline_markdown=build_timeline_markdown(storylines, events, t_map),
            storylines=storylines,
            events=events,
            t_map=t_map,
        )
    except Exception as exc:  # noqa: BLE001 — 派生视图：解析异常不阻断
        print(f"[storyline_graph] 解析失败（{type(exc).__name__}: {exc}）")
        return None


def build_panorama_events(workspace_path: Path) -> list[Event] | None:
    """跨线全景事件列表（FR-003 大纲全景表数据源）。

    全部故事线的事件按时序号升序合并（tiebreak=文档出现序），每事件带：
    t_raw（原 T 号）、storylines（交汇=全部参与线）、type/characters/location/desc。
    无产物、旧格式或解析失败返回 None——调用方据此降级，不抛异常。
    """
    data = build_storyline_graph_data(workspace_path)
    if data is None:
        return None
    return sorted(data.events.values(), key=lambda e: (e.t_num, e.doc_order))


# ---------------------------------------------------------------------------
# 节奏数据（REQ-20261010-000638 FR-006：张力曲线 / 形态锚点 / 许诺进度）
# ---------------------------------------------------------------------------


@dataclass
class RhythmPoint:
    """张力曲线上的一个点：T 号原文 + 张力/爽点 + 主属线。

    surface=True 标记暗线「浮出时点」（FR-006）：区块状态为 可浮出/浮空 的
    暗线，其最后一个事件即浮出时点，前端打标。
    """

    t: str
    name: str
    tension: int | None
    payoff: str
    line: str
    surface: bool = False


@dataclass
class ShapeSlotView:
    """目标形态槽位（解析自故事核心「节奏曲线」，contracts 判定器唯一实现）。"""

    slot: str
    op: str
    values: tuple[int, ...]
    twin_peak: bool


@dataclass
class HookView:
    """钩子登记行（hooks.md 解析结果，contracts 判定器唯一实现）。"""

    id: str
    text: str
    level: str
    type: str
    status: str
    plant_events: tuple[str, ...]
    progress_events: tuple[str, ...]
    payoff_events: tuple[str, ...]
    note: str


@dataclass
class RhythmData:
    """节奏数据包（DEC-009：主线对比形态；合成曲线=明线按 T 合并取最大张力）。"""

    mainline: list[RhythmPoint]
    synthesis: list[RhythmPoint]
    dark: dict[str, list[RhythmPoint]]
    shape_slots: list[ShapeSlotView]
    hooks: list[HookView]


def _t_value(t_raw: str) -> float:
    m = _T_NUM.match(t_raw.strip()) if t_raw else None
    return float(m.group(1)) if m else 0.0


def build_rhythm_data(workspace_path: Path) -> RhythmData | None:
    """派生节奏数据包（FR-006 / DEC-009/015）。

    全部事件都无张力标注（旧大纲）→ None：前端据此显示「无节奏数据」
    降级提示，不阻塞其余面板。派生失败同样返回 None（派生视图不上抛）。
    """
    try:
        data = build_storyline_graph_data(workspace_path)
        if data is None:
            return None
        text = _read_text(workspace_path / "storyline.md")
        if not any(ev.tension is not None for ev in data.events.values()):
            return None

        mainline: list[RhythmPoint] = []
        synthesis: list[RhythmPoint] = []
        dark: dict[str, list[RhythmPoint]] = {}
        for sl in data.storylines:
            own = sorted(
                (ev for ev in data.events.values() if ev.storylines[:1] == (sl.id,)),
                key=lambda e: (e.t_num, e.doc_order),
            )
            if sl.type == "暗线":  # 暗线不计入读者体验合成曲线（DEC-009）
                dark_pts = [
                    RhythmPoint(ev.t_raw, ev.name, ev.tension, ev.payoff, sl.id) for ev in own
                ]
                # 浮出时点打标（FR-006）：状态 可浮出/浮空 的暗线，最后事件即浮出点
                if dark_pts and any(k in sl.status for k in ("可浮出", "浮出", "浮空")):
                    last = dark_pts[-1]
                    dark_pts[-1] = RhythmPoint(
                        last.t, last.name, last.tension, last.payoff, last.line, surface=True,
                    )
                dark[sl.id] = dark_pts
                continue
            pts = [RhythmPoint(ev.t_raw, ev.name, ev.tension, ev.payoff, sl.id) for ev in own]
            synthesis.extend(pts)
            if sl.type == "主线":
                mainline.extend(pts)

        # 合成曲线按 T 序合并；同一时点多线并存取张力最大者（读者体感由最强线决定）。
        # 仅对双方 T 号都可解析且数值相等的点合并——不可解析 T（空/带注记）不参与
        # 同点合并，否则全部坍缩在 0.0 上互相吞并、事件静默消失。
        synthesis.sort(key=lambda p: _t_value(p.t))
        dedup: list[RhythmPoint] = []
        for p in synthesis:
            if (
                dedup
                and _T_NUM.match(dedup[-1].t.strip())
                and _T_NUM.match(p.t.strip())
                and _t_value(dedup[-1].t) == _t_value(p.t)
            ):
                if (p.tension or 0) > (dedup[-1].tension or 0):
                    dedup[-1] = p
            else:
                dedup.append(p)

        shape_slots = [
            ShapeSlotView(s.slot, s.op, s.values, s.twin_peak)
            for s in (parse_shape_slots(extract_rhythm_field(text) or "") or ())
        ]

        hooks: list[HookView] = []
        hooks_path = workspace_path / "hooks.md"
        if hooks_path.exists():
            hooks = [
                HookView(
                    row.id, row.hook, row.level, row.type, row.status,
                    row.plant_events, row.progress_events, row.payoff_events, row.note,
                )
                for row in parse_hooks(_read_text(hooks_path))
            ]

        return RhythmData(mainline, dedup, dark, shape_slots, hooks)
    except Exception as exc:  # noqa: BLE001 — 派生视图：解析异常不阻断
        print(f"[storyline_graph] 节奏数据派生失败（{type(exc).__name__}: {exc}）")
        return None


def is_stale(workspace_path: Path) -> bool:
    """派生产物（timeline.md）相对源文件是否缺失或过期。

    判定：timeline.md 不存在，或 storyline.md 的 mtime 晚于它。
    供「读取时按需生成」兜底——源文件变了就重生成，保证视图与数据一致。
    """
    timeline_path = workspace_path / "timeline.md"
    source = workspace_path / "storyline.md"
    if not source.exists():
        return False  # 无源文件（storybuilding 尚未产出）——不触发生成
    if not timeline_path.exists():
        return True
    return source.stat().st_mtime > timeline_path.stat().st_mtime


def generate_storyline_graph(workspace_path: Path) -> None:
    """从 workspace/storyline.md 派生 timeline.md（全景时间轴，agent 只读上下文）。

    确定性、纯后端。任何解析异常都吞掉并打日志——派生视图，
    绝不因自身问题阻断 storybuilding 主流程。
    """
    data = build_storyline_graph_data(workspace_path)
    if data is None:
        # 无源文件（常态静默）或旧格式/解析失败（build 内部已打日志）——不覆盖既有产物
        return
    try:
        timeline_path = workspace_path / "timeline.md"
        timeline_path.write_text(data.timeline_markdown, encoding="utf-8")
        print(
            f"[storyline_graph] 已生成 {timeline_path.name}"
            f"（{len(data.storylines)} 故事线 / {len(data.events)} 事件 / T01–T{len(data.t_map):02d}）"
        )
    except Exception as exc:  # noqa: BLE001 — 派生视图：写盘失败不上抛
        print(f"[storyline_graph] 跳过生成（{type(exc).__name__}: {exc}）")
