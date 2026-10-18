"""append_chapter_deviation 工具 — 章纲偏差注记回写（REQ-20261018-115508 FR-005/009，DEC-006）。

正文人修定稿后，与章纲卡的差异逐条追加到对应章纲卡末尾（参考级注记）。
前置硬条件：该章正文状态行必须是「人修定稿」（本工具内校验 + stage_gate
双保险，AC-007）。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.writing_artifacts import (
    KIND_CHAPTER,
    KIND_TEXT,
    artifact_relpath,
    parse_manuscript_status,
)

logger = logging.getLogger(__name__)

APPEND_DEVIATION_TOOL_DESCRIPTION = """在某章章纲卡末尾追加「实际偏差」注记（仅正文段、该章已人修定稿后可用）。

人修定稿与章纲不一致时，把差异逐条写进 note（如：钩子改为……／爽点落点移至……）。
以正文定稿为准，只记录不评价；偏差动摇后文卡片的（伏笔时点、钩子链），
在回复里提醒作者连锁重检。"""


class AppendDeviationInput(BaseModel):
    chapter: int = Field(ge=1, description="章号")
    note: str = Field(min_length=1, description="偏差注记（逐条差异；不带日期前缀，工具自动加）")


def _atomic_write(path: Path, text: str) -> None:
    """原子写（唯一 tmp 后缀 + replace，R1/R4 修复）。"""
    import uuid as _uuid

    tmp = path.with_suffix(f"{path.suffix}.{_uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def build_append_deviation_tool(workspace_root: Path) -> StructuredTool:
    def _append(chapter: int, note: str) -> str:
        outline_path = workspace_root / artifact_relpath(KIND_CHAPTER, chapter=chapter)
        if not outline_path.is_file():
            return f"[append_chapter_deviation 失败] 第 {chapter} 章章纲卡不存在。"
        # 前置硬闸（工具内二道防线；stage_gate 是第一道）：正文必须已人修定稿
        text_dir = workspace_root / artifact_relpath(KIND_TEXT, chapter=chapter)
        manuscript = None
        for cand in text_dir.parent.glob(f"{text_dir.stem}*.md"):
            manuscript = cand
            break
        if manuscript is None:
            return f"[append_chapter_deviation 拒绝] 第 {chapter} 章正文尚未落盘，无从谈定稿偏差。"
        try:
            status_text = manuscript.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"[append_chapter_deviation 失败] 读正文失败：{type(exc).__name__}"
        if parse_manuscript_status(status_text) != "finalized":
            return (
                f"[append_chapter_deviation 拒绝] 第 {chapter} 章正文状态仍是「初稿」——"
                "偏差回写只发生在作者人修定稿之后（以正文定稿为准）。"
            )
        block = f"\n> 实际偏差（{date.today().isoformat()}）：{note.strip()}\n"
        try:
            current = outline_path.read_text(encoding="utf-8")
            _atomic_write(outline_path, current.rstrip("\n") + "\n" + block)
        except (OSError, UnicodeDecodeError) as exc:
            return f"[append_chapter_deviation 失败] 写入失败：{type(exc).__name__}"
        return f"第 {chapter} 章章纲卡已追加偏差注记（参考级，卡内其余内容不变）。"

    return StructuredTool.from_function(
        name="append_chapter_deviation",
        description=APPEND_DEVIATION_TOOL_DESCRIPTION,
        func=_append,
        args_schema=AppendDeviationInput,
        infer_schema=False,
    )


def build(abc):
    return build_append_deviation_tool(abc.workspace_path)


__all__ = [
    "APPEND_DEVIATION_TOOL_DESCRIPTION",
    "AppendDeviationInput",
    "build",
    "build_append_deviation_tool",
]
