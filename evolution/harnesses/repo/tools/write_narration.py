"""write_narration 工具 — 逐句写作：写一句「叙述」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool


def build(abc):
    return build_sentence_tool(abc, "write_narration", "叙述：过渡、时间跳跃、背景、概述")


__all__ = ["SentenceInput", "build"]
