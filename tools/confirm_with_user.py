"""confirm_with_user 工具 — 产出单元拍板载体（interrupt 暂停）。

双 agent 管线（REQ-20261011-142529 FR-003，DEC-010）的单元拍板口：
起草 → 本工具拍板 → 批准后落盘 → 停下（严禁自动连跑）。

机制不变（2026-10-02 死锁修复延续）：interrupt() 暂停整个图 → 执行端收尾
awaiting_input → 前端渲染选项区 → 用户回复 Command(resume=...) → resume 值
作为本工具返回值回到模型。挂起发生在工具节点，不经过 after_model 产物
校验，天然绕开互锁。

storystructure 泳道专属（DEC-016，FR-007）：question 尾部自动附程序体检
报告（checkup.compute——爽点间隔/钩子/大爽点/悬置/境界余量，只建议不拦
截），拍板的用户与拍板后的模型都看得到同一份程序证据。
"""
from __future__ import annotations

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

CONFIRM_WITH_USER_TOOL_DESCRIPTION = """提交本产出单元的草稿并暂停等待用户拍板（单元流程第 3 步，唯一合法等待方式）。

question 放草稿全文（不是摘要）+ 检验清单自查结果；有创意分歧时给 2-3 个方向选项。
调用后运行挂起，用户看到全文并回复；回复内容作为本工具返回值。
一次有效回复即拍板：选定 → 以选定稿落盘；调整/补充 → 消化吸收后落盘；
用户明示「不用问了直接写」→ 以推荐稿落盘。落盘后本轮收束，严禁自动进入下一单元。
不要用输出纯文本的方式等待用户回复。"""


class ConfirmOption(BaseModel):
    label: str = Field(min_length=1, max_length=12, description="选项标签，≤12 字")
    description: str = Field(max_length=30, description="一句话解释选项含义，≤30 字")


class ConfirmWithUserInput(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=16000,
        description="要请用户确认的完整文本（草稿全文 + 检验结果）",
    )
    options: list[ConfirmOption] = Field(
        min_length=1,
        max_length=6,
        description="结构化选项（1-6 项），给用户确认/修改的引导入口",
    )
    multi_select: bool = Field(default=False, description="是否允许多选，默认 false")


def build_confirm_payload(
    question: str,
    options: list[ConfirmOption],
    multi_select: bool = False,
    *,
    source: str = "confirm",
) -> dict[str, object]:
    """构造 interrupt payload（纯逻辑，便于测试）。

    kind="choice"（hitl.py 协议）；source 标记拍板来源泳道，
    供执行端 SSE / trace 归因。
    """
    return {
        "kind": "choice",
        "source": source,
        "question": question,
        "options": [opt.model_dump() for opt in options],
        "multi_select": multi_select,
    }


def build_confirm_with_user_tool(
    *,
    source: str = "confirm",
    checkup_renderer=None,
) -> StructuredTool:
    """构建拍板工具。

    Args:
        source: 泳道来源标签（如 "storydesign-confirm"）。
        checkup_renderer: 可选零参回调，返回附加到 question 尾部的体检
            报告文本（storystructure 泳道注入；None = 不附）。
    """

    def confirm_with_user(
        question: str,
        options: list[ConfirmOption],
        multi_select: bool = False,
    ) -> str:
        if checkup_renderer is not None:
            section = checkup_renderer()
            if section:
                question = f"{question}{section}"
        payload = build_confirm_payload(question, options, multi_select, source=source)
        answer: str = interrupt(payload)
        return answer

    return StructuredTool.from_function(
        name="confirm_with_user",
        description=CONFIRM_WITH_USER_TOOL_DESCRIPTION,
        func=confirm_with_user,
        args_schema=ConfirmWithUserInput,
        infer_schema=False,
    )


def build(abc):
    """架构清单挂载钩子：按泳道装配（structure 泳道附体检报告）。"""
    agent_name = getattr(abc.agent, "name", "")
    checkup_renderer = None
    if agent_name == "storystructure":
        from ..checkup import render_checkup_section

        checkup_renderer = lambda: render_checkup_section(  # noqa: E731 — 闭包绑定当前 workspace
            abc.workspace_path, abc.pkg_dir
        )
    return build_confirm_with_user_tool(
        source=f"{agent_name}-confirm" if agent_name else "confirm",
        checkup_renderer=checkup_renderer,
    )


__all__ = [
    "CONFIRM_WITH_USER_TOOL_DESCRIPTION",
    "ConfirmOption",
    "ConfirmWithUserInput",
    "build",
    "build_confirm_payload",
    "build_confirm_with_user_tool",
]
