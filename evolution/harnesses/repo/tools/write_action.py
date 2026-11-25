"""write_action 工具 — 逐句写作：写一句「动作」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_ACTION_TOOL_DESCRIPTION = """写一句「动作」：人物主动做了什么，旁人看得见，包括手势、走动和神情。
一句只写一个动作：谁、怎么做、落在哪个人或物的哪个部位。
要紧的动作写出过程：先怎么靠近、怎么换姿势，再落下去。
被触动才起的身体变化（手一抖、脸一红）用 write_reaction。
一次只写一个完整的句子，这一句自成一段；等工具返回后再写下一句。"""


def build(abc):
    return build_sentence_tool(abc, "write_action", WRITE_ACTION_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_ACTION_TOOL_DESCRIPTION", "build"]
