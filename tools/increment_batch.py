"""set_increment_batch 工具 — 交互模式连写批次设置（进化 #4）。

语义（用户拍板）：
  - N 按新增故事线计：「连写 3」= 本轮落地 3 条新线；人物单元作为配套
    穿插（R 分流自然发生），不计入批次；收口判定由 QuotaConvergence 按
    「本运行新增线区块数 ≥ N 或配比达标」机械核对。
  - N 上限 = demand 目标配比余量，超出自动对齐（「连写 100」→「连写到达标」，
    「自动跑完」= 连写剩余全部）。
  - 批次仅当次运行有效：QuotaConvergence.before_agent 每轮重置
    batch_lines=1 并回调恢复联动中间件基线，无跨运行状态。

三护栏联动（同一次设置内同步更新，防语义漂移）：
  - QuotaConvergence.batch_lines → 导航注入批次收口判定
  - RevisionLimit.max_revisions → 每单元一次 review；N 线 ≈ N + 3(N-1)
    单元，放宽为 4N 带余量（原硬上限 1 会在第 2 次 review 调用直接终止运行）
  - StorylineSingleLineLimit.max_new_lines → 至少放行 N 条新线

实现为工厂闭包：工具与中间件同进程装配，直接改实例属性，不触碰
State/reducer（合法路径：工厂闭包引用，非 State 操作）。
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

INCREMENT_BATCH_TOOL_DESCRIPTION = """设置本轮运行的连写批次数（仅在用户明确指示连写时调用）。

N = 本轮要新增的故事线数（人物单元作为配套穿插不计入）；N 超过配比余量会自动对齐到余量。
设置后本批次内连续产出 N 条线（每个增量单元各自完成 review 审查一次），N 条全部落地或配比达标后收尾返回。
批次仅本次运行有效，运行结束自动回到默认（一轮一个增量单元）。
用户没有明确说「连写 N 条」时不要调用本工具。"""


def clamp_batch_lines(n: int, target: Any, current_lines: int) -> tuple[int, str]:
    """按配比余量截断批次数，返回 (生效值, 说明文本)。

    target 为 None（留白档软终止）时不设天花板，按请求值生效。
    """
    if target is None:
        return max(n, 1), ""
    remaining = max(target.total() - current_lines, 0)
    if n > remaining:
        effective = max(remaining, 1)
        return effective, (
            f"（请求 {n} 条已超过配比余量 {remaining} 条，自动对齐为 {effective}）"
        )
    return n, ""


def apply_batch_updates(
    quota_middleware: Any,
    revision_limit_middleware: Any,
    line_limit_middleware: Any,
    n: int,
) -> int:
    """把批次数 n 应用到三护栏（闭包联动），返回生效值。

    纯逻辑函数（便于测试）：不读工作区，天花板截断由调用方完成。
    """
    effective = max(int(n), 1)
    quota_middleware.batch_lines = effective
    # 每单元一次 review：N 线 ≈ 4N 单元带余量；下限 1（默认轮语义）
    revision_limit_middleware.max_revisions = max(1, 4 * effective)
    # 线数预算：不低于装配基线（配比预算），至少放行 N 条
    line_limit_middleware.max_new_lines = max(
        line_limit_middleware.max_new_lines, effective
    )
    return effective


def build_increment_batch_tool(
    quota_middleware: Any,
    revision_limit_middleware: Any,
    line_limit_middleware: Any,
) -> StructuredTool:
    """构建 set_increment_batch 工具（闭包引用三个中间件实例）。

    Args:
        quota_middleware:         QuotaConvergenceMiddleware 实例（批次参数 + 工作区路径）
        revision_limit_middleware: RevisionLimitMiddleware 实例（review 上限联动）
        line_limit_middleware:     StorylineSingleLineLimitMiddleware 实例（线数预算联动）
    """
    workspace_path = quota_middleware.workspace_path

    def set_increment_batch(line_count: int) -> str:
        n = int(line_count)
        if n < 1:
            return "错误：line_count 必须 ≥ 1。用户未指示连写时不要调用本工具。"

        # 配比余量天花板（复用 QuotaConvergence 的配比判定器来源）
        from contracts.storybuilding_quota import count_workspace

        try:
            count = count_workspace(workspace_path)
            by_type = count.get("by_type") or {}
        except Exception:  # noqa: BLE001 — 计数失败不设天花板，交导航兜底
            by_type = {}
        current_lines = sum(by_type.values())

        effective, note = clamp_batch_lines(n, quota_middleware.target, current_lines)
        apply_batch_updates(
            quota_middleware,
            revision_limit_middleware,
            line_limit_middleware,
            effective,
        )
        return (
            f"已设置本批次连写 {effective} 条新故事线{note}。"
            "本批次内按 R 分流连续产出（人物单元为配套穿插、不计入批次），"
            "每个增量单元完成后各调用一次 review 审查；"
            f"{effective} 条线全部落地或配比达标后收尾返回。"
            f"review 上限已同步放宽至 {max(1, 4 * effective)} 次（本运行内有效）。"
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
    "clamp_batch_lines",
]
