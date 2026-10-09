"""set_increment_batch 工具 — 连写授权工具（进化 #4 创建，#10 重接线）。

语义演变：
  - #4（配比导航时代）：用户指示「连写 N 条」时设置批次，配比导航
    （QuotaConvergence）按「新增线区块 ≥ N 或配比达标」机械收口。
  - #10（每轮拍板架构）：配比导航已卸载，收口语义变为「一次拍板授权
    N 个增量单元」——用户在 confirm_with_user 回复中明示连写授权后，
    模型据此工具登记授权数，ReceiptGate 拍板闸门与 review 上限随批次
    放宽（N 单元 = N 次审查），N 个单元完成后自动收束、逐单元确认恢复。
    本工具不代收口（无机械循环驱动），只放宽两道闸门的上限——收口由
    任务卡的授权语义与汇报纪律承载。

使用前提（三层流程，系统提示词 §二②）：连写授权是拍板的组成部分，
只应在 confirm_with_user 回复明示「连写 N 个单元」后、执行层开始前
调用一次；默认每轮任务 = 1 个增量单元，无需调用本工具。

实现为工厂闭包：工具与中间件同进程装配，直接改实例属性，不触碰
State/reducer（合法路径：工厂闭包引用，非 State 操作）。
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

INCREMENT_BATCH_TOOL_DESCRIPTION = """登记本轮任务的连写授权（仅在用户明示「连写 N 个单元」时调用）。

N = 用户一次拍板授权的增量单元数（每个单元完成后仍各自调用一次 review 审查）；
设置后本批次内连续执行 N 个单元（执行层不再逐单元向用户确认），N 个单元全部完成后
收束汇报、恢复逐单元确认节奏。用户没有明确授权连写时不要调用本工具；
连写授权是拍板（confirm_with_user 回复）的一部分——先拍板、后调用本工具。"""


def clamp_batch_units(n: int) -> tuple[int, str]:
    """连写单元数取值钳制，返回 (生效值, 说明文本)。"""
    if n < 1:
        return 1, "（请求 < 1 已按 1 生效：本轮至少一个增量单元）"
    return n, ""


def apply_batch_updates(
    revision_limit_middleware: Any,
    line_limit_middleware: Any,
    n: int,
) -> int:
    """把授权单元数 n 应用到两道闸门（闭包联动），返回生效值。

    纯逻辑函数（便于测试）：不读工作区。

    每单元一次 review：N 单元放宽为 max(1, N) 次带余量；线数预算：不
    低于装配基线（配比预算），至少放行 N 条（单元以加线为主体的口径，
    人物单元作为配套穿插不占线额度，预算带余量不误伤）。
    """
    effective = max(int(n), 1)
    # review 上限：N 单元各审一次；带 2 的余量防物品卡建卡轮误伤
    revision_limit_middleware.max_revisions = max(1, effective + 2)
    line_limit_middleware.max_new_lines = max(
        line_limit_middleware.max_new_lines, effective
    )
    return effective


def build_increment_batch_tool(
    revision_limit_middleware: Any,
    line_limit_middleware: Any,
) -> StructuredTool:
    """构建 set_increment_batch 工具（闭包引用两个中间件实例）。

    Args:
        revision_limit_middleware: RevisionLimitMiddleware 实例（review 上限联动）
        line_limit_middleware:     StorylineSingleLineLimitMiddleware 实例（线数预算联动）
    """

    def set_increment_batch(unit_count: int) -> str:
        n = int(unit_count)
        effective, note = clamp_batch_units(n)
        apply_batch_updates(
            revision_limit_middleware,
            line_limit_middleware,
            effective,
        )
        return (
            f"已登记本批次连写授权 {effective} 个增量单元{note}。"
            "本批次内连续执行（每个增量单元完成后仍各自调用一次 review 审查），"
            f"{effective} 个单元全部完成后收束汇报，恢复逐单元确认节奏。"
            f"review 上限已同步放宽至 {max(1, effective + 2)} 次（本运行内有效）。"
        )

    return StructuredTool.from_function(
        name="set_increment_batch",
        description=INCREMENT_BATCH_TOOL_DESCRIPTION,
        func=set_increment_batch,
    )


__all__ = [
    "INCREMENT_BATCH_TOOL_DESCRIPTION",
    "apply_batch_updates",
    "build_increment_batch_tool",
    "clamp_batch_units",
]
