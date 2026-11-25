"""write_dialogue 工具 — 逐句写作：写一句「语言」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_DIALOGUE_TOOL_DESCRIPTION = """写一句「语言」：人物说出声的话，原话放进引号。
一句只说一层意思；可以带一个简短的说话提示，如谁说的、什么语气。
没说出声的想法用 write_reaction。
一次只写一个完整的句子，这一句自成一段；等工具返回后再写下一句。"""


def build(abc):
    return build_sentence_tool(abc, "write_dialogue", WRITE_DIALOGUE_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_DIALOGUE_TOOL_DESCRIPTION", "build"]
