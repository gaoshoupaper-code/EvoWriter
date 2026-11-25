"""write_dialogue 工具 — 逐句写作：写一句「语言」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_DIALOGUE_TOOL_DESCRIPTION = """写一句人物说出声的话，原话放进引号。

写法：一句只表达一层意思；说话提示保持极简，仅交代说话人或声音/语气（如「他压低声音说」），禁止在提示中夹带明显动作、神态或心理描写。

边界：伴随的动作或神态拆出去另调用 write_action / write_reaction；未说出口的想法改用 write_reaction。"""


def build(abc):
    return build_sentence_tool(abc, "write_dialogue", WRITE_DIALOGUE_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_DIALOGUE_TOOL_DESCRIPTION", "build"]
