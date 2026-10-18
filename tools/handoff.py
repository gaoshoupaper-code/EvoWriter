"""handoff 工具 — 用户点名跳段（REQ-20261018-115508 FR-002，DEC-002）。

作者在对话里点名换段（「直接进第 1 卷卷纲」「回去改主题」）时，agent 调
本工具登记跳段请求：写入 .writing/state.json 的 override_stage，executor
下一轮路由消费（点名优先于目录扫描），消费即清。跳到前置缺失的段由下一轮
路由/提示词报前置缺失，不在此处拦截。
"""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from contracts.writing_artifacts import (
    STAGE_CHAPTER,
    STAGE_LABELS,
    STAGE_OUTLINE,
    STAGE_TEXT,
    STAGE_THEME,
    STAGE_VOLUME,
    load_writing_state,
    save_writing_state,
)

logger = logging.getLogger(__name__)

HANDOFF_TOOL_DESCRIPTION = """作者点名切换创作大段时调用（点名优先于目录进度）。

target_stage 五选一：theme（题材）/ outline（总纲）/ volume（卷纲）/
chapter（章纲）/ text（正文）。调用后本轮按当前段收尾；下一条作者消息起
进入目标段（含该段的提示词与写入权限）。仅作者明示跳段时用，不得自行调用。"""

_VALID = (STAGE_THEME, STAGE_OUTLINE, STAGE_VOLUME, STAGE_CHAPTER, STAGE_TEXT)


class HandoffInput(BaseModel):
    target_stage: str = Field(description="目标大段：theme/outline/volume/chapter/text")


def build_handoff_tool(workspace_root: Path) -> StructuredTool:
    def _handoff(target_stage: str) -> str:
        if target_stage not in _VALID:
            return "[handoff 失败] target_stage 只能是 theme/outline/volume/chapter/text。"
        # 用户确认凭据（S3 修复）：跳段解锁写入闸，必须由作者本人拍板——
        # interrupt 挂起，resume 值只有用户操作可产生（agent 自调无法伪造）。
        from langgraph.types import interrupt

        label = STAGE_LABELS.get(target_stage, target_stage)
        answer: str = interrupt({
            "kind": "choice",
            "source": "handoff-confirm",
            "question": (
                f"是否切换到「{label}」段？切换后下一条消息起按该段流程推进，"
                "写入权限同步切换（前置产物缺失会提示先走上游）。"
            ),
            "options": [
                {"label": "切换", "description": f"进入{label}段"},
                {"label": "取消", "description": "留在当前段"},
            ],
            "multi_select": False,
        })
        answer_text = answer if isinstance(answer, str) else ""
        # 白名单判定（round2 修复）：明确确认才跳段——匹配确认词或选项 label；
        # 模糊/未知/非字符串答复一律视为未确认（安全默认：不切段）。
        confirmed = (
            any(p in answer_text for p in ("切换", "确认", "进入"))
            or answer_text.strip().startswith("切换")
        )
        if not confirmed:
            return "作者未确认跳段：留在当前段继续（模糊答复按不切换处理）。"
        try:
            state = load_writing_state(workspace_root)
            state.override_stage = target_stage
            save_writing_state(workspace_root, state)
        except OSError as exc:
            return f"[handoff 失败] 状态写入失败：{type(exc).__name__}"
        return (
            f"作者已确认跳段：{label}（{target_stage}）。本轮按当前段收尾，"
            "下一条作者消息起生效；生效时提示词与写入闸同步切换到目标段。"
        )

    return StructuredTool.from_function(
        name="handoff",
        description=HANDOFF_TOOL_DESCRIPTION,
        func=_handoff,
        args_schema=HandoffInput,
        infer_schema=False,
    )


def build(abc):
    return build_handoff_tool(abc.workspace_path)


__all__ = [
    "HANDOFF_TOOL_DESCRIPTION",
    "HandoffInput",
    "build",
    "build_handoff_tool",
]
