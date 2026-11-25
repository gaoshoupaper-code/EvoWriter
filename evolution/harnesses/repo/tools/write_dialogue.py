"""write_dialogue 工具 — 逐句写作：写一句「语言」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool


def build(abc):
    return build_sentence_tool(abc, "write_dialogue", "语言：人物说了什么")


__all__ = ["SentenceInput", "build"]
