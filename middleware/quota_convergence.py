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
    """配比导航中间件：未达标驱动继续、达标收尾、预算耗尽强制收尾。

    交互模式批次（进化 #4）：batch_lines=1（默认）时单元完成即收尾（一轮一个
    增量单元，返回摘要交用户审阅）；>1 为连写批次，新增线数达标才收尾。
    收口判定机械核对（运行开始基线快照 vs 当前实况），不依赖 LLM 自觉。
    """

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
        # 交互模式批次参数（进化 #4）：默认一轮一个增量单元；用户指示连写时由
        # set_increment_batch 工具设为 N（按新增故事线计），每次运行开始重置回 1
        self.batch_lines = 1
        # 联动基线恢复回调（装配层注入：运行开始时恢复 RevisionLimit /
        # SingleLineLimit 的装配基线；None = 无联动，独立运行/测试可用）
        self.baseline_callback = None
        self._baseline: tuple[int, int] | None = None
        self._model_calls = 0

    # ── 运行周期重置（每次 graph 执行开始）──────────────────────

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._model_calls = 0
        self._reset_batch()

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._model_calls = 0
        self._reset_batch()

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

    # ── 交互模式批次（进化 #4）：运行开始重置 + 基线快照 + 收口判定 ──

    def _reset_batch(self) -> None:
        """运行开始：批次回默认 1、恢复联动中间件基线、快照工作区基线。"""
        self.batch_lines = 1
        if self.baseline_callback is not None:
            try:
                self.baseline_callback()
            except Exception:  # noqa: BLE001 — 恢复失败不阻断运行
                pass
        self._baseline = self._snapshot()

    def _snapshot(self) -> tuple[int, int]:
        """工作区基线快照：(故事线区块数, 人物档案数)。"""
        try:
            count = count_workspace(self.workspace_path)
            by_type = count.get("by_type") or {}
            lines = sum(by_type.values())
        except Exception:  # noqa: BLE001 — 计数失败返回 (0, 0)，批次判定退化为不触发
            return (0, 0)
        try:
            chars = len(list((self.workspace_path / "character").glob("*.md")))
        except Exception:  # noqa: BLE001
            chars = 0
        return (lines, chars)

    def _batch_complete(self) -> bool:
        """批次收口判定：默认（1）= 新增任一单元（线或人物）即收；连写（N>1）
        = 新增线数 ≥ N。基线缺失时不触发（退化由配比/预算兜底）。"""
        if self._baseline is None:
            return False
        try:
            count = count_workspace(self.workspace_path)
            by_type = count.get("by_type") or {}
            lines = sum(by_type.values())
            chars = len(list((self.workspace_path / "character").glob("*.md")))
        except Exception:  # noqa: BLE001
            return False
        new_lines = lines - self._baseline[0]
        new_chars = chars - self._baseline[1]
        if self.batch_lines > 1:
            return new_lines >= self.batch_lines
        return (new_lines + new_chars) >= 1

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
        batch_done = self._batch_complete()

        if status.achieved or batch_done:
            if status.achieved:
                reason = "配比已达标"
            elif self.batch_lines > 1:
                reason = f"连写批次已完成（{self.batch_lines} 条线全部落地）"
            else:
                reason = "本轮增量单元已完成（交互模式默认一轮一个单元）"
            return HumanMessage(content=(
                f"[配比导航 第{cycle}轮] {status.summary_line()}；{reason}。"
                "停止新增故事线与人物，进入收尾——"
                "核对 storyline.md 事件表时序号、交汇标注与线头字段完整，"
                "然后按流程调用 review 审查并按需修订一次"
                "（本轮产物有修改而尚未审查的必须先审，未审直接返回会被系统闸门拦回），"
                "最后返回摘要交用户审阅（返回后等待用户反馈；"
                "用户明确说「连写 N 条」后下一轮才会连续产出）。"
            ))

        return HumanMessage(content=(
            f"[配比导航 第{cycle}轮] {status.summary_line()}。"
            "继续本轮增量：按系统提示词的增量分流规则（人物/故事线比值 R）"
            "选择新增故事线或新增人物，直至单元/批次完成、达标或预算耗尽。"
        ))


__all__ = ["DEFAULT_MAX_MODEL_CALLS", "QuotaConvergenceMiddleware"]
