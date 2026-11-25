"""逐句写作工具的共用部分（REQ-20261125-154544 FR-001/FR-002/FR-003/FR-006）。

五个写作工具只收一句、只回一条短回执。句子在这里不写入文件：文件头工具把状态
改为「初稿」时，由 save_artifact 一次写入整章（FR-005）。本轮已写的句子每次都
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


class SentenceInput(BaseModel):
    text: str = Field(description="这一句的正文：一个完整的句子，不带换行")


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


def build_sentence_tool(abc, name: str, description: str) -> StructuredTool:
    """一类形式一个工具：收一句，回「第几句、本章累计多少字」。说明全文由各工具文件给出。"""
    workspace_root = Path(abc.workspace_path)

    def write_sentence(text: str, runtime: ToolRuntime = None) -> str:
        reason = validate_sentence(text)
        if reason:
            return rejection(reason)
        sentence = text.strip()
        messages = messages_of(runtime)
        pending = pending_sentences(messages, draft_state().flushed_through)
        total = (body_char_count(existing_chapter(workspace_root))
                 + sum(len(item) for _, item in pending) + len(sentence))
        return f"第 {len(turn_sentences(messages)) + 1} 句，本章累计约 {total} 字。"

    return StructuredTool.from_function(
        name=name,
        description=description,
        func=write_sentence,
        args_schema=SentenceInput,
        infer_schema=False,
    )


__all__ = ["SentenceInput", "build_sentence_tool", "current_chapter", "existing_chapter", "messages_of"]
