"""submit_rework_ticket 工具 — 回炉工单的提交与拍板载体（REQ-20261011-142529 FR-004，DEC-008/020）。

结构端（storystructure）需要修改上游产物时的唯一合法路径：白名单硬拦上游
写入，唯一出路就是本工具。回炉三停协议的第一停：

  1. 提案（本工具，interrupt 请用户拍板）← 你在这里
  2. 设计端执行上游修订（design_revising）
  3. 结构端波及修订（impact_revising）

interrupt 恢复后按用户答复分支：
  - 批准 → 系统落盘 outline/rework/NNN-提案.md，管线状态置 design_revising
    （contracts.outline_pipeline 共享契约，executor 管线层据此路由设计端）
  - 否决 → 不落盘不置状态；返回指令要求结构端在上游不变前提下自行消化，
    决策写进台账备注。
"""
from __future__ import annotations

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from contracts.outline_pipeline import (
    REWORK_DESIGN,
    ReworkState,
    load_pipeline_state,
    next_ticket_id,
    save_pipeline_state,
    write_rework_ticket,
)

SUBMIT_REWORK_TICKET_DESCRIPTION = """提交回炉工单并暂停等待用户拍板（需要修改上游产物时的唯一合法路径）。

上游产物（outline/00-03、character/core/、worldview/core.md）你写不了——白名单会拦。
需要改（境界余量不足、BOSS/金手指设定与已写章纲冲突、用户要求改上游）时：
起草提案后调用本工具。proposal 放提案全文（改什么/为什么非改不可/修订方案），
impact_files 列出波及清单（要跟着改的下游文件）。批准后由设计端执行修订；
否决则你在上游不变前提下自行消化，决策写进台账备注。"""


class ReworkTicketInput(BaseModel):
    title: str = Field(min_length=1, max_length=30, description="工单标题，≤30 字（如：境界段不足以支撑第 3 卷）")
    proposal: str = Field(
        min_length=1,
        max_length=6000,
        description="提案全文 markdown：改什么 / 为什么非改不可 / 修订方案",
    )
    impact_files: list[str] = Field(
        min_length=1,
        max_length=20,
        description="波及清单：批准后需要跟着修订的下游文件（相对 workspace 路径）",
    )


def build_rework_ticket_tool(workspace_path):
    def submit_rework_ticket(
        title: str,
        proposal: str,
        impact_files: list[str],
    ) -> str:
        impact_md = "\n".join(f"- {f}" for f in impact_files)
        question = (
            f"【回炉工单提案】{title}\n\n{proposal}\n\n"
            f"【波及清单】批准后需跟着修订的下游内容：\n{impact_md}\n\n"
            "批准 = 转设计端执行上游修订，随后结构端做波及修订；"
            "否决 = 结构端在上游不变前提下自行消化。"
        )
        answer: str = interrupt({
            "kind": "choice",
            "source": "rework-ticket",
            "question": question,
            "options": [
                {"label": "批准，转设计端修订", "description": "落盘工单，设计端执行修订"},
                {"label": "否决，自行消化", "description": "不改上游，结构端消化并在台账备注"},
            ],
            "multi_select": False,
        })
        if "批准" in (answer or ""):
            ticket_id = next_ticket_id(workspace_path)
            proposal_md = (
                f"# 回炉工单 {ticket_id}：{title}\n\n## 提案\n{proposal}\n\n"
                f"## 波及清单\n{impact_md}\n\n## 状态\n- [x] 用户已批准\n- [ ] 设计端修订完成\n- [ ] 波及修订完成\n"
            )
            write_rework_ticket(workspace_path, ticket_id, title, proposal_md)
            state = load_pipeline_state(workspace_path)
            state.rework = ReworkState(ticket=ticket_id, title=title, stage=REWORK_DESIGN)
            save_pipeline_state(workspace_path, state)
            return (
                f"工单已批准并落盘：outline/rework/{ticket_id}-提案.md（管线已转设计端修订）。"
                "本轮到此为止：向用户一句话汇报后结束，不要继续写任何产物。"
            )
        return (
            "用户否决了回炉提案：上游保持不变。在上游不变的前提下自行消化本次问题，"
            "把消化决策写进期待台账备注（如「V-05 改为绕开境界限制的写法，第 N 批执行」），"
            "然后按原单元流程继续或收束本轮。"
        )

    return StructuredTool.from_function(
        name="submit_rework_ticket",
        description=SUBMIT_REWORK_TICKET_DESCRIPTION,
        func=submit_rework_ticket,
        args_schema=ReworkTicketInput,
        infer_schema=False,
    )


def build(abc):
    """架构清单挂载钩子：绑定当前 workspace。"""
    return build_rework_ticket_tool(abc.workspace_path)


__all__ = [
    "SUBMIT_REWORK_TICKET_DESCRIPTION",
    "ReworkTicketInput",
    "build",
    "build_rework_ticket_tool",
]
