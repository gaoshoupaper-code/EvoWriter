"""write_narration 工具 — 逐句写作：写一句「叙述」（REQ-20261125-154544 FR-001/FR-003/DEC-003）。"""
from __future__ import annotations

from .sentence_common import SentenceInput, build_sentence_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字
# （REQ-20261125-210621 FR-001/FR-003）。
WRITE_NARRATION_TOOL_DESCRIPTION = """写一句讲述人视角的过渡或交代（时间跳跃、场景切换、背景补充、非重点过程的概述）。

写法：一句只交代一件事，保持克制，不把多个事件或因果压进同一长句。

边界：当前场景中正在发生、看得见听得着的具体过程，必须用另外四个工具逐句写，禁止用叙述一笔带过。"""


def build(abc):
    return build_sentence_tool(abc, "write_narration", WRITE_NARRATION_TOOL_DESCRIPTION)


__all__ = ["SentenceInput", "WRITE_NARRATION_TOOL_DESCRIPTION", "build"]
