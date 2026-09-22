"""故事构建护栏预算（v14 收缩为工具模块，REQ-20260922-162823）。

v13 单故事专家装配（build_storybuilding_* 函数）已随 v14 多 Agent 架构切换
退役——完整定义保留在 bare repo 的 v13 commit，回滚 v13 即 checkout 对应版本。
本模块保留被 v14 复用的线数预算解析（连续增量护栏参数化，FR-002①）。
"""
from __future__ import annotations

from pathlib import Path

# 连续增量负载（REQ-20260922-162823 FR-002）：
#   - 有配比（full/semi）：单线护栏预算 = 目标线总数 + 余量（防中途废稿重写卡护栏）
#   - 无配比（minimal / 生产缺字段，DEC-013 软终止）：固定宽松上限防失控
#     （与 QuotaConvergence 的模型调用预算构成双层防护）
_MINIMAL_LINE_BUDGET = 8
_LINE_BUDGET_MARGIN = 2


def resolve_line_budget(target) -> int:
    """按 demand 目标配比计算本次运行的新增故事线预算（护栏 max_new_lines）。"""
    if target is None:
        return _MINIMAL_LINE_BUDGET
    return target.total() + _LINE_BUDGET_MARGIN


__all__ = ["resolve_line_budget"]
