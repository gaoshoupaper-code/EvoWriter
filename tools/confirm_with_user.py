"""confirm_with_user 工具 — 理解回执轮的用户确认载体（interrupt 暂停）。

回执轮死锁根因（2026-10-02 线上）：回执轮靠「输出回执文本后结束运行」暂停，
但 ArtifactValidationMiddleware 要求 storyline.md 已产出才放行终局——回执轮
恰好禁止写产物，两个闸门互锁，模型在「想停被拦回」与「想写被拦截」之间
反复挣扎（trace 表现为连续重复模型调用，输入框全程锁死）。

本工具把暂停改为真实的图挂起：interrupt() 暂停整个图 → 执行端收尾
awaiting_input → 前端渲染选项区并解锁输入 → 用户回复 Command(resume=...) →
resume 值作为本工具返回值回到模型，同一运行内继续初构。挂起发生在工具节点，
不经过 after_model 产物校验，天然绕开互锁。

与 interview 子代理的 ask_user 同机制（方式2 HITL），payload 按 hitl.py 协议
带 kind="choice"，前端按现有选项化组件渲染（选项 + 自定义/补充入口）。
"""
from __future__ import annotations

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

CONFIRM_WITH_USER_TOOL_DESCRIPTION = """提交理解回执并暂停等待用户确认（回执轮唯一合法的等待方式）。

调用后运行挂起，用户在前端看到回执全文并回复；回复内容（选中选项 + 可选补充文字）
作为本工具返回值。用于首轮理解回执：question 放完整回执（需求复述 + 假设清单 +
故事核心五字段草案），options 给确认/修改引导（如「确认草案，开始初构」「我要修改，见补充」）。
用户回复后按其意见继续：确认 → 以确认稿为锚开始初构；修改 → 按修改意见调整后再动笔。
不要用输出纯文本的方式等待用户回复——产物未写出时终局会被产物校验拦截，运行无法结束。"""


class ConfirmOption(BaseModel):
    label: str = Field(min_length=1, max_length=12, description="选项标签，≤12 字")
    description: str = Field(
        max_length=30, description="一句话解释选项含义，≤30 字"
    )


class ConfirmWithUserInput(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=8000,
        description="要请用户确认的完整文本（如理解回执全文）",
    )
    options: list[ConfirmOption] = Field(
        min_length=1,
        max_length=6,
        description="结构化选项（1-6 项），给用户确认/修改的引导入口",
    )
    multi_select: bool = Field(
        default=False,
        description="是否允许多选，默认 false",
    )


def build_confirm_payload(
    question: str,
    options: list[ConfirmOption],
    multi_select: bool = False,
) -> dict[str, object]:
    """构造 interrupt payload（纯逻辑，便于测试）。

    kind="choice" 显式声明（hitl.py 协议）；source 标记回执轮来源，
    供执行端 SSE / trace 归因（默认值兜底为 choice 的旧口径）。
    """
    return {
        "kind": "choice",
        "source": "storybuilding-receipt",
        "question": question,
        "options": [opt.model_dump() for opt in options],
        "multi_select": multi_select,
    }


def build_confirm_with_user_tool() -> StructuredTool:
    def confirm_with_user(
        question: str,
        options: list[ConfirmOption],
        multi_select: bool = False,
    ) -> str:
        # interrupt() 暂停整个图；Command(resume=用户回复) 后，resume 值
        # 作为返回值回到模型继续推理。挂起期间运行收尾 awaiting_input。
        payload = build_confirm_payload(question, options, multi_select)
        answer: str = interrupt(payload)
        return answer

    return StructuredTool.from_function(
        name="confirm_with_user",
        description=CONFIRM_WITH_USER_TOOL_DESCRIPTION,
        func=confirm_with_user,
        args_schema=ConfirmWithUserInput,
        infer_schema=False,
    )


__all__ = [
    "CONFIRM_WITH_USER_TOOL_DESCRIPTION",
    "ConfirmOption",
    "ConfirmWithUserInput",
    "build_confirm_payload",
    "build_confirm_with_user_tool",
]
