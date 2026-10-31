"""read_artifact 工具 — 语义化读档（REQ-20261018-115508 FR-003）。

agent 按产物 kind 读档案，不接触任何文件路径：kind+卷号/章号 → 相对路径
的唯一映射在 contracts.writing_artifacts（路径收权）。

读取会登记进 ctx.artifact_read_registry——save_artifact 校验编辑模式的
「先读后改」保险消费它（DEC-007 四道保险之一）。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

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

READ_ARTIFACT_TOOL_DESCRIPTION = """按产物种类读取当前书的档案（不接触文件路径）。

kind 取值：
- theme（主题定稿） / outline（总纲定稿） / style（文风卡）
- volume_outline（卷纲，需 volume 卷号）
- chapter_outline（章纲指令卡，需 chapter 章号）
- chapter_text（正文章节，需 chapter 章号）
- character（人物卡，需 name 人名；主角卡固定 name="主角"）

section 可选：只取包含该关键词的小节（如 section="伏笔登记表"）。
draft=true 仅卷纲可用：显式读取修订草稿，之后编辑草稿也须传 draft=true。
每次起草或修订前先读对应档案；编辑落盘（save_artifact mode=edit）前必须先读本产物。"""

_KINDS = (KIND_THEME, KIND_OUTLINE, KIND_STYLE, KIND_VOLUME, KIND_CHAPTER, KIND_TEXT, KIND_CHARACTER)


class ReadArtifactInput(BaseModel):
    kind: str = Field(description="产物种类：theme/outline/style/volume_outline/chapter_outline/chapter_text/character")
    name: str | None = Field(default=None, description="人名（character 必填；主角卡固定「主角」）")
    volume: int | None = Field(default=None, ge=1, description="卷号（volume_outline 必填）")
    chapter: int | None = Field(default=None, ge=1, description="章号（chapter_outline/chapter_text 必填）")
    section: str | None = Field(default=None, description="可选：只取包含该关键词的小节")
    draft: bool = Field(default=False, description="仅卷纲：读取修订草稿，不读取正式稿")


def _slice_section(text: str, keyword: str) -> str:
    """取包含 keyword 的标题所辖小节（到同级或更高级标题为止）。"""
    lines = text.splitlines()
    start = None
    level = 0
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if not s.startswith("#"):
            continue
        lv = len(s) - len(s.lstrip("#"))
        if start is None:
            if keyword in s:
                start, level = i, lv
        elif lv <= level:
            return "\n".join(lines[start:i]).strip()
    if start is None:
        return text
    return "\n".join(lines[start:]).strip() or text


def read_artifact(
    kind: str,
    volume: int | None = None,
    chapter: int | None = None,
    section: str | None = None,
    draft: bool = False,
) -> str:
    raise NotImplementedError("由 build(abc) 闭包实现（需要 workspace 与读取登记）")


def build_read_artifact_tool(workspace_root: Path, registry: dict) -> StructuredTool:
    """构建读档工具。

    Args:
        workspace_root: 工作区根（路径映射的落点）。
        registry:       读取登记容器（与 save_artifact 共享，先读后改保险）。
    """

    def _read(kind: str, name: str | None = None, volume: int | None = None,
              chapter: int | None = None,
              section: str | None = None, draft: bool = False) -> str:
        if draft and kind != KIND_VOLUME:
            return "[read_artifact 失败] draft 仅卷纲可用。"
        try:
            rel = artifact_relpath(kind, volume=volume, chapter=chapter, name=name)
            if draft:
                from contracts.writing_artifacts import volume_draft_relpath

                rel = volume_draft_relpath(volume)
        except ValueError as exc:
            return f"[read_artifact 失败] {exc}"
        path = workspace_root / rel
        if not path.is_file():
            return (
                f"[read_artifact] 产物不存在：{kind}"
                + (f"（{name}）" if name else "")
                + (f"（第 {volume} 卷）" if volume else "")
                + (f"（第 {chapter} 章）" if chapter else "")
                + "。当前进度以指令头部的进度摘要为准；上游产物未落盘时先推进上游。"
            )
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"[read_artifact 失败] 读取失败：{type(exc).__name__}"
        # 卷纲复核耗时较长，登记读取时内容，防调用保存前已被作者改过。
        registry[rel] = (
            {"content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}
            if kind == KIND_VOLUME else True
        )
        if section:
            return _slice_section(text, section)
        return text

    return StructuredTool.from_function(
        name="read_artifact",
        description=READ_ARTIFACT_TOOL_DESCRIPTION,
        func=_read,
        args_schema=ReadArtifactInput,
        infer_schema=False,
    )


def build(abc):
    """架构清单挂载钩子：取/建共享读取登记（ctx 上一次装配内共享）。"""
    registry = getattr(abc.ctx, "artifact_read_registry", None)
    if registry is None:
        registry = {}
        try:
            abc.ctx.artifact_read_registry = registry
        except Exception:  # noqa: BLE001 — ctx 不可写时工具自持（降级：编辑保险退化为进程内）
            pass
    return build_read_artifact_tool(abc.workspace_path, registry)


__all__ = [
    "READ_ARTIFACT_TOOL_DESCRIPTION",
    "ReadArtifactInput",
    "build",
    "build_read_artifact_tool",
]
