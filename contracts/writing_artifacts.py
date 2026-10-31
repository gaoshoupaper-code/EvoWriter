"""五段式创作产物契约（REQ-20261018-115508 FR-002/003/006/012）。

五段机制（DEC-001 单 agent 技能切换）：题材(theme) → 总纲(outline) →
卷纲(volume) → 章纲(chapter) → 正文(text)。产物文件树（workspace 根，
中文结构）：

    主题.md               ← 题材段定稿（约束级三件套 + 一句话主题）
    总纲.md               ← 总纲段定稿（核心纲 + 伏笔登记表）
    文风.md               ← 风格卡（章纲段前 agent 起草，DEC-009）
    卷纲/第NN卷.md
    章纲/第NNN章.md
    正文/第NNN章<章名>.md
    .writing/state.json   ← 会话/路由状态（可重建加速层，同 outline_pipeline 定位）

路径收权（FR-003）：kind → 相对路径的唯一映射在本模块——agent 语义工具与
executor 路由都消费这里，agent 永远不见路径。

目录即进度（DEC-002 + REQ-20261019-183150 定稿闸）：阶段推导只看文件
事实；主题定稿用结构校验（AC-003），总纲/卷纲定稿＝状态行「定稿」＋结构
校验双硬条件——草稿（起草中/无状态行）落盘不得切段。旧「文件存在即完成」
口径与逐点落盘流程直接冲突（2026-10-19 主线脉络事故：首笔草稿即切段）。

legacy（DEC-010）：旧五件套（storyline.md）、旧七步（outline/00-06）、
三步制（outline/世界观.md 等）一律只读——outline/ 目录存在即 legacy。

executor 管线层与 harness 工具共用本模块（跨端共享契约，零业务依赖）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

# ── 大段常量（五段 + legacy）──────────────────────────────────────
STAGE_THEME = "theme"
STAGE_OUTLINE = "outline"
STAGE_VOLUME = "volume"
STAGE_CHAPTER = "chapter"
STAGE_TEXT = "text"
STAGE_LEGACY = "legacy"

# 阶段条五段标签（desktop pipeline-steps 同名对齐，FR-011）
LABEL_THEME = "题材"
LABEL_OUTLINE = "总纲"
LABEL_VOLUME = "卷纲"
LABEL_CHAPTER = "章纲"
LABEL_TEXT = "正文"

STAGE_LABELS: dict[str, str] = {
    STAGE_THEME: LABEL_THEME,
    STAGE_OUTLINE: LABEL_OUTLINE,
    STAGE_VOLUME: LABEL_VOLUME,
    STAGE_CHAPTER: LABEL_CHAPTER,
    STAGE_TEXT: LABEL_TEXT,
}

# ── 产物 kind（语义工具的落盘语义，FR-003）────────────────────────
KIND_THEME = "theme"
KIND_OUTLINE = "outline"
KIND_VOLUME = "volume_outline"
KIND_CHAPTER = "chapter_outline"
KIND_TEXT = "chapter_text"
KIND_STYLE = "style"
KIND_CHARACTER = "character"

# ── 文件形态（五段产物树）────────────────────────────────────────
THEME_FILENAME = "主题.md"
MASTER_FILENAME = "总纲.md"
STYLE_FILENAME = "文风.md"
VOLUME_DIR = "卷纲"
CHAPTER_OUTLINE_DIR = "章纲"
TEXT_DIR = "正文"

# ── 人物卡（REQ-20261031-183257：一人一卡，DEC-001）───────────────
# 主角卡文件名固定「主角」（标题行写真实姓名）：新旧作品分流判定
# （DEC-017）＝人物/主角.md 存在，固定名免去解析卡内容。
CHARACTER_DIR = "人物"
PROTAGONIST_CARD_NAME = "主角"
PROTAGONIST_CARD_RELPATH = f"{CHARACTER_DIR}/{PROTAGONIST_CARD_NAME}.md"
# 人名即文件名：首字符为字母/中文/数字/下划线，全长 1-24；· 与 - 放行
# （BOSS·卷一·恶霸工头 / 欧阳-锋）；路径分隔符与 Windows 保留字符全拒。
CHARACTER_NAME_PATTERN = re.compile(r"^\w[\w·\-]{0,23}$")
CHARACTER_FILE_PATTERN = re.compile(r"^([^/\\]+)\.md$")

VOLUME_FILE_PATTERN = re.compile(r"^第(\d+)卷\.md$")
CHAPTER_OUTLINE_PATTERN = re.compile(r"^第(\d+)章\.md$")
# 标题组排除路径分隔符与反斜杠：章名是单段文件名（目录扫描天然不含 / \），
# 收紧堵死「正文/第001章/../../总纲.md」类经标题组携带穿越段的路径（删除
# 重做 P0 修复，REQ-20261030-211446）；对合法文件名零行为变化。
TEXT_FILE_PATTERN = re.compile(r"^第(\d+)章([^/\\]*)\.md$")

# ── 路由状态文件（可重建加速层）──────────────────────────────────
WRITING_STATE_SCHEMA_VERSION = 1
WRITING_STATE_RELPATH = ".writing/state.json"

# ── 阶段 → 可写产物 kind 集（FR-006 阶段闸唯一依据）──────────────
# 人物卡放行总纲/卷纲/正文三段（REQ-20261031-183257）：总纲段建主角卡与
# 各卷 BOSS 卡，卷纲段建配角卡＋滚弧光轨迹，正文段按审稿触发回写轨迹；
# 题材/章纲段不写人物卡（章纲段的人物信息内嵌在章纲卡，不落人物卡）。
_STAGE_KINDS: dict[str, set[str]] = {
    STAGE_THEME: {KIND_THEME},
    STAGE_OUTLINE: {KIND_OUTLINE, KIND_CHARACTER},
    STAGE_VOLUME: {KIND_VOLUME, KIND_CHARACTER},
    # 文风卡在章纲段起草（DEC-009：无文风.md 时 agent 起草+一次拍板）
    STAGE_CHAPTER: {KIND_CHAPTER, KIND_STYLE},
    STAGE_TEXT: {KIND_TEXT, KIND_CHARACTER},
}


def stage_allowed_kinds(stage: str) -> set[str]:
    """当前大段允许写入的产物 kind 集（stage_gate 与路由共用）。"""
    return set(_STAGE_KINDS.get(stage, set()))


# ── kind → 相对路径（唯一映射；agent 不见路径）────────────────────


def artifact_relpath(
    kind: str,
    *,
    volume: int | None = None,
    chapter: int | None = None,
    name: str | None = None,
) -> str:
    """kind+序号/人名 → 相对路径（唯一映射；agent 不见路径）。

    正文文件名不带章名（一章一文件不变式，C1 修复）：章名只活在文件头
    标题行「# 第NNN章 · <章名>」。read 端（parse_relpath/查找）对带章名
    的历史形态保持容忍，创建端只产规范名。
    """
    if kind not in (KIND_THEME, KIND_OUTLINE, KIND_STYLE, KIND_VOLUME, KIND_CHAPTER, KIND_TEXT, KIND_CHARACTER):
        raise ValueError(f"未知产物 kind: {kind!r}")
    if kind == KIND_CHARACTER:
        if volume is not None or chapter is not None:
            raise ValueError("character 产物不带卷号/章号")
        if not name or not CHARACTER_NAME_PATTERN.match(name):
            raise ValueError(f"非法人物卡名: {name!r}（1-24 字，字母/中文/数字与 · -）")
        return f"{CHARACTER_DIR}/{name}.md"
    if name is not None:
        raise ValueError(f"{kind} 产物不接收 name（仅 character 用）")
    if kind == KIND_THEME:
        if volume is not None or chapter is not None:
            raise ValueError("theme 产物不带卷号/章号")
        return THEME_FILENAME
    if kind == KIND_OUTLINE:
        if volume is not None or chapter is not None:
            raise ValueError("outline 产物不带卷号/章号")
        return MASTER_FILENAME
    if kind == KIND_STYLE:
        if volume is not None or chapter is not None:
            raise ValueError("style 产物不带卷号/章号")
        return STYLE_FILENAME
    if kind == KIND_VOLUME:
        if volume is None:
            raise ValueError("volume_outline 需要 volume 卷号")
        if chapter is not None:
            raise ValueError("volume_outline 不带章号")
        if volume < 1:
            raise ValueError(f"非法卷号: {volume}")
        return f"{VOLUME_DIR}/第{volume:02d}卷.md"
    if kind == KIND_CHAPTER:
        if chapter is None:
            raise ValueError("chapter_outline 需要 chapter 章号")
        if volume is not None:
            raise ValueError("chapter_outline 不带卷号")
        if chapter < 1:
            raise ValueError(f"非法章号: {chapter}")
        return f"{CHAPTER_OUTLINE_DIR}/第{chapter:03d}章.md"
    if kind == KIND_TEXT:
        if chapter is None:
            raise ValueError("chapter_text 需要 chapter 章号")
        if volume is not None:
            raise ValueError("chapter_text 不带卷号")
        if chapter < 1:
            raise ValueError(f"非法章号: {chapter}")
        return f"{TEXT_DIR}/第{chapter:03d}章.md"
    raise ValueError(f"未知产物 kind: {kind!r}")


@dataclass(frozen=True)
class ArtifactRef:
    """五段产物引用（相对路径解析结果）。"""

    kind: str
    volume: int | None = None
    chapter: int | None = None
    title: str | None = None
    rel: str = ""


def parse_relpath(rel: str | Path) -> ArtifactRef | None:
    """相对 workspace 路径 → 产物引用；非五段产物返回 None。"""
    p = Path(rel).as_posix().lstrip("/")
    if p == THEME_FILENAME:
        return ArtifactRef(kind=KIND_THEME, rel=p)
    if p == MASTER_FILENAME:
        return ArtifactRef(kind=KIND_OUTLINE, rel=p)
    if p == STYLE_FILENAME:
        return ArtifactRef(kind=KIND_STYLE, rel=p)
    parts = p.split("/", 1)
    if len(parts) != 2:
        return None
    d, name = parts
    if d == VOLUME_DIR:
        m = VOLUME_FILE_PATTERN.match(name)
        if m:
            return ArtifactRef(kind=KIND_VOLUME, volume=int(m.group(1)), rel=p)
    elif d == CHAPTER_OUTLINE_DIR:
        m = CHAPTER_OUTLINE_PATTERN.match(name)
        if m:
            return ArtifactRef(kind=KIND_CHAPTER, chapter=int(m.group(1)), rel=p)
    elif d == TEXT_DIR:
        m = TEXT_FILE_PATTERN.match(name)
        if m:
            title = m.group(2) or None
            return ArtifactRef(kind=KIND_TEXT, chapter=int(m.group(1)), title=title, rel=p)
    elif d == CHARACTER_DIR:
        m = CHARACTER_FILE_PATTERN.match(name)
        if m and CHARACTER_NAME_PATTERN.match(m.group(1)):
            return ArtifactRef(kind=KIND_CHARACTER, title=m.group(1), rel=p)
    return None


# ── 工作区分类（DEC-010：旧制一律 legacy）────────────────────────


def classify_writing_workspace(workspace: Path) -> str:
    """"legacy"（旧五件套 / 旧七步 / 三步制，只读）| "five"。"""
    workspace = Path(workspace)
    if (workspace / "storyline.md").exists():
        return "legacy"
    if (workspace / "outline").is_dir():
        return "legacy"
    return "five"


# ── 结构校验（定稿判定；纯静态 markdown 解析）────────────────────


def _headings(text: str) -> list[str]:
    return [ln.lstrip("#").strip() for ln in text.splitlines() if ln.lstrip().startswith("#")]


def _find_table(text: str, first_columns: list[str]) -> list[list[str]]:
    """定位首列匹配的 markdown 表，返回数据行（列序必须一致）。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells[: len(first_columns)] != first_columns:
            continue
        rows: list[list[str]] = []
        for follow in lines[i + 2 :]:  # 跳过分隔行
            f = follow.strip()
            if not (f.startswith("|") and f.endswith("|")):
                break
            rows.append([c.strip() for c in f.strip("|").split("|")])
        return rows
    return []


def validate_theme_final(text: str) -> list[str]:
    """题材定稿结构：一句话主题 + 约束级三件套（AC-003 推进依据）。"""
    errors: list[str] = []
    joined = "\n".join(_headings(text))
    for h in ("一句话主题", "核心爽点", "核心矛盾", "核心爽点剧情"):
        if h not in joined:
            errors.append(f"[主题] 缺必备小节「{h}」")
    return errors


def _count_master_volumes(text: str) -> int:
    """核心纲表行数 = 全书卷数（解析失败返回 0，路由按已有卷数+1 兜底）。"""
    rows = _find_table(text, ["卷"])
    return len([r for r in rows if r and r[0]])


def validate_master_final(text: str) -> list[str]:
    """总纲定稿结构：核心纲小节 + 卷表至少一行。"""
    errors: list[str] = []
    joined = "\n".join(_headings(text))
    if "核心纲" not in joined:
        errors.append("[总纲] 缺必备小节「核心纲」")
    if _count_master_volumes(text) < 1:
        errors.append("[总纲] 核心纲卷表为空")
    return errors


_VOLUME_RANGE_RE = re.compile(r"本卷章号范围\s*[:：]\s*(?:第\s*)?(\d+)\s*[-—–~～至]\s*(\d+)\s*章?")


def parse_volume_chapter_range(text: str) -> tuple[int, int] | None:
    """卷纲正文中的「本卷章号范围：第x-y章」——唯一一处且数值合法才有效。

    删除重做的卷级下游判定（REQ-20261030-211446 FR-002）与进度扫描共用；
    解析失败返回 None，调用方按 fail-safe 兜底。
    """
    ranges = _VOLUME_RANGE_RE.findall(text)
    if len(ranges) == 1:
        start, end = map(int, ranges[0])
        if 1 <= start <= end and end - start <= 1000:
            return start, end
    return None


def volume_draft_relpath(volume: int) -> str:
    """修订草稿不匹配正式卷纲文件名，现有列表仍可展示。"""
    if isinstance(volume, bool) or not isinstance(volume, int) or volume < 1:
        raise ValueError("卷号必须是正整数")
    return f"{VOLUME_DIR}/第{volume:02d}卷-修订草稿.md"


def volume_review_block_reason(workspace: Path, volume: int | None = None) -> str | None:
    """最新卷未定稿（状态行＋占位行）时拦截交审与切段。

    REQ-20261030-200030：卷纲机械校验（payoff 清单/收据）已整体移除，
    只保留「定稿状态行」这道段完成信号。
    """
    try:
        directory = Path(workspace) / VOLUME_DIR
        numbers = [
            int(match.group(1)) for path in directory.iterdir()
            if (match := VOLUME_FILE_PATTERN.match(path.name))
        ] if directory.is_dir() else []
        if numbers:
            volume = volume or max(numbers)
        if volume is None:
            return None
        text = _read(Path(workspace), artifact_relpath(KIND_VOLUME, volume=volume))
        if text is None:
            return None
        if not is_final_status(text) or _placeholder_blocks_final(text):
            return f"第 {volume} 卷尚未定稿。"
        return None
    except (OSError, UnicodeDecodeError):
        return None


# ── 定稿状态行（REQ-20261019-183150 FR-001：段完成信号）────────────

# 状态行窗口：与正文 parse_manuscript_status 同口径（文件头前 8 行）
_STATUS_LINE_WINDOW = 8


def _status_line_value(text: str) -> str | None:
    """文件头状态行取值（前 8 行内以「>」开头且含「状态」；无则 None）。"""
    for ln in text.splitlines()[:_STATUS_LINE_WINDOW]:
        s = ln.strip()
        if s.startswith(">") and "状态" in s:
            return s.lstrip("> ").strip()
    return None


def is_final_status(text: str) -> bool:
    """段产物定稿判定：状态行含「定稿」且不含否定（未定稿/非定稿/起草中/进行中）。

    严格口径（DEC-003）：无状态行不算定稿——忘写状态行的草稿只会
    「该切没切」（handoff 可救），不得反向误切段。「起草中/进行中」
    否定词（REQ-20261019-232617 DEC-003）：2026-10-19 事故样本状态行
    「起草中（核心爽点已定稿）」含「定稿」二字但整体是起草态。
    """
    value = _status_line_value(text) or ""
    if any(w in value for w in ("未定稿", "非定稿", "起草中", "进行中")):
        return False
    return "定稿" in value


# 占位小节检测（REQ-20261019-232617 DEC-003 辅助条件）：整行括号占位
# （「（未定稿，待第 2 步）」「（待三件套齐后压成）」）不算真实内容。
# 便宜启发式：只拦「整行括号且含未定稿/待字头」的正文，正常行内括号短句
# （如「（见第 3 卷）」）不误伤；拦不住的占位变体由人工段指针闸兜底。
_PLACEHOLDER_LINE = re.compile(r"^（[^）]{1,60}）$")


def _placeholder_blocks_final(text: str) -> bool:
    for ln in text.splitlines():
        s = ln.strip()
        if _PLACEHOLDER_LINE.match(s) and ("未定稿" in s or s[1] == "待"):
            return True
    return False


def parse_manuscript_status(text: str) -> str:
    """正文状态行 → "finalized"（人修定稿）| "draft"（初稿/无状态行）。"""
    value = _status_line_value(text) or ""
    return "finalized" if "人修定稿" in value else "draft"


# ── 进度扫描（目录即进度，DEC-002）───────────────────────────────


@dataclass
class ManuscriptState:
    """单章正文状态。"""

    rel: str
    finalized: bool = False


@dataclass
class WritingProgress:
    """五段产物扫描结果（快照函数，不落盘）。"""

    theme_done: bool = False
    outline_done: bool = False
    outline_volumes: int = 0
    volumes: list[int] = field(default_factory=list)
    volume_chapter_ranges: dict[int, tuple[int, int]] = field(default_factory=dict)
    chapter_outlines: list[int] = field(default_factory=list)
    chapter_texts: dict[int, ManuscriptState] = field(default_factory=dict)
    style_ready: bool = False
    # 人物卡（REQ-20261031-183257）：人名清单＋启用标志。人物卡不参与
    # 切段（derive_stage 不读这两项），仅供进度摘要与新旧分流判定。
    characters: list[str] = field(default_factory=list)
    character_mode: bool = False


def is_character_mode(workspace: Path) -> bool:
    """人物卡机制启用判定（DEC-017）：人物/主角.md 存在＝新作品走人物卡流程。"""
    return (Path(workspace) / PROTAGONIST_CARD_RELPATH).is_file()


def _read(workspace: Path, rel: str) -> str | None:
    p = workspace / rel
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def scan_writing_progress(workspace: Path) -> WritingProgress:
    """扫描五段产物树推导进度（快照函数，不落盘）。"""
    workspace = Path(workspace)
    prog = WritingProgress()

    theme_text = _read(workspace, THEME_FILENAME)
    # 定稿闸（REQ-20261019-232617 对齐总纲/卷纲 + 事故修复）：状态行「定稿」
    # + 结构校验 + 非占位三硬条件——2026-10-19 事故是小节标题齐全但正文全是
    # 占位的草稿被误判 theme_done 自动切段。
    prog.theme_done = bool(theme_text) and (
        is_final_status(theme_text)
        and validate_theme_final(theme_text) == []
        and not _placeholder_blocks_final(theme_text)
    )

    master_text = _read(workspace, MASTER_FILENAME)
    # 定稿闸（FR-001）：状态行「定稿」＋结构校验双硬条件——草稿/无状态行
    # 不切段；解析异常经 is_final_status 自然落到 False（fail-safe 方向）。
    if master_text:
        prog.outline_done = (
            bool(master_text.strip())
            and is_final_status(master_text)
            and validate_master_final(master_text) == []
            and not _placeholder_blocks_final(master_text)
        )
        prog.outline_volumes = _count_master_volumes(master_text)

    prog.style_ready = (workspace / STYLE_FILENAME).is_file()

    vol_dir = workspace / VOLUME_DIR
    if vol_dir.is_dir():
        for f in vol_dir.iterdir():
            m = VOLUME_FILE_PATTERN.match(f.name)
            if not m:
                continue
            # 定稿闸（FR-002）：只统计定稿卷（状态行＋无占位行）——
            # 草稿卷不计入，段停留卷纲可继续编辑该卷。
            text = _read(workspace, f.relative_to(workspace).as_posix())
            if text is None:
                continue
            if not (is_final_status(text) and not _placeholder_blocks_final(text)):
                continue
            volume = int(m.group(1))
            prog.volumes.append(volume)
            rng = parse_volume_chapter_range(text)
            if rng is not None:
                prog.volume_chapter_ranges[volume] = rng
        prog.volumes.sort()

    co_dir = workspace / CHAPTER_OUTLINE_DIR
    if co_dir.is_dir():
        for f in co_dir.iterdir():
            m = CHAPTER_OUTLINE_PATTERN.match(f.name)
            if m:
                prog.chapter_outlines.append(int(m.group(1)))
        prog.chapter_outlines.sort()

    tx_dir = workspace / TEXT_DIR
    if tx_dir.is_dir():
        for f in tx_dir.iterdir():
            m = TEXT_FILE_PATTERN.match(f.name)
            if not m:
                continue
            ch = int(m.group(1))
            text = _read(workspace, f.relative_to(workspace).as_posix()) or ""
            prog.chapter_texts[ch] = ManuscriptState(
                rel=f.relative_to(workspace).as_posix(),
                finalized=parse_manuscript_status(text) == "finalized",
            )

    ch_dir = workspace / CHARACTER_DIR
    if ch_dir.is_dir():
        for f in ch_dir.iterdir():
            m = CHARACTER_FILE_PATTERN.match(f.name)
            if m and CHARACTER_NAME_PATTERN.match(m.group(1)):
                prog.characters.append(m.group(1))
        prog.characters.sort()
        prog.character_mode = (ch_dir / f"{PROTAGONIST_CARD_NAME}.md").is_file()
    return prog


def derive_stage(prog: WritingProgress) -> str:
    """按进度推导下一大段（路由唯一依据；卷数解析失败按已有卷数+1 兜底）。"""
    if not prog.theme_done:
        return STAGE_THEME
    if not prog.outline_done:
        return STAGE_OUTLINE
    if not prog.volumes:
        return STAGE_VOLUME
    outlined = set(prog.chapter_outlines)
    written = set(prog.chapter_texts)
    if outlined - written:
        return STAGE_TEXT
    if not outlined:
        return STAGE_CHAPTER
    total = prog.outline_volumes or (max(prog.volumes) + 1)
    if len(prog.volumes) < total:
        return STAGE_VOLUME
    return STAGE_CHAPTER


def next_stage_on_confirm(workspace_path: Path, current: str) -> str:
    """段产物审查确认后的循环推进目标段（一卷→一批章纲→一批正文→回章纲…→下一卷）。

    与 derive_stage（纯产物推导）的区别：derive_stage 在「上一卷正文写完、
    新一卷卷纲刚定稿」时看不到章纲事实差异（outlined==written 两种场景同形），
    会把指针留在卷纲段——自动模式段审确认后 agent 继续写下一卷（2026-10-27
    线上 bug）。本函数按「确认时所在段」定向推进：

      volume  → chapter（一卷交审通过即进章纲摊本卷；无定稿卷时留卷纲）
      chapter → 细纲库存>0 → text；库存=0 且本卷章纲、正文齐 → 还有卷时
                进 volume，否则留在 chapter
      text    → 库存=0 → chapter（回章纲补纲）；库存>0 → 留在 text
      theme/outline 原样返回（不经段审通道）
    """
    if current == STAGE_VOLUME:
        if volume_review_block_reason(Path(workspace_path)):
            return STAGE_VOLUME
        prog = scan_writing_progress(Path(workspace_path))
        return STAGE_CHAPTER if prog.volumes else STAGE_VOLUME
    if current not in (STAGE_CHAPTER, STAGE_TEXT):
        return current
    prog = scan_writing_progress(Path(workspace_path))
    stock = set(prog.chapter_outlines) - set(prog.chapter_texts)
    if current == STAGE_TEXT:
        return STAGE_CHAPTER if not stock else STAGE_TEXT
    if stock:
        return STAGE_TEXT
    if not prog.volumes:
        return STAGE_CHAPTER
    span = prog.volume_chapter_ranges.get(prog.volumes[-1])
    if span is None:
        return STAGE_CHAPTER
    expected = set(range(span[0], span[1] + 1))
    if not expected.issubset(prog.chapter_outlines) or not expected.issubset(prog.chapter_texts):
        return STAGE_CHAPTER
    total = prog.outline_volumes or (prog.volumes[-1] + 1)
    return STAGE_VOLUME if len(prog.volumes) < total else STAGE_CHAPTER


# ── 创作双模式（REQ-20261026-191719 DEC-001/002/013）────────────────
MODE_INTERACTIVE = "interactive"   # 逐步确认（现状行为）
MODE_AUTO = "auto"                 # 自动完成（段内不提问，只在停等点停）
WRITING_MODES = (MODE_INTERACTIVE, MODE_AUTO)

# 自动模式适用段（DEC-002）：题材/总纲锁死逐步——全书根基段人工把关
AUTO_ELIGIBLE_STAGES = frozenset({STAGE_VOLUME, STAGE_CHAPTER, STAGE_TEXT})

# 正文批次默认章数（FR-003 / DEC-007：首批默认，后续沿用用户现场指定值）
DEFAULT_TEXT_BATCH_SIZE = 5


def stage_mode(workspace_path: Path, stage: str | None = None) -> str:
    """解析段的创作模式（FR-001/FR-011）。

    缺省与未设置段一律 interactive（DEC-013 存量迁移语义：升级后无任何段
    自动进入自动模式）；题材/总纲段锁死 interactive（DEC-002）。
    stage 缺省读人工段指针（无指针按目录扫描兜底，与 current_stage 同口径）。
    """
    from pathlib import Path as _P
    workspace_path = _P(workspace_path)
    target = stage if stage is not None else resolve_confirmed_stage(workspace_path)
    if target not in AUTO_ELIGIBLE_STAGES:
        return MODE_INTERACTIVE
    state = load_writing_state(workspace_path)
    mode = state.stage_modes.get(target, MODE_INTERACTIVE)
    return mode if mode in WRITING_MODES else MODE_INTERACTIVE


def set_stage_mode(workspace_path: Path, stage: str, mode: str) -> None:
    """写入段级模式（FR-008 唯一写入口；界面动作调用，模型不可写）。

    题材/总纲段与非法定值直接拒绝（fail-fast，不落盘）。
    """
    if stage not in AUTO_ELIGIBLE_STAGES:
        raise ValueError(f"段 {stage!r} 不支持自动模式（题材/总纲锁死逐步确认）")
    if mode not in WRITING_MODES:
        raise ValueError(f"mode 只能是 {'/'.join(WRITING_MODES)}，收到 {mode!r}")
    state = load_writing_state(Path(workspace_path))
    state.stage_modes[stage] = mode
    save_writing_state(Path(workspace_path), state)


def text_batch_size(workspace_path: Path) -> int:
    """正文批次大小（FR-003）：用户现场指定值优先，缺省 5。"""
    state = load_writing_state(Path(workspace_path))
    size = state.text_batch_size
    return size if isinstance(size, int) and size >= 1 else DEFAULT_TEXT_BATCH_SIZE


# ── 路由状态（DEC-002 handoff override 载体；可重建加速层）────────


class WritingState(BaseModel):
    schema_version: int = WRITING_STATE_SCHEMA_VERSION
    stage: str = STAGE_THEME
    confirmed_stage: str | None = Field(
        default=None,
        description="人工段指针（REQ-20261019-232617 FR-002）：当前段的唯一切段真相，"
        "只由界面人工确认动作写入（前进/跳段/回退统一入口），模型不可写；"
        "缺失时按目录扫描段兜底（FR-005 存量迁移语义）",
    )
    # 废弃兼容位（不再有写入方）：旧 harness stage_gate 读此字段切段。保留至
    # 新 harness 发版对齐后可删；新路由/闸门一律走 confirmed_stage。
    override_stage: str | None = Field(default=None)
    sessions: dict[str, int] = Field(default_factory=dict)
    active_threads: dict[str, str] = Field(default_factory=dict)
    active_traces: dict[str, str] = Field(default_factory=dict)
    conversation_forks: dict[str, int] = Field(
        default_factory=dict,
        description="会话粘性派生号（REQ-20260919-222403 FR-001）：客户端会话 id → 丢弃重开计数，"
        "同一会话全程复用同一 langgraph 线程；旧 sessions seq 制已停用",
    )
    stage_modes: dict[str, str] = Field(
        default_factory=dict,
        description="段级创作模式（REQ-20261026-191719 FR-008）：{stage: auto|interactive}，"
        "只由界面动作经 set_stage_mode 写入，模型不可写；缺省段=interactive"
        "（DEC-013 存量迁移：升级后无段自动进入 auto）",
    )
    text_batch_size: int = Field(
        default=DEFAULT_TEXT_BATCH_SIZE,
        description="正文批次章数（FR-003/DEC-007）：批次审查卡现场指定后回写，首批缺省 5",
    )
    last_stage_advance: dict[str, str] = Field(
        default_factory=dict,
        description="段审循环推进幂等标记：{trace_id, from, to}——同一待审 trace 的"
        "「确认通过」只推进一次（网络重试不二次跳段）；单槽，只留最近一次",
    )
    updated_at: str = ""


def load_writing_state(workspace_path: Path) -> WritingState:
    """容错读取：缺失 / 损坏 / schema 不符 → 默认状态（可按目录扫描重建）。"""
    path = Path(workspace_path) / WRITING_STATE_RELPATH
    if not path.is_file():
        return WritingState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = WritingState.model_validate(raw)
    except Exception:  # noqa: BLE001 — 状态层故障不得中断创作主流程
        return WritingState()
    if state.schema_version != WRITING_STATE_SCHEMA_VERSION:
        return WritingState()
    return state


def save_writing_state(workspace_path: Path, state: WritingState) -> None:
    """原子写（临时文件 + replace）；目录不存在则创建。"""
    path = Path(workspace_path) / WRITING_STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now(UTC).isoformat()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def resolve_confirmed_stage(workspace_path: Path) -> str:
    """人工段指针解析（REQ-20261019-232617 FR-002/005，唯一切段口径）。

    executor 路由、段规程装配与 harness 阶段闸共用本函数（同口径）：
    指针已设 → 指针说了算（DEC-006：产物变化不动摇，人工意志最高）；
    指针缺失 → 目录扫描段兜底（FR-005 存量迁移：升级前的书停在原段）。
    legacy 工作区由调用方先行 classify 拦截，不进本函数。
    """
    state = load_writing_state(workspace_path)
    if state.confirmed_stage in STAGE_LABELS:
        return state.confirmed_stage
    return derive_stage(scan_writing_progress(workspace_path))


__all__ = [
    "AUTO_ELIGIBLE_STAGES",
    "DEFAULT_TEXT_BATCH_SIZE",
    "MODE_AUTO",
    "MODE_INTERACTIVE",
    "WRITING_MODES",
    "CHAPTER_OUTLINE_DIR",
    "CHAPTER_OUTLINE_PATTERN",
    "CHARACTER_DIR",
    "CHARACTER_FILE_PATTERN",
    "CHARACTER_NAME_PATTERN",
    "KIND_CHAPTER",
    "KIND_CHARACTER",
    "KIND_OUTLINE",
    "KIND_STYLE",
    "KIND_TEXT",
    "KIND_THEME",
    "KIND_VOLUME",
    "LABEL_CHAPTER",
    "LABEL_OUTLINE",
    "LABEL_TEXT",
    "LABEL_THEME",
    "LABEL_VOLUME",
    "MASTER_FILENAME",
    "ManuscriptState",
    "PROTAGONIST_CARD_NAME",
    "PROTAGONIST_CARD_RELPATH",
    "STAGE_CHAPTER",
    "STAGE_LABELS",
    "STAGE_LEGACY",
    "STAGE_OUTLINE",
    "STAGE_TEXT",
    "STAGE_THEME",
    "STAGE_VOLUME",
    "STYLE_FILENAME",
    "TEXT_DIR",
    "TEXT_FILE_PATTERN",
    "THEME_FILENAME",
    "VOLUME_DIR",
    "VOLUME_FILE_PATTERN",
    "WRITING_STATE_RELPATH",
    "WRITING_STATE_SCHEMA_VERSION",
    "WritingProgress",
    "WritingState",
    "artifact_relpath",
    "classify_writing_workspace",
    "derive_stage",
    "is_character_mode",
    "is_final_status",
    "load_writing_state",
    "next_stage_on_confirm",
    "resolve_confirmed_stage",
    "parse_manuscript_status",
    "parse_relpath",
    "save_writing_state",
    "scan_writing_progress",
    "set_stage_mode",
    "stage_allowed_kinds",
    "stage_mode",
    "text_batch_size",
    "validate_master_final",
    "validate_theme_final",
    "volume_draft_relpath",
    "volume_review_block_reason",
]
