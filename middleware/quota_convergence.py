"""QuotaConvergenceMiddleware — 连续增量构建的配比导航中间件（REQ-20260922-162823 FR-001②/FR-002③）。

连续增量负载的循环驱动者：每轮模型调用前核对 demand 目标配比与工作区实况，
把「继续增量 / 已达标收尾 / 预算耗尽强制收尾」三类导航指令注入对话。

设计约束（DEC-011 同一判定器 / DEC-013 软终止）：
  - 配比解析与核对逻辑来自 ``contracts.storybuilding_quota``（唯一实现），
    v13 单 Agent 版与 v14 多 Agent 版挂载同一份本中间件——终止语义机械一致，
    不依赖 LLM 自觉，也不因架构不同而漂移。
  - demand 无配比（minimal 档留白 / 生产表单缺字段）→ target 为 None，
    本中间件不注入任何指令，收束时机完全交给 agent 自判（DEC-013 软终止）。
  - ``max_model_calls`` 为增量预算上限（防失控，FR-001⑤），超过即注入强制
    收尾指令；实际值经 pilot 校准后由装配层传入。

指令注入采用每轮一条短 HumanMessage（ReAct 循环中持续提醒，防止长上下文
淡忘早期目标）。三类指令文本即「循环驱动方法论」本体，双臂逐字同源。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage

from contracts.storybuilding_quota import (
    QuotaTarget,
    count_workspace,
    evaluate_quota,
)

# 默认增量预算：模型调用数上限（REQ-20261002-125538 DEC-005 过渡期上调 48→60）。
# 历史口径已失准：v14 注释按「每模型调用 5 超步」精算 (300−60)/5=48，但依赖
# 升级（deepagents 0.7.19 / langgraph 1.2.12）+ 中间件增删后，2026-10-01 线上
# trace 实测 ≈7.1 超步/次，48 预算的软着陆来不及触发，42 次调用撞 300 硬顶
# （GraphRecursionError）。执行端已把 recursion_limit 保险丝化（300→1000），
# 精算关系不再成立，改为守护测试锁定「预算 × 保守系数 8 < 保险丝 1000」
# （tests/test_harness_quota_convergence.py::test_default_budget_below_recursion_limit）。
# 60 = 过渡期对冲 edit_file old_string 失配浪费（DEC-004 另开需求）；
# 失配修复上线后复评回收。编排栈再升级时以新实测系数重算。
DEFAULT_MAX_MODEL_CALLS = 60


class QuotaConvergenceMiddleware(AgentMiddleware):
    """配比导航中间件：未达标驱动继续、达标收尾、预算耗尽强制收尾。"""

    def __init__(
        self,
        workspace_path: Path,
        target: QuotaTarget | None,
        *,
        max_model_calls: int = DEFAULT_MAX_MODEL_CALLS,
    ) -> None:
        """
        Args:
            workspace_path:   工作区根目录（实时计数用）
            target:           demand 解析出的目标配比；None = 软终止模式（不注入）
            max_model_calls:  增量预算（模型调用数上限），超过注入强制收尾
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.target = target
        self.max_model_calls = max_model_calls
        self._model_calls = 0

    # ── 运行周期重置（每次 graph 执行开始）──────────────────────

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._model_calls = 0

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._model_calls = 0

    # ── 每轮模型调用前注入导航指令 ──────────────────────────────

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        message = self._build_message()
        if message is None:
            return None
        return {"messages": [message]}

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        message = self._build_message()
        if message is None:
            return None
        return {"messages": [message]}

    # ── 导航判定（纯逻辑，便于测试）────────────────────────────

    def _build_message(self) -> HumanMessage | None:
        """构造本轮导航指令；软终止模式（target=None）恒返回 None。"""
        if self.target is None:
            return None

        self._model_calls += 1
        cycle = self._model_calls

        # 预算耗尽 → 强制收尾（优先级最高，覆盖「未达标继续」）
        if cycle > self.max_model_calls:
            return HumanMessage(content=(
                f"[配比导航 第{cycle}轮] 已达增量预算上限（{self.max_model_calls} 轮模型调用）。"
                "立即停止新增故事线与人物，基于现有内容收尾："
                "核对 storyline.md 事件表时序号、交汇标注与线头字段完整，按流程调用 review 审查并按需修订一次，然后返回。"
                "剩余配比差距视为配比让步（配比是上限而非必达）。"
            ))

        count = count_workspace(self.workspace_path)
        by_type = count["by_type"] or {}
        status = evaluate_quota(self.target, by_type)

        if status.achieved:
            return HumanMessage(content=(
                f"[配比导航 第{cycle}轮] {status.summary_line()}。"
                "已达标：停止新增故事线与人物，进入收尾——"
                "核对 storyline.md 事件表时序号、交汇标注与线头字段完整，"
                "然后按流程调用 review 审查并按需修订一次，最后返回。"
            ))

        return HumanMessage(content=(
            f"[配比导航 第{cycle}轮] {status.summary_line()}。"
            "继续下一轮增量：按系统提示词的增量分流规则（人物/故事线比值 R）"
            "选择新增故事线或新增人物，直至达标或预算耗尽。"
        ))


__all__ = ["DEFAULT_MAX_MODEL_CALLS", "QuotaConvergenceMiddleware"]


def _load_quota_target(abc):
    from contracts.storybuilding_quota import parse_demand_quota

    demand_path = abc.workspace_path / "demand.md"
    demand_md = demand_path.read_text(encoding="utf-8") if demand_path.exists() else ""
    return parse_demand_quota(demand_md)


def build(abc):
    """架构清单挂载钩子：domain 导航——配比收敛（预算常量随模块口径演进）。"""
    return QuotaConvergenceMiddleware(
        abc.workspace_path,
        _load_quota_target(abc),
        max_model_calls=DEFAULT_MAX_MODEL_CALLS,
    )
