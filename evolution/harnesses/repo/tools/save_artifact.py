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

每次「锚点→替换」经既有 TraceMiddleware 进 trace（L4 审计，DEC-008）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.writing_artifacts import (
    KIND_CHAPTER,
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

SAVE_ARTIFACT_TOOL_DESCRIPTION = """按产物种类落盘（不接触文件路径）。每个点被作者确认后立即落盘。

kind 取值：theme / outline / style / volume_outline(+volume) / chapter_outline(+chapter) / chapter_text(+chapter)。
正文文件名由章号决定（不带章名——章名写进文件头标题行「# 第NNN章 · <章名>」）。

两种模式：
- mode="full"：content = 该产物的完整新内容（新建文件、推翻重做大改用）。
- mode="edit"（默认，推荐）：anchor + replacement——anchor 是文件里的原文片段
  （须逐字一致且在文件中唯一，建议带上标题行等上下文）；replacement 是替换后
  的文本（新增一张卡 = anchor 所在区块末尾 + 新卡片内容；状态行变更同理）。
  编辑前必须先 read_artifact 读过该产物；锚点不唯一/找不到会报错且文件不变，
  带更多上下文重试；连续失败 3 次后改用 mode="full" 提交完整内容。

落盘内容遵守文件头两行约定：标题行 + 状态日期行；约束级/参考级分区。"""


class SaveArtifactInput(BaseModel):
    kind: str = Field(description="产物种类：theme/outline/style/volume_outline/chapter_outline/chapter_text")
    volume: int | None = Field(default=None, ge=1, description="卷号（volume_outline 必填）")
    chapter: int | None = Field(default=None, ge=1, description="章号（chapter_outline/chapter_text 必填）")
    mode: str = Field(default="edit", description='"edit"（默认，锚点校验编辑）| "full"（整文件替换）')
    anchor: str | None = Field(default=None, description="edit 模式：要替换的原文片段（须唯一）")
    replacement: str | None = Field(default=None, description="edit 模式：替换后的文本")
    content: str | None = Field(default=None, description="full 模式：完整新内容")


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
            end = start + sum(len(ln) for ln in file_lines[i:i + m])
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
    match = find_edit_match(content, anchor)
    if match == "ambiguous":
        return "锚点在文件中不唯一：带上标题行等更多上下文重试，或改用 mode=full。"
    if match is None:
        return "锚点在文件中找不到（须与原文逐字一致）：重新 read_artifact 后按当前内容重试。"
    start, end = match
    return content[:start] + replacement + content[end:], 1


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _describe(kind: str, volume: int | None, chapter: int | None) -> str:
    if volume:
        return f"{kind}（第 {volume} 卷）"
    if chapter:
        return f"{kind}（第 {chapter} 章）"
    return kind


def save_artifact(*args, **kwargs) -> str:  # pragma: no cover - 由 build 闭包实现
    raise NotImplementedError("由 build(abc) 闭包实现（需要 workspace 与登记容器）")


def build_save_artifact_tool(
    workspace_root: Path, registry: dict, failures: dict
) -> StructuredTool:
    """构建落盘工具。

    Args:
        workspace_root: 工作区根。
        registry:       读取登记（read_artifact 写入，先读后改保险）。
        failures:       编辑失败计数（重试上限退化兜底）。
    """

    def _save(kind: str, volume: int | None = None, chapter: int | None = None,
              mode: str = "edit",
              anchor: str | None = None, replacement: str | None = None,
              content: str | None = None) -> str:
        try:
            rel = artifact_relpath(kind, volume=volume, chapter=chapter)
        except ValueError as exc:
            return f"[save_artifact 失败] {exc}"
        if mode not in ("edit", "full"):
            return "[save_artifact 失败] mode 只能是 edit 或 full。"
        path = workspace_root / rel
        exists = path.is_file()
        current = ""
        if exists:
            try:
                current = path.read_text(encoding="utf-8")
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

        if mode == "full":
            if content is None or not content.strip():
                return "[save_artifact 失败] mode=full 需要 content（完整新内容）。"
            try:
                _atomic_write(path, content)
            except OSError as exc:
                return f"[save_artifact 失败] 写入失败：{type(exc).__name__}"
            failures.pop(rel, None)
            lines = len(content.splitlines())
            return f"已落盘 {_describe(kind, volume, chapter)}（全量 {lines} 行）。"

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
        try:
            _atomic_write(path, new_text)
        except OSError as exc:
            return f"[save_artifact 失败] 写入失败：{type(exc).__name__}"
        failures.pop(rel, None)
        return f"已落盘 {_describe(kind, volume, chapter)}（校验编辑：替换 {hits} 处，仅目标区段变更）。"

    return StructuredTool.from_function(
        name="save_artifact",
        description=SAVE_ARTIFACT_TOOL_DESCRIPTION,
        func=_save,
        args_schema=SaveArtifactInput,
        infer_schema=False,
    )


def build(abc):
    """架构清单挂载钩子：与 read_artifact 共享读取登记；失败计数挂 ctx。"""
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
    return build_save_artifact_tool(abc.workspace_path, registry, failures)


__all__ = [
    "SAVE_ARTIFACT_TOOL_DESCRIPTION",
    "SaveArtifactInput",
    "apply_edit",
    "build",
    "build_save_artifact_tool",
    "find_edit_match",
]
