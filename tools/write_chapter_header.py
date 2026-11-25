"""write_chapter_header 工具 — 只改正文文件开头的章名行和状态行（REQ-20261125-154544 FR-006/DEC-011）。

状态改为「初稿」的那次调用，由 save_artifact 把本轮攒下的句子整章一次写入。
这里只做调用层面的检查；文件怎么拼、怎么写，都在 save_artifact 的整章写入里。
"""
from __future__ import annotations

from typing import Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.sentence_draft import HEADER_TOOL, rejection, unresolved_draft_calls
from .sentence_common import messages_of

_TITLE_LIMIT = 40

CHAPTER_HEADER_TOOL_DESCRIPTION = """只改正文文件开头的章名行和状态行，不动正文。

整章写完后调用：status 填「初稿」，title 填章名。系统在这一次调用里把本轮写好的句子整章写入文件。
本章还没有任何正文时不能调用。"""


class ChapterHeaderInput(BaseModel):
    status: Literal["起草中", "初稿"] = Field(description="要改成的状态；整章写完填「初稿」")
    title: str | None = Field(default=None, description="章名，一行简短文字；本章已有章名时留空表示不改")


def build(abc):
    from .save_artifact import chapter_draft_flush

    def write_chapter_header(status: Literal["起草中", "初稿"], title: str | None = None,
                             runtime: ToolRuntime = None) -> tuple[str, dict | None]:
        if unresolved_draft_calls(messages_of(runtime), own_call=getattr(runtime, "tool_call_id", None)):
            return rejection("同一轮里还有句子没返回，等返回后再改文件头。"), None
        if title is not None and (len(title) > _TITLE_LIMIT or "\n" in title or "\r" in title):
            return rejection("章名须是一行简短文字。"), None
        return chapter_draft_flush(abc)(status=status, title=(title or "").strip() or None,
                                        messages=messages_of(runtime))

    return StructuredTool.from_function(
        name=HEADER_TOOL,
        description=CHAPTER_HEADER_TOOL_DESCRIPTION,
        func=write_chapter_header,
        args_schema=ChapterHeaderInput,
        infer_schema=False,
        response_format="content_and_artifact",
    )


__all__ = ["CHAPTER_HEADER_TOOL_DESCRIPTION", "ChapterHeaderInput", "build"]
