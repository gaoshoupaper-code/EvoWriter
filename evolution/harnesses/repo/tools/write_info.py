"""write_info 工具 — 逐段写作：写一段「信息」（建坐标、定空间）。"""
from __future__ import annotations

from .sentence_common import build_paragraph_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字。
WRITE_INFO_TOOL_DESCRIPTION = """写一段用于确立客观坐标的场景信息（时间、地点、空间布局、相对距离、视线与遮挡、在场/缺席人员及关键物件位置），让读者脑中有稳定画面，不迷路。

适用场景：
- 新场景开场，或人物进入新空间时，交代环境边界、大小、布局与感官锚点。
- 关键互动开始前，补清人物之间的相对位置、距离、朝向、遮挡物或道具所在。
- 场景中静态状态发生显性清单变化时（如明确谁已入席、谁仍缺席）。

写法规范：
- 写客观可感知的具体细节（视觉、声响、光线、温度、材质与方位关系），不写空洞概括（如「大殿很气派」），不掺人物主观评价。
- 重点交代「空间关系」而非零散摆设：说清人/物彼此离多远、隔着什么、能不能看清或够得着。
- 篇幅克制在 2–4 句，自然融入当下场景视角，禁止写成干巴巴的说明书或建筑清单。
- 主语明确、方位前后一致，读者一遍即可在脑中还原站位。

边界：
- 若细节的核心目的是埋设定规则、立人物性格、造压迫氛围或给后文转折留依据，改用 write_setup。
- 若涉及人物主动发起冲突、对话交锋或引发局势变化，改用 write_causal。
- 若只需简短切换时空或略过无关路程，改用 write_transition。"""

# writing_intent 的填法随工具而异，放进参数说明让模型在填参时直接读到。
WRITE_INFO_INTENT_GUIDE = (
    "按「确立的硬坐标 → 为后续哪类互动/视线提供依据」格式简写"
    "（如：交代大殿纵深、席位布局与殿心空地 → 支撑后续当众展示时众人都能看清）"
)


def build(abc):
    return build_paragraph_tool(abc, "write_info", WRITE_INFO_TOOL_DESCRIPTION, WRITE_INFO_INTENT_GUIDE)


__all__ = ["WRITE_INFO_TOOL_DESCRIPTION", "build"]
