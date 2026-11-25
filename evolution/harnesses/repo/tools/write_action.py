"""write_action 工具 — 逐句写作：写一句「动作」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool


def build(abc):
    return build_sentence_tool(abc, "write_action", "动作：人物做了什么，旁人看得见")


__all__ = ["SentenceInput", "build"]
