"""write_sensory 工具 — 逐句写作：写一句「感官」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_SENSORY_TOOL_DESCRIPTION = """写一句外界环境或物理接触带来的直接五感（视觉细节、声响、气味、味觉、温度与触感）。

写法：写清具体来源、空间位置和质感/强弱；只写客观感知，不掺主观评价（如「很难受」「很诡异」）或心理推演。

边界：他人的动作直接用 write_action 写，不要套「看见/听到某人……」；受情绪或事件刺激引发的自身生理反应（如喉咙发紧、心跳加快）改用 write_reaction。"""


def build(abc):
    return build_sentence_tool(abc, "write_sensory", WRITE_SENSORY_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_SENSORY_TOOL_DESCRIPTION", "build"]
