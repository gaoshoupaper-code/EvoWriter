"""write_sensory 工具 — 逐句写作：写一句「感官」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_SENSORY_TOOL_DESCRIPTION = """写一句「感官」：人物直接感到的印象，看到的、听到的、闻到的、尝到的、触到的，不掺想法。
写具体的来源、位置和强弱，不写「很难受」这类评价。
别人做了什么，照实写成 write_action，不套「看见」「听到」。
一次只写一个完整的句子，这一句自成一段；等工具返回后再写下一句。"""


def build(abc):
    return build_sentence_tool(abc, "write_sensory", WRITE_SENSORY_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_SENSORY_TOOL_DESCRIPTION", "build"]
