"""write_transition 工具 — 逐段写作：写一段「过渡」（控节奏、接时空）。"""
from __future__ import annotations

from .sentence_common import build_paragraph_tool

# 说明全文单独成常量：要素页直接读它展示，页面显示的和模型读到的才是同一份文字。
WRITE_TRANSITION_TOOL_DESCRIPTION = """写一段连接两个因果节点的过渡内容，负责快进压缩非重点过程、缓冲高潮张力，或干净地切换时间、空间与叙事焦点，不改变核心大因果。

适用场景：
- 两个核心因果事件之间需要时空衔接、视线/焦点转移，或短暂的留白与余波沉淀。
- 存在非重点的例行位移、等待、重复性动作或时间跨度，需要一两句干脆带过以保持节奏紧凑。
- 剧烈冲突刚结束或即将升级前，用克制的停顿维持时空连续性与叙事张力。

写法规范：
- 明确区分两种用法，按当前节奏二选一：
  1. 快进压缩：用 1–2 句干净利落地概括重复过程或无关位移，杜绝机械拆解肢体微动作。
  2. 缓冲蓄势：用 1–3 句承接上一段落下的余波、静默或焦点移动，把镜头自然引向下一段。
- 承上启下明确：交代清楚从什么状态/时间/位置切走，落到哪里，避免跳跃突兀。
- 篇幅精短（1–3 句），克制不注水。

边界：
- 严禁用 write_transition 一笔带过章纲要求的核心事件、关键冲突、关键展示过程或重要后果（这些必须用 write_causal 写实）。
- 切换到全新场地且后续互动强依赖具体布局与距离时，需紧接调用 write_info 锚定空间。"""

# writing_intent 的填法随工具而异，放进参数说明让模型在填参时直接读到。
WRITE_TRANSITION_INTENT_GUIDE = (
    "按「承接上一状态 → 快进/缓冲目的与落点」格式简写"
    "（如：承接展示结束后的死寂 → 快速压缩复位过程，将焦点切回空着的席位）"
)


def build(abc):
    return build_paragraph_tool(abc, "write_transition", WRITE_TRANSITION_TOOL_DESCRIPTION, WRITE_TRANSITION_INTENT_GUIDE)


__all__ = ["WRITE_TRANSITION_TOOL_DESCRIPTION", "build"]
