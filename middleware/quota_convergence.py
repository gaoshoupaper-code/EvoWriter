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

# 默认增量预算：模型调用数上限。执行端 recursion_limit=300 计的是 LangGraph
# 超步（super-step）——实测 v14 编排栈（deepagents 0.6.1 / langchain 1.3.1 /
# langgraph 1.2.0）每次模型调用消耗 5 个超步：QuotaConvergence.before_model +
# ContextAssembler.before_model + model 节点 + TodoListMiddleware.after_model +
# tools 节点，即 300 超步 ≈ 60 次模型调用（GraphRecursionError 硬死点）。
# 预算 = (300 − 60 收尾余量) / 5 = 48，保证软着陆先于硬限制触发。
# 后续在编排链增删 before_model/after_model 中间件时须重算此系数。
DEFAULT_MAX_MODEL_CALLS = 48


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
                "维护 storyline/timeline.md 一致性，按流程调用 review 审查并按需修订一次，然后返回。"
                "剩余配比差距视为配比让步（配比是上限而非必达）。"
            ))

        count = count_workspace(self.workspace_path)
        by_type = count["by_type"] or {}
        status = evaluate_quota(self.target, by_type)

        if status.achieved:
            return HumanMessage(content=(
                f"[配比导航 第{cycle}轮] {status.summary_line()}。"
                "已达标：停止新增故事线与人物，进入收尾——"
                "检查 storyline/timeline.md 与各线详情、一览表一致，"
                "然后按流程调用 review 审查并按需修订一次，最后返回。"
            ))

        return HumanMessage(content=(
            f"[配比导航 第{cycle}轮] {status.summary_line()}。"
            "继续下一轮增量：按系统提示词的增量分流规则（人物/故事线比值 R）"
            "选择新增故事线或新增人物，直至达标或预算耗尽。"
        ))


__all__ = ["DEFAULT_MAX_MODEL_CALLS", "QuotaConvergenceMiddleware"]
