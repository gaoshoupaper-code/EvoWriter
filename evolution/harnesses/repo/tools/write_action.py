"""write_action 工具 — 逐句写作：写一句「动作」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_ACTION_TOOL_DESCRIPTION = """写一句人物主动做出的外部可见动作（肢体动作、位移、与人/物的交互、刻意表露的神态）。

写法：写清主体、方式与落点（落在哪个人或物的具体部位）；一句只落一个动作。重要动作必须拆成连续多次调用逐步推进（如先靠近、再换姿势、最后接触），禁止把多个步骤挤进同一句。

边界：受刚发生的事触动而起的非自主身体变化（手抖、脸红、一僵）改用 write_reaction；说话改用 write_dialogue。"""


def build(abc):
    return build_sentence_tool(abc, "write_action", WRITE_ACTION_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_ACTION_TOOL_DESCRIPTION", "build"]
