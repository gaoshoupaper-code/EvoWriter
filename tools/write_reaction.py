"""write_reaction 工具 — 逐句写作：写一句「反应」（REQ-20261125-154544 FR-001/FR-003/DEC-003/DEC-016）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_REACTION_TOOL_DESCRIPTION = """写一句人物被刚发生的事触动后的即时回应，包括非自主身体变化或脑中闪过的念头。

写法：身体反应写具体部位和变化（如指节发白、呼吸一滞）；心理反应写具体念头，不直接贴情绪标签（如「他很生气」「她有些委屈」）。一次只写单一反应，不把身体变化与内心想法混在同一句。

边界：人物有意发起的动作或刻意神态改用 write_action；说出声的话改用 write_dialogue。"""


def build(abc):
    return build_sentence_tool(abc, "write_reaction", WRITE_REACTION_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_REACTION_TOOL_DESCRIPTION", "build"]
