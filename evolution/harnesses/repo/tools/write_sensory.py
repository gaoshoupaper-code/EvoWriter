"""write_sensory 工具 — 逐句写作：写一句「感官」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool


def build(abc):
    return build_sentence_tool(abc, "write_sensory", "感官：人物看到、听到、闻到、尝到、触到什么")


__all__ = ["SentenceInput", "build"]
