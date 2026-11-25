"""write_reaction 工具 — 逐句写作：写一句「反应」（REQ-20261125-154544 FR-001/FR-003/DEC-003/DEC-016）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_REACTION_TOOL_DESCRIPTION = """写一句「反应」：人物被刚发生的事触动后的回应，身体的和心里的都算。
身体反应写具体部位和变化，如手一抖、喉咙发紧。
心里的反应写冒出的念头，不直接贴情绪标签。
有意去做的事用 write_action；说出声的话用 write_dialogue。
一次只写一个完整的句子，这一句自成一段；等工具返回后再写下一句。"""


def build(abc):
    return build_sentence_tool(abc, "write_reaction", WRITE_REACTION_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_REACTION_TOOL_DESCRIPTION", "build"]
