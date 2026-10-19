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

# ── 文件形态（五段产物树）────────────────────────────────────────
THEME_FILENAME = "主题.md"
MASTER_FILENAME = "总纲.md"
STYLE_FILENAME = "文风.md"
VOLUME_DIR = "卷纲"
CHAPTER_OUTLINE_DIR = "章纲"
TEXT_DIR = "正文"

VOLUME_FILE_PATTERN = re.compile(r"^第(\d+)卷\.md$")
CHAPTER_OUTLINE_PATTERN = re.compile(r"^第(\d+)章\.md$")
TEXT_FILE_PATTERN = re.compile(r"^第(\d+)章(.*)\.md$")

# ── 路由状态文件（可重建加速层）──────────────────────────────────
WRITING_STATE_SCHEMA_VERSION = 1
WRITING_STATE_RELPATH = ".writing/state.json"

# ── 阶段 → 可写产物 kind 集（FR-006 阶段闸唯一依据）──────────────
_STAGE_KINDS: dict[str, set[str]] = {
    STAGE_THEME: {KIND_THEME},
    STAGE_OUTLINE: {KIND_OUTLINE},
    STAGE_VOLUME: {KIND_VOLUME},
    # 文风卡在章纲段起草（DEC-009：无文风.md 时 agent 起草+一次拍板）
    STAGE_CHAPTER: {KIND_CHAPTER, KIND_STYLE},
    STAGE_TEXT: {KIND_TEXT},
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
) -> str:
    """kind+序号 → 相对路径（唯一映射；agent 不见路径）。

    正文文件名不带章名（一章一文件不变式，C1 修复）：章名只活在文件头
    标题行「# 第NNN章 · <章名>」。read 端（parse_relpath/查找）对带章名
    的历史形态保持容忍，创建端只产规范名。
    """
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


def validate_volume_final(text: str) -> list[str]:
    """卷纲定稿结构：落成模板四个必备小节（REQ-20261019-183150 FR-002）。"""
    errors: list[str] = []
    joined = "\n".join(_headings(text))
    for h in ("发展阶段", "爽点节奏表", "爽点质检表", "卷末钩子"):
        if h not in joined:
            errors.append(f"[卷纲] 缺必备小节「{h}」")
    return errors


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
    """段产物定稿判定：状态行含「定稿」且不含否定（未定稿/非定稿）。

    严格口径（DEC-003）：无状态行不算定稿——忘写状态行的草稿只会
    「该切没切」（handoff 可救），不得反向误切段。
    """
    value = _status_line_value(text) or ""
    if "未定稿" in value or "非定稿" in value:
        return False
    return "定稿" in value


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
    chapter_outlines: list[int] = field(default_factory=list)
    chapter_texts: dict[int, ManuscriptState] = field(default_factory=dict)
    style_ready: bool = False


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
    prog.theme_done = bool(theme_text) and validate_theme_final(theme_text) == []

    master_text = _read(workspace, MASTER_FILENAME)
    # 定稿闸（FR-001）：状态行「定稿」＋结构校验双硬条件——草稿/无状态行
    # 不切段；解析异常经 is_final_status 自然落到 False（fail-safe 方向）。
    if master_text:
        prog.outline_done = (
            bool(master_text.strip())
            and is_final_status(master_text)
            and validate_master_final(master_text) == []
        )
        prog.outline_volumes = _count_master_volumes(master_text)

    prog.style_ready = (workspace / STYLE_FILENAME).is_file()

    vol_dir = workspace / VOLUME_DIR
    if vol_dir.is_dir():
        for f in vol_dir.iterdir():
            m = VOLUME_FILE_PATTERN.match(f.name)
            if not m:
                continue
            # 定稿闸（FR-002）：只统计定稿卷（状态行＋四小节结构）——
            # 草稿卷不计入，段停留卷纲可继续编辑该卷。
            text = _read(workspace, f.relative_to(workspace).as_posix())
            if text is None:
                continue
            if not (is_final_status(text) and validate_volume_final(text) == []):
                continue
            prog.volumes.append(int(m.group(1)))
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


# ── 路由状态（DEC-002 handoff override 载体；可重建加速层）────────


class WritingState(BaseModel):
    schema_version: int = WRITING_STATE_SCHEMA_VERSION
    stage: str = STAGE_THEME
    override_stage: str | None = Field(default=None, description="用户点名跳段（handoff 写入，消费即清）")
    sessions: dict[str, int] = Field(default_factory=dict)
    active_threads: dict[str, str] = Field(default_factory=dict)
    active_traces: dict[str, str] = Field(default_factory=dict)
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


__all__ = [
    "CHAPTER_OUTLINE_DIR",
    "CHAPTER_OUTLINE_PATTERN",
    "KIND_CHAPTER",
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
    "is_final_status",
    "load_writing_state",
    "parse_manuscript_status",
    "parse_relpath",
    "save_writing_state",
    "scan_writing_progress",
    "stage_allowed_kinds",
    "validate_master_final",
    "validate_theme_final",
    "validate_volume_final",
]
