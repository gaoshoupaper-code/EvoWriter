"""write_narration 工具 — 逐句写作：写一句「叙述」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_NARRATION_TOOL_DESCRIPTION = """写一句「叙述」：讲述人交代的内容，包括过渡、时间跳跃、背景和概述。
一句交代一件事，不把几件事压在一句里。
正在发生、看得见听得着的事，用另外四个工具逐句写，不用叙述带过。
一次只写一个完整的句子，这一句自成一段；等工具返回后再写下一句。"""


def build(abc):
    return build_sentence_tool(abc, "write_narration", WRITE_NARRATION_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_NARRATION_TOOL_DESCRIPTION", "build"]
