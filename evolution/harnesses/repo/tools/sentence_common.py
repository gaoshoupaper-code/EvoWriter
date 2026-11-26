"""逐段写作工具的共用部分。

四个写作工具只收一段、只回一条短回执。段落在这里不写入文件：文件头工具把状态
改为「初稿」时，由 save_artifact 一次写入整章。本轮已写的段落每次都
从对话记录里重新取，所以任务恢复后不必另存一份。
"""
from __future__ import annotations

from pathlib import Path

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.sentence_draft import (
    body_char_count, draft_state, pending_sentences, rejection, turn_sentences, validate_sentence,
)
from contracts.writing_artifacts import KIND_TEXT, artifact_relpath

INTENT_LIMIT = 100


def current_chapter() -> int | None:
    """当前目标章号：作者对话看当前任务，评测任务看冻结的执行目标。"""
    from app.domains.writing.creative_scope import ACTIVE
    from app.domains.writing.target_execution import current_execution

    task = ACTIVE.get()
    if task is not None:
        return task.row["chapter"]
    execution = current_execution()
    return execution.chapter if execution else None


def messages_of(runtime: ToolRuntime) -> list:
    state = getattr(runtime, "state", None)
    return state.get("messages", []) if isinstance(state, dict) else []


def existing_chapter(workspace_root: Path) -> str:
    """本章已写进文件的文本；没有目标章或还没有文件时为空串。"""
    chapter = current_chapter()
    if chapter is None:
        return ""
    path = workspace_root / artifact_relpath(KIND_TEXT, chapter=chapter)
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return ""


def build_paragraph_tool(abc, name: str, description: str, intent_guide: str) -> StructuredTool:
    """一类形式一个工具：收一段，回「第几段、本章累计多少字」。

    各工具的 writing_intent 填法不同，由 intent_guide 给出箭头格式说明。
    说明全文由各工具文件给出。
    """
    workspace_root = Path(abc.workspace_path)

    class ParagraphInput(BaseModel):
        writing_intent: str = Field(
            description=f"{intent_guide}。一行短语，最多 {INTENT_LIMIT} 字")
        text: str = Field(description="这一段的成段正文")

    def write_paragraph(writing_intent: str, text: str, runtime: ToolRuntime = None) -> str:
        messages = messages_of(runtime)
        if messages and len(getattr(messages[-1], "tool_calls", ())) > 1:
            return rejection("写段落时一次只调用一个工具。等上一段的回执，再单独想和写当前这一段。")
        if not 1 <= len(writing_intent.strip()) <= INTENT_LIMIT or "\n" in writing_intent or "\r" in writing_intent:
            return rejection(f"请先用一行短语写清本段目的与因果去向，最多 {INTENT_LIMIT} 字。")
        reason = validate_sentence(text)
        if reason:
            return rejection(reason)
        paragraph = text.strip()
        pending = pending_sentences(messages, draft_state().flushed_through)
        total = (body_char_count(existing_chapter(workspace_root))
                 + sum(len(item) for _, item in pending) + len(paragraph))
        return f"第 {len(turn_sentences(messages)) + 1} 段，本章累计约 {total} 字。"

    return StructuredTool.from_function(
        name=name,
        description=description,
        func=write_paragraph,
        args_schema=ParagraphInput,
        infer_schema=False,
    )


__all__ = ["build_paragraph_tool", "current_chapter", "existing_chapter", "messages_of"]
