"""save_artifact 工具 — 语义化落盘 + 校验编辑（REQ-20261018-115508 FR-003/004）。

路径收权（FR-003）：kind+卷号/章号 → 路径的唯一映射在 contracts 层，agent
不见路径；工具 schema 不含任何路径参数（AC-005）。

校验编辑四道保险（DEC-007，ZCode Edit 同款机制——打歪的补丁应用不上去，
只会报错重试，fail-closed）：
  1. 先读后改：mode=edit 与对已存在文件的 mode=full 都要求本装配周期内
     先 read_artifact 过该产物（读取登记在 ctx.artifact_read_registry）。
  2. 精确+多级归一匹配：先逐字精确；失败后按行去尾空白再试；再失败按
     引号归一（中英引号视为相同）再试。
  3. 锚点唯一性：匹配多于一处 → 报错要求带更多上下文，不落盘。
  4. 失败不落盘：任何校验不过，原文件字节不变，返回错误供重试。

重试上限退化兜底（DEC-007）：同一产物编辑连续失败 3 次 → 拒绝继续 edit，
强制引导 mode=full 提交完整内容（工具无法凭空补全全文，退化以「堵 edit、
导流 full」实现）；成功落盘后计数清零。

追加模式（正文单元生长）：mode="append" 把本次完整正文单元追加到文件末尾，不做
锚点匹配——正文一段 50 字上下，拿短段当锚点极易撞到重复句，撞车累积会触发
上面的重试上限、被强制退回整篇重写，分段落盘就此失效。append 落盘成功后
回执带本批序号与正文本体累计字数，给执笔人明确的节奏感与收束信号。full /
append 落盘成功后把该产物登记进读取登记——内容刚由本工具写出，后续
append/edit 视为已读，不必为此重读整章。

整章写入（REQ-20261125-154544）：正文包按段写作时，起草不再走 full/append 的
整批路径。四个写作工具只收段落；文件头工具把状态改为「初稿」时调用这里的整章
写入——改文件头两行，并把本轮攒下的段落一次写入。走同一条保存通道与取证。

每次「锚点→替换」经既有 TraceMiddleware 进 trace（L4 审计，DEC-008）。
落盘成功后经 ctx.trace_recorder 冻结 ArtifactRevision——进化侧产物库的唯一
摄入源（REQ-20261019-203500 FR-001）：内容在手直记，不重读文件；取证失败
静默降级留痕，绝不影响落盘（DEC-004）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from itertools import groupby
from pathlib import Path
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.sentence_draft import (
    HEADER_TOOL, compose_chapter, draft_state, note_flushed, package_uses_sentences, pending_sentences, rejection,
    split_chapter,
)
from contracts.writing_artifacts import (
    KIND_CHAPTER,
    KIND_CHARACTER,
    KIND_OUTLINE,
    KIND_STYLE,
    KIND_TEXT,
    KIND_THEME,
    KIND_VOLUME,
    artifact_relpath,
)

logger = logging.getLogger(__name__)

# 编辑重试上限：同一产物连续失败到该次数后封死 edit、强制 full（DEC-007）
_EDIT_FAILURE_CAP = 3
_PROGRESS_LINE = re.compile(r"> 进度：已落 ([0-9]+) 批")
_UNKNOWN_PROGRESS_REJECTION = (
    "[save_artifact 拒绝·文件未变] 历史次数未知，不补造累计次数。"
    "请移除本次提交新增的数字进度行，保留原稿后重试。"
)

_TEXT_UNIT_GUIDANCE = (
    "普通正文保持 300-400 字左右，在自然停顿落盘；"
    "已匹配 Skill 的完整场景可超出普通批长，不拆批。"
)

SAVE_ARTIFACT_TOOL_DESCRIPTION = """按产物种类落盘（不接触文件路径）。每个点被作者确认后立即落盘。

kind 取值：theme / outline / style / volume_outline(+volume) / chapter_outline(+chapter) / chapter_text(+chapter) / character(+name 人名，主角卡固定 name="主角")。
正文文件名由章号决定（不带章名——章名写进文件头标题行「# 第NNN章 · <章名>」）。

三种模式：
- mode="full"：content = 该产物的完整新内容（新建文件、推翻重做大改用）。
- mode="append"：content = 本次完整写作单元（普通批次或 Skill 完整场景）。
  首个单元用 full 建文件，后续每个单元用 append；场景占 N 批仍只保存一次。
  回执的追加序号仅计本轮 append 次数，不含首次 full，也不是 Skill 篇幅预算。
  正文新建按实际保存的批数建立进度；已有标准进度由工具维护，修订不增加总数。
  append 不做锚点匹配，避免短段重复导致整章重写。
  正文新建和追加必须注明 unit_kind：ordinary（普通单元）或 skill_scene（匹配的完整技能场景）。
  skill_scene 填与当前剧情匹配的技能原名；普通单元在自然停顿处收束，不硬切句。
  已有正文的 full 全量修订与 edit 沿用原规则，不按普通单元限长。
- mode="edit"（默认，推荐）：anchor + replacement——anchor 是文件里的原文片段
  （须逐字一致且在文件中唯一，建议带上标题行等上下文）；replacement 是替换后
  的文本（新增一张卡 = anchor 所在区块末尾 + 新卡片内容；状态行变更同理）。
  编辑前必须先 read_artifact 读过该产物；锚点不唯一/找不到会报错且文件不变，
  带更多上下文重试；连续失败 3 次后改用 mode="full" 提交完整内容。

仅卷纲支持 draft=true：读取并编辑修订草稿，须先 read_artifact(draft=true)。

落盘内容遵守文件头两行约定：标题行 + 状态日期行；约束级/参考级分区。"""

# 正文包按段写作时换用这份说明：起草已交给写作工具，这里不再提供整批路径。
SENTENCE_PACKAGE_TOOL_DESCRIPTION = """保存或修改本章正文（不接触文件路径）。

起草不用这个工具：正文用四个写作工具逐段写，写完用 write_chapter_header 把状态改为「初稿」，系统在那时把整章写入文件。
这里只做两件事：
- mode="full"：content = 本章完整的新内容，推翻重写整章时用。先用 read_artifact 读过本章；
  content 从两行文件头写起：「# 第NNN章 · 章名」和「> 状态：初稿｜日期」。
- mode="edit"（默认）：anchor + replacement——anchor 是文件里的原文片段（须逐字一致且在文件中唯一），
  replacement 是替换后的文本。编辑前必须先 read_artifact 读过本章；锚点不唯一或找不到会报错且文件不变，
  带更多上下文重试；连续失败 3 次后改用 mode="full"。"""


class SaveArtifactInput(BaseModel):
    kind: str = Field(description="产物种类：theme/outline/style/volume_outline/chapter_outline/chapter_text/character")
    name: str | None = Field(default=None, description="人名（character 必填；主角卡固定「主角」）")
    volume: int | None = Field(default=None, ge=1, description="卷号（volume_outline 必填）")
    chapter: int | None = Field(default=None, ge=1, description="章号（chapter_outline/chapter_text 必填）")
    mode: str = Field(
        default="edit",
        description='"edit"（默认，锚点校验编辑）| "full"（整文件替换）| "append"（追加到文件末尾，普通批次或Skill完整场景）',
    )
    anchor: str | None = Field(default=None, description="edit 模式：要替换的原文片段（须唯一）")
    replacement: str | None = Field(default=None, description="edit 模式：替换后的文本")
    content: str | None = Field(default=None, description="full 模式：完整新内容；append 模式：本次完整正文单元（普通批次或Skill完整场景）")
    draft: bool = Field(default=False, description="仅卷纲：以修订草稿为编辑基线")
    unit_kind: Literal["ordinary", "skill_scene"] | None = Field(
        default=None, description="正文新建/追加：ordinary普通单元；skill_scene当前剧情匹配的完整技能场景。修订与其他产物无需填写。",
    )
    skill_name: str | None = Field(default=None, description="skill_scene 填匹配场景的技能原名；普通单元留空。")


class CharacterOwnerSaveInput(SaveArtifactInput):
    character_role: Literal["protagonist", "antagonist", "supporting"] | None = Field(
        default=None, description="保存人物设定时填写人物归属：主角、反派或配角")


def _quote_norm(s: str) -> str:
    return (s.replace("\u201c", '"').replace("\u201d", '"')
             .replace("\u2018", "'").replace("\u2019", "'"))


def _find_line_span(text: str, anchor: str, *, quote: bool) -> list[tuple[int, int]] | None:
    """按行归一匹配：返回 [行起止偏移] 候选（行级整段替换）；无匹配返回 None。

    归一规则：每行去尾部空白（含换行）后比较；quote=True 时引号归一。
    """
    file_lines = text.splitlines(keepends=True)
    anchor_lines = anchor.splitlines()
    norm = _quote_norm if quote else (lambda s: s)
    a = [norm(ln.rstrip()) for ln in anchor_lines]
    if not a or any(not ln and i in (0, len(a) - 1) for i, ln in enumerate(a)):
        return None
    f = [norm(ln.rstrip()) for ln in file_lines]
    spans: list[tuple[int, int]] = []
    n, m = len(f), len(a)
    for i in range(n - m + 1):
        if f[i:i + m] == a:
            start = sum(len(ln) for ln in file_lines[:i])
            last = file_lines[i + m - 1]
            end = start + sum(len(ln) for ln in file_lines[i:i + m])
            if not anchor.endswith(("\r", "\n")):
                end -= len(last) - len(last.rstrip("\r\n"))
            spans.append((start, end))
    return spans or None


def find_edit_match(content: str, anchor: str) -> tuple[int, int] | None | str:
    """锚点匹配三档：精确 → 行归一 → 行归一+引号归一。

    Returns:
        (start, end)  唯一匹配的原文偏移区间
        None          找不到
        "ambiguous"   多于一处
    """
    count = content.count(anchor)
    if count == 1:
        i = content.index(anchor)
        return (i, i + len(anchor))
    if count > 1:
        return "ambiguous"
    for quote in (False, True):
        spans = _find_line_span(content, anchor, quote=quote)
        if spans is None:
            continue
        if len(spans) == 1:
            return spans[0]
        if len(spans) > 1:
            return "ambiguous"
    return None


def apply_edit(content: str, anchor: str, replacement: str) -> tuple[str, int] | str:
    """应用一次校验编辑。成功返回 (新内容, 替换处数)；失败返回错误说明。"""
    # read_artifact exposes LF even when the manuscript uses CRLF or mixed endings.
    visible = content.replace("\r\n", "\n").replace("\r", "\n")
    match = find_edit_match(visible, anchor.replace("\r\n", "\n").replace("\r", "\n"))
    if match == "ambiguous":
        return "锚点在文件中不唯一：带上标题行等更多上下文重试，或改用 mode=full。"
    if match is None:
        return "锚点在文件中找不到（须与原文逐字一致）：重新 read_artifact 后按当前内容重试。"
    start, end = match
    if "\r" in content:
        # Each CRLF before a boundary occupies one extra character in the source.
        for boundary in re.finditer("\r\n", content):
            if boundary.start() >= end:
                break
            if boundary.start() < start:
                start += 1
            end += 1
        newline = (re.search(r"\r\n?|\n", content[start:end]) or re.search(r"\r\n?|\n", content[end:])
                   or re.search(r"\r\n?|\n", content))
        replacement = re.sub(r"\r\n?|\n", newline[0], replacement)
    return content[:start] + replacement + content[end:], 1


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def _body_char_count(text: str) -> int:
    """正文本体字数估算：排除标题行、状态/进度行（> 开头）与空行。"""
    total = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">"):
            continue
        total += len(s)
    return total


def _ordinary_batch_sizes(text: str) -> list[int]:
    """Plan before writing; the original text remains the only source for the manuscript."""
    sizes = []
    for nonblank, lines in groupby(text.splitlines(), key=lambda line: bool(line.strip())):
        if not nonblank:
            continue
        size = _body_char_count("\n".join(lines))
        if size > 450:
            raise ValueError(f"单个自然段实际 {size} 字，超过 450 字。请在自然停顿处独立分段后再保存。")
        if sizes and sizes[-1] + size <= 450:
            sizes[-1] += size
        elif size:
            sizes.append(size)
    return sizes


def _append_text_unit(current: str, content: str) -> str:
    newline = "\r\n" if "\r\n" in current or "\r\n" in content else "\n"
    separator = ""
    if current and not current.endswith(newline * 2):
        separator = newline if current.endswith(("\r", "\n")) else newline * 2
    appended = current + separator + content
    return appended if appended.endswith(("\r", "\n")) else appended + newline


def _header_metadata(text: str) -> tuple[list[str], int]:
    """只读开头连续的元数据行，不越过空行或正文。"""
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith(("# ", "> 状态：")):
        return lines, 0
    end = 1 if lines[0].startswith("# ") else 0
    while end < len(lines) and lines[end].startswith(("> 状态：", "> 进度：")):
        end += 1
    return lines, end


def _initial_text_header(lines: list[str], chapter: int) -> str | None:
    """Normalize header formatting only when the author supplied the chapter, draft status and date."""
    title = re.fullmatch(r"# 第([0-9]+)章(.*)", lines[0].rstrip("\r\n")) if lines else None
    if not title or title[1].lstrip("0") != str(chapter) or not title[2].strip(" \t·：:—-"):
        return None
    index = next((i for i, line in enumerate(lines[1:], 1) if line.strip()), None)
    if index is None:
        return None
    status = lines[index].removeprefix("> ")
    match = re.fullmatch(r"状态：[ \t]*起草中[ \t]*(?:[｜|][ \t]*(?:日期：[ \t]*)?|·[ \t]*)"
                         r"([0-9]{4}-[0-9]{2}-[0-9]{2})[ \t]*", status.rstrip("\r\n"))
    if not match:
        return None
    try:
        date.fromisoformat(match[1])
    except ValueError:
        return None
    if "·" in status or "日期：" in status:
        status = "状态：起草中｜" + status[match.start(1):]
    return lines[0] + "> " + status + "".join(lines[index + 1:])


def _progress_count(text: str) -> int | None:
    lines, end = _header_metadata(text)
    for line in lines[:end]:
        match = _PROGRESS_LINE.fullmatch(line.rstrip("\r\n"))
        if match:
            return int(match.group(1))
    return None


def _with_progress(text: str, count: int, previous: str = "") -> str:
    """正文修订可改状态与正文，但不能改掉已知的落盘总数。"""
    line = f"> 进度：已落 {count} 批"
    lines, end = _header_metadata(text)
    newline = "\r\n" if "\r\n" in text else "\n"
    if not end and previous:
        old_lines, end = _header_metadata(previous)
        # 全量修订漏掉整个文件头时，沿用旧元数据，正文仍按修订内容保存。
        prefix = "".join(old_lines[:end]).rstrip("\r\n") + newline * 2
        lines = (prefix + text).splitlines(keepends=True)
    for index in range(end):
        if lines[index].startswith("> 进度："):
            ending = lines[index][len(lines[index].rstrip("\r\n")):]
            lines[index] = line + ending
            return "".join(lines)
    if end and not lines[end - 1].endswith("\n"):
        lines[end - 1] += newline
    lines.insert(end, line + newline)
    return "".join(lines)


def _progress_receipt(count: int | None) -> str:
    return (
        f"本章已保存 {count} 个正文单元（含首次 full 和成功 append，不代表 Skill 篇幅预算）。"
        if count is not None else ""
    )


def _describe(kind: str, volume: int | None, chapter: int | None, name: str | None = None) -> str:
    if name:
        return f"{kind}（{name}）"
    if volume:
        return f"{kind}（第 {volume} 卷）"
    if chapter:
        return f"{kind}（第 {chapter} 章）"
    return kind


def save_artifact(*args, **kwargs) -> str:  # pragma: no cover - 由 build 闭包实现
    raise NotImplementedError("由 build(abc) 闭包实现（需要 workspace 与登记容器）")


def build_save_artifact_tool(
    workspace_root: Path, registry: dict, failures: dict, **options,
) -> StructuredTool:
    """只取落盘工具本身（测试与旧调用方）；整章写入入口经 chapter_draft_flush(abc) 取得。"""
    tool, _flush = _build_writer(workspace_root, registry, failures, **options)
    return tool


def _build_writer(
    workspace_root: Path, registry: dict, failures: dict,
    *, recorder=None, trace_id: str | None = None,
    agent_name: str = "author-subagent",
    append_batches: dict | None = None,
    writing_context: str | None = None,
    enabled_skill_names: set[str] | None = None,
    read_package_skill_names: set[str] | None = None,
    sentence_package: bool = False,
) -> tuple[StructuredTool, Callable]:
    """构建落盘工具及其整章写入入口。

    Args:
        workspace_root:  工作区根。
        registry:        读取登记（read_artifact 写入，先读后改保险）。
        failures:        编辑失败计数（重试上限退化兜底）。
        recorder:        TraceRecorder（取证用；None = 无追踪场景，跳过取证）。
        trace_id:        当前追踪会话标识（None = 无追踪场景）。
        agent_name:      取证事件的 agent 名（用 trace 埋点名，如 author-subagent）。
        append_batches:  追加批次计数（正文分段生长回执用；None = 工具自持）。
        writing_context: 当前 author 规程与启用技能；None 保持旧调用兼容。
        enabled_skill_names: 同轮启用技能原名；None 兼容旧执行端的标题核验。
        read_package_skill_names: 当前正文角色成功读取的包内技能，与读取工具共用同一集合。
        sentence_package: 正文包按段写作时为 True，说明文字不再提供整批起草。
    """
    append_batches = append_batches if append_batches is not None else {}
    skill_names_hint = (
        "作者已启用的 skill_name 原名："
        + json.dumps(sorted(enabled_skill_names), ensure_ascii=False)
        + "。只填原名，不附带标题中的描述。"
        if enabled_skill_names is not None else ""
    )
    workspace_fingerprint = (
        hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
        if writing_context is not None else ""
    )

    def _saved_receipt(receipt: str, kind: str, chapter: int | None, path: Path,
                       is_writing_unit: bool = False) -> str | tuple[str, dict]:
        if writing_context is None or kind != KIND_TEXT:
            return receipt
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # 正文已保存；无法复核真实状态时不声称有完成凭证。
            return receipt
        lines, end = _header_metadata(actual)
        status = next((re.split(r"[｜|]", line[len("> 状态："):].strip(), maxsplit=1)[0].strip()
                       for line in lines[:end] if line.startswith("> 状态：")), "")
        return receipt, {"writer_text": {
            "workspace": workspace_fingerprint, "chapter": chapter, "event": "save",
            "is_writing_unit": is_writing_unit, "status": status,
        }}

    def _register(rel: str, text: str, kind: str) -> None:
        """落盘成功后登记读取状态：内容刚由本工具写出，后续 append/edit 视为已读。

        正文单元连续落盘依赖这一点——每个单元落盘后不必为了
        下一批 append 而重读整章；不登记的话 append 会被上面的「先读后改」拦死。
        卷纲仍登记内容指纹（save 侧的卷纲变动复核口径不变）。
        """
        registry[rel] = (
            {"content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}
            if kind == KIND_VOLUME else True
        )

    def _capture_revision(rel: str, text: str, tool_name: str = "save_artifact") -> None:
        """落盘成功后冻结 ArtifactRevision（进化侧产物库摄入源）。

        内容在手直记，不重读文件——重读会与同文件并发写竞态（平台取证层
        前科 12494f65）。取证失败静默降级（DEC-004）：留 capture_degraded
        痕迹，绝不影响落盘结果与工具返回。

        取证身份内容寻址（R1/P0）：tool_call_id 由内容哈希派生。recorder 的
        call_paths 守卫按 (tool_call_id, 路径) 判「同调用不同内容 = 冲突」——
        恒定 id 会让同一产物的后续修订全部被误判冲突静默丢弃；per-call 随机
        id 又会让同内容重落失去幂等去重。内容寻址两头都成立：同内容 → 同 id
        → 去重返回既有修订；内容变 → id 变 → 新修订且不触冲突守卫。
        """
        if recorder is None or not trace_id:
            return
        capture_id = f"{tool_name}:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        try:
            recorder.record_artifact_revision(
                trace_id, agent_name,
                file_path=rel, content=text, tool_name=tool_name,
                tool_call_id=capture_id,
            )
        except Exception as exc:  # noqa: BLE001 — 取证失败不得影响写作（DEC-004）
            marker = getattr(recorder, "_mark_capture_degraded", None)
            if callable(marker):
                try:
                    marker(trace_id, f"save_artifact_capture_failed:{rel}:{type(exc).__name__}")
                except Exception:
                    pass
            logger.warning("save_artifact 产物取证失败（已降级）：%s：%s", rel, exc)

    def _save(kind: str, name: str | None = None, volume: int | None = None,
              chapter: int | None = None,
              mode: str = "edit",
              anchor: str | None = None, replacement: str | None = None,
              content: str | None = None, draft: bool = False,
              unit_kind: str | None = None, skill_name: str | None = None) -> str | tuple[str, dict]:
        if draft and kind != KIND_VOLUME:
            return "[save_artifact 失败] draft 仅卷纲可用。"
        try:
            rel = artifact_relpath(kind, volume=volume, chapter=chapter, name=name)
            if draft:
                from contracts.writing_artifacts import volume_draft_relpath

                rel = volume_draft_relpath(volume)
        except ValueError as exc:
            return f"[save_artifact 失败] {exc}"
        if mode not in ("edit", "full", "append"):
            return "[save_artifact 失败] mode 只能是 edit、full 或 append。"
        path = workspace_root / rel
        exists = path.is_file()
        text_entry = agent_name == "text-subagent" and kind == KIND_TEXT
        current = ""
        if exists:
            try:
                with path.open(encoding="utf-8", newline="" if text_entry else None) as stream:
                    current = stream.read()
            except (OSError, UnicodeDecodeError) as exc:
                return f"[save_artifact 失败] 读取现状失败：{type(exc).__name__}"

        # ── mode=edit：产物不存在的判定先于读档要求（读不存在的产物只会得到
        #    「不存在」指引，不应拿读档拦路）──
        if mode == "edit" and not exists:
            return "[save_artifact 失败] 产物尚不存在，新建用 mode=full。"

        # ── 保险 1：先读后改（edit 改已存在文件；full 改已存在文件同样要）──
        if exists and not registry.get(rel):
            return (
                "[save_artifact 拒绝] 先调 read_artifact 读取该产物，再落盘——"
                "锚点/整文件替换都必须基于刚读到的当前内容。"
            )
        read_entry = registry.get(rel)
        if exists and kind == KIND_VOLUME and isinstance(read_entry, dict):
            fingerprint = hashlib.sha256(current.encode("utf-8")).hexdigest()
            if read_entry.get("content_hash") != fingerprint:
                return "[save_artifact 拒绝] 卷纲自读取后已变化，请重新 read_artifact 后合并修改。"

        track_progress = kind == KIND_TEXT and writing_context is not None
        uninitialized = not exists or (track_progress and not current.strip())
        if text_entry and track_progress and uninitialized and mode == "full" and content:
            content = _initial_text_header(content.splitlines(keepends=True), chapter) or content
        is_draft_unit = kind == KIND_TEXT and (mode == "append" or (mode == "full" and uninitialized))
        batch_count = 1
        batch_info = ""
        if is_draft_unit:
            if writing_context is not None and unit_kind is None:
                return "[save_artifact 拒绝·文件未变] 请注明 unit_kind：普通单元 ordinary；匹配的完整技能场景 skill_scene，并填写 skill_name 原名。"
            if unit_kind == "ordinary" and _body_char_count(content or "") > 450:
                if not text_entry:
                    return (
                        f"[save_artifact 拒绝·文件未变] 普通单元实际约 {_body_char_count(content or '')} 字，超过 450 字的自然停顿余量。"
                        "请只推进当前一步，控制在 300-400 字，把后续普通剧情留到下一单元；不硬切句，也不将普通段落冒充技能场景。"
                    )
                try:
                    sizes = _ordinary_batch_sizes(content)
                except ValueError as exc:
                    return f"[save_artifact 拒绝·文件未变] {exc}"
                batch_count = len(sizes)
                batch_info = f"本次按完整自然段保存 {batch_count} 批，各批字数：{'、'.join(map(str, sizes))}。"
            if unit_kind == "skill_scene":
                if enabled_skill_names is not None:
                    enabled = skill_name in enabled_skill_names
                elif writing_context is not None:
                    enabled = f"### {skill_name}——" in writing_context
                else:
                    enabled = read_package_skill_names is None  # 旧调用未提供任何技能名单。
                package_read = text_entry and read_package_skill_names is not None and skill_name in read_package_skill_names
                if not (skill_name or "").strip() or not (enabled or package_read):
                    return (
                        "[save_artifact 拒绝·文件未变] 请填写适用于当前场景的技能原名。"
                        + skill_names_hint
                        + ("包内技能请先用 read_writing_skill 读取本角色目录里的 SKILL.md。"
                           if text_entry and read_package_skill_names is not None else "")
                        + "若剧情已匹配场景，请改正名称后整场重试，不得改为 ordinary 拆批保存。"
                    )

        if track_progress and mode == "append" and uninitialized:
            return "[save_artifact 拒绝·文件未变] 正文尚未初始化，请先用 mode=full 建立标题、状态和首批进度，再追加正文单元。"
        progress = _progress_count(current) if track_progress else None
        if mode == "full":
            if content is None or not content.strip():
                return "[save_artifact 失败] mode=full 需要 content（完整新内容）。"
            if track_progress:
                if uninitialized:
                    header, end = _header_metadata(content)
                    if end < 2 or not header[0].startswith("# ") or not header[1].startswith("> 状态："):
                        return (
                            "[save_artifact 拒绝·文件未变] 新建正文文件头须先有标题行，再有状态行。"
                            "请补齐 # 第NNN章 · 章名 与 > 状态：起草中｜日期 后重试。"
                        )
                    progress = batch_count
                elif progress is None and _progress_count(content) is not None:
                    return _UNKNOWN_PROGRESS_REJECTION
                if progress is not None:
                    content = _with_progress(content, progress, current)
            try:
                _atomic_write(path, content)
            except OSError as exc:
                return f"[save_artifact 失败] 写入失败：{type(exc).__name__}"
            _capture_revision(rel, content)
            _register(rel, content, kind)
            failures.pop(rel, None)
            lines = len(content.splitlines())
            text_info = batch_info or (
                f"本次正文单元约 {_body_char_count(content)} 字。{_TEXT_UNIT_GUIDANCE}"
                if is_draft_unit else ""
            )
            receipt = f"已落盘 {_describe(kind, volume, chapter, name)}（全量 {lines} 行）。{text_info}{_progress_receipt(progress)}"
            return _saved_receipt(receipt, kind, chapter, path, is_draft_unit)

        # ── mode=append：正文分段生长（每批一段落一批）──
        # 追加到文件末尾，不做锚点匹配——50 字短段当锚点在文件里极易撞到重复句，
        # 撞车累积 3 次会被上面的重试上限强制退回 mode=full 整章重写，分段落盘
        # 就此失效。append 从机制上绕开这条路径。
        if mode == "append":
            if content is None or not content.strip():
                return "[save_artifact 失败] mode=append 需要 content（本批要追加的正文片段）。"
            body = content.strip("\n").rstrip()
            if not body:
                return "[save_artifact 失败] mode=append 的 content 是空内容。"
            if text_entry:
                new_text = _append_text_unit(current, content)
            elif exists and current.strip():
                new_text = current.rstrip("\n") + "\n\n" + body + "\n"
            else:
                new_text = body + "\n"
            if progress is not None:
                progress += batch_count
                new_text = _with_progress(new_text, progress)
            try:
                _atomic_write(path, new_text)
            except OSError as exc:
                return f"[save_artifact 失败] 写入失败：{type(exc).__name__}"
            _capture_revision(rel, new_text)
            _register(rel, new_text, kind)
            batch = append_batches.get(rel, 0) + 1
            append_batches[rel] = batch
            chars = _body_char_count(new_text)
            text_info = batch_info or (
                f"本次追加约 {_body_char_count(body)} 字。{_TEXT_UNIT_GUIDANCE}"
                if kind == KIND_TEXT else ""
            )
            if progress is not None:
                progress_guidance = "进度已由工具随正文同步，依正文规程规划下一个单元；完整场景不拆批。"
            elif track_progress:
                progress_guidance = "历史次数未知，不补造累计次数；本轮追加序号仅供续写参考，完整场景不拆批。"
            else:
                progress_guidance = "按本章实际成功写入次数更新进度，依正文规程规划下一个单元；完整场景不拆批。"
            receipt = (
                f"已追加落盘 {_describe(kind, volume, chapter, name)}：本轮追加第 {batch} 次，"
                f"正文本体累计约 {chars} 字。"
                f"{text_info}"
                f"{_progress_receipt(progress)}"
                "此序号仅计本轮追加保存次数，不含首次 full，不代表 Skill 篇幅预算。"
                f"{progress_guidance}"
            )
            return _saved_receipt(receipt, kind, chapter, path, is_draft_unit)

        # ── mode=edit ──
        if failures.get(rel, 0) >= _EDIT_FAILURE_CAP:
            return (
                "[save_artifact 拒绝] 该产物编辑已连续失败 "
                f"{failures[rel]} 次：停止重试锚点，改用 mode=full 提交完整内容"
                "（read 后整文件重写，随后失败计数自动清零）。"
            )
        if not anchor or replacement is None:
            return "[save_artifact 失败] mode=edit 需要 anchor 与 replacement。"
        result = apply_edit(current, anchor, replacement)
        if isinstance(result, str):
            failures[rel] = failures.get(rel, 0) + 1
            return f"[save_artifact 失败·文件未变] {result}"
        new_text, hits = result
        if track_progress and progress is None and _progress_count(new_text) is not None:
            return _UNKNOWN_PROGRESS_REJECTION
        if progress is not None:
            new_text = _with_progress(new_text, progress, current)
        try:
            _atomic_write(path, new_text)
        except OSError as exc:
            return f"[save_artifact 失败] 写入失败：{type(exc).__name__}"
        _capture_revision(rel, new_text)
        _register(rel, new_text, kind)
        failures.pop(rel, None)
        receipt = f"已落盘 {_describe(kind, volume, chapter, name)}（校验编辑：替换 {hits} 处，仅目标区段变更）。{_progress_receipt(progress)}"
        return _saved_receipt(receipt, kind, chapter, path)

    def _through_task(values, run):
        """受限进程里先写本地副本再交父进程落盘；没有任务时直接写。回执连同取证元数据一起返回。"""
        from app.domains.writing.creative_scope import ACTIVE

        task = ACTIVE.get()
        result = task.save(values, run) if task else run()
        return tuple(result) if isinstance(result, (tuple, list)) else (result, None)

    def _native_save(*args, **kwargs):
        values = dict(kwargs)
        kwargs.pop("character_role", None)
        return _through_task(values, lambda: _save(*args, **kwargs))

    def _flush_chapter(status, title, messages):
        """整章写入：改文件头两行，并把本轮攒下的段落一次写入（REQ-20261125-154544 FR-005/FR-006）。"""
        from .sentence_common import current_chapter

        chapter = current_chapter()
        if chapter is None:
            return rejection("当前没有目标章节。"), None
        rel = artifact_relpath(KIND_TEXT, chapter=chapter)
        path = workspace_root / rel
        try:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError) as exc:
            return rejection(f"读取现状失败：{type(exc).__name__}。"), None
        state = draft_state()
        pending = pending_sentences(messages, state.flushed_through)
        if not pending and not split_chapter(current)[1]:
            return rejection("本章还没有正文，先写正文再改文件头。"), None
        content = compose_chapter(current, chapter, [paragraph for _, paragraph in pending], title=title, status=status)
        marker = pending[-1][0] if pending else state.flushed_through

        def write():
            _atomic_write(path, content)
            # 与整批保存走同一条取证链：父进程核验和进化端产物库都靠这份修订记录
            # 认识本章（审前稿、审后修订）。只写文件不发记录，评测任务会被判失败。
            _capture_revision(rel, content, tool_name=HEADER_TOOL)
            _register(rel, content, KIND_TEXT)
            failures.pop(rel, None)
            return f"文件头已改为「{status}」" + (f"，写入本章 {len(pending)} 段。" if pending else "。")

        values = {"kind": KIND_TEXT, "chapter": chapter, "mode": "full", "content": content,
                  "draft_flushed_through": marker, "draft_sentences": len(pending)}
        if not pending:
            values.pop("draft_flushed_through")
        result, _metadata = _through_task(values, write)
        if not isinstance(result, str) or result.startswith("[拒绝]"):
            return result, None
        if pending:
            note_flushed(marker)
        receipt = _saved_receipt(result, KIND_TEXT, chapter, path, is_writing_unit=True)
        return receipt if isinstance(receipt, tuple) else (receipt, None)

    description = (
        SENTENCE_PACKAGE_TOOL_DESCRIPTION if sentence_package else SAVE_ARTIFACT_TOOL_DESCRIPTION
        + ("\n新建正文的 content 从两行文件头开始。第一行写 # 第NNN章 · 章名，"
           "第二行写 > 状态：起草中｜YYYY-MM-DD，填实际章号、章名和日期。空一行后写正文。"
           "\n普通正文超过 450 字时按完整自然段自动分批并一次落盘；单个自然段仍不可超过 450 字。"
           if agent_name == "text-subagent" else "")
        + ("\n匹配当前场景时，作者已启用的技能，或本角色已用 read_writing_skill 读过入口的包内技能，"
           "均可按原名整场保存。仅规定文风的技能仍按普通正文保存。"
           if agent_name == "text-subagent" and read_package_skill_names is not None else "")
        + ("\n" + skill_names_hint if skill_names_hint else "")
    )
    return StructuredTool.from_function(
        name="save_artifact",
        description=description,
        func=_native_save,
        args_schema=CharacterOwnerSaveInput if agent_name in ("outline-subagent", "volume-subagent") else SaveArtifactInput,
        infer_schema=False,
        response_format="content_and_artifact",
    ), _flush_chapter


def build(abc):
    """架构清单挂载钩子：与 read_artifact 共享读取登记；失败计数挂 ctx。

    同一装配只建一次落盘工具，文件头工具经 chapter_draft_flush 共用这份入口。
    取证接线（REQ-20261019-203500）：recorder/trace_id 来自 RuntimeContext，
    agent 名用 trace 埋点名（与 TraceMiddleware 同源，如 author-subagent）。
    """
    existing = getattr(abc, "_save_artifact_tool", None)
    if existing is not None:
        return existing
    registry = getattr(abc.ctx, "artifact_read_registry", None)
    if registry is None:
        registry = {}
        try:
            abc.ctx.artifact_read_registry = registry
        except Exception:  # noqa: BLE001 — ctx 不可写时自持（降级同 read 侧）
            pass
    failures = getattr(abc.ctx, "artifact_edit_failures", None)
    if failures is None:
        failures = {}
        try:
            abc.ctx.artifact_edit_failures = failures
        except Exception:  # noqa: BLE001
            pass
    append_batches = getattr(abc.ctx, "artifact_append_batches", None)
    if append_batches is None:
        append_batches = {}
        try:
            abc.ctx.artifact_append_batches = append_batches
        except Exception:  # noqa: BLE001 — 不可写时工具自持（回执序号从第 1 批重数）
            pass
    ctx = getattr(abc, "ctx", None)
    styles = getattr(ctx, "styles", None) or {}
    skill_names = styles.get("_writing_skill_names")
    tool, flush = _build_writer(
        abc.workspace_path, registry, failures,
        recorder=getattr(ctx, "trace_recorder", None),
        trace_id=getattr(ctx, "trace_id", None),
        agent_name=getattr(abc, "trace_name", None) or "author-subagent",
        append_batches=append_batches,
        writing_context=styles.get("author"),
        enabled_skill_names=set(json.loads(skill_names)) if skill_names is not None else None,
        read_package_skill_names=getattr(abc, "read_package_skill_names", None)
        if getattr(abc, "trace_name", None) == "text-subagent" else None,
        sentence_package=package_uses_sentences(getattr(abc, "pkg_dir", None)),
    )
    abc._save_artifact_tool = tool
    abc._chapter_draft_flush = flush
    return tool


def chapter_draft_flush(abc):
    """文件头工具用的整章写入；与落盘工具共用同一份读取登记和取证。"""
    build(abc)
    return abc._chapter_draft_flush


__all__ = [
    "SAVE_ARTIFACT_TOOL_DESCRIPTION",
    "SENTENCE_PACKAGE_TOOL_DESCRIPTION",
    "SaveArtifactInput",
    "apply_edit",
    "build",
    "build_save_artifact_tool",
    "chapter_draft_flush",
    "find_edit_match",
]
