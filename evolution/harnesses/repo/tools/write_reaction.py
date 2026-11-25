"""write_reaction 工具 — 逐句写作：写一句「反应」（REQ-20261125-154544 FR-001/FR-003/DEC-003/DEC-016）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool


def build(abc):
    return build_sentence_tool(abc, "write_reaction", "反应：人物对刚发生之事的回应，身体的和心里的都算")


__all__ = ["SentenceInput", "build"]
