"""ReviewGateMiddleware — review 执行下限闸门（防「不审而返」）。

与 RevisionLimitMiddleware（上限闸：count > max_revisions 时拦截，防多审）
合围，review 执行约束由「仅 ≤1 次」补全为「产物已产出 ⇒ ≥1 次」：

  RevisionLimit（上限闸）：防模型反复审查浪费预算
  ReviewGate（下限闸）  ：防模型收尾链截断、静默跳过审查

根因（2026-09-30 会话证据，harness@0e635fc）：4 条完成 trace 骨架均无
review 子代理节点——首建 trace（trace-e434ed1c…）写完 storyline.md 后
7s 即终局返回，收尾链「核对时序号 → 调用 review → 修订 → 返回」后半段
被静默截断。全部指令层（system prompt §6.5/§七、quota 导航指令、两个
Skill 步骤 4）均为软约束；RevisionLimitMiddleware 只在超出上限时拦截，
不防零调用。

机制：
  - wrap_tool_call 拦截 task(review) 仅计数不拦截（判定与 RevisionLimit 同款）
  - after_model 检测模型输出无 tool_calls（拟终局返回）时，若三者同时成立：
    storyline.md 已存在 + 本运行 review 未执行 + 注入未达上限
    → 注入 HumanMessage 强制先补审再返回（终局 AIMessage 不再是循环
      最后一条消息，ReAct 循环得以继续）
  - 注入上限（默认 2）防死循环：模型连续无视指令则放行返回（人工 review
    阶段兜底），并打印一次日志留痕

接口协议（deepagents 0.6.1 / langchain 1.3.1，2026-10-01 修复）：
  after_model / aafter_model 采用与 before_model 同族的二参签名
  (state, runtime)。原实现误用旧签名 (state, response)，首次运行即抛
  TypeError（harness@94782e8f，trace-d586244a…，33s 零产物失败）。闸门
  判定只依赖 state（response 从未参与），修复后不再臆取 response；hook
  整体 try/except 降级放行 + logger.exception 留痕——护栏故障不得中断
  创作主流程（对齐 storyline_contract_guard 的降级哲学）。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


class ReviewGateMiddleware(AgentMiddleware):
    """review 执行下限闸门：产物已产出而 review 未执行时，强制拦回补审。"""

    def __init__(
        self,
        workspace_root: Path,
        *,
        review_name: str = "review",
        max_injections: int = 2,
        storyline_relpath: str = "storyline.md",
    ) -> None:
        """
        Args:
            workspace_root:      工作区根目录（storyline.md 存在性判定用）
            review_name:         review 子代理的注册名称（task 调用目标匹配用）
            max_injections:      强制补审指令的最大注入次数（防死循环）
            storyline_relpath:   产物存在的判定文件（相对 workspace_root）
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.review_name = review_name
        self.max_injections = max_injections
        self.storyline_path = self.workspace_root / storyline_relpath
        self._review_calls = 0
        self._injections = 0
        self._exhausted_logged = False

    # ------------------------------------------------------------------
    # 运行周期重置（子代理每次被 task 委托 / 每次顶层运行开始时触发）
    # ------------------------------------------------------------------

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._review_calls = 0
        self._injections = 0
        self._exhausted_logged = False

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._review_calls = 0
        self._injections = 0
        self._exhausted_logged = False

    # ------------------------------------------------------------------
    # review 调用计数（不拦截，仅记录——上限由 RevisionLimit 负责）
    # ------------------------------------------------------------------

    def _is_review_task(self, request: Any) -> bool:
        """判断 task 工具调用是否目标是 review 子代理（与 RevisionLimit 同款）。"""
        tool_call = getattr(request, "tool_call", {})
        tool_name = _mapping_value(tool_call, "name")
        if tool_name != "task":
            return False
        args = _mapping_value(tool_call, "args")
        if not isinstance(args, dict):
            return False
        target = args.get("subagent_type") or args.get("name") or ""
        return target == self.review_name

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        """拦截同步工具调用：目标为 review 时累计计数，放行。"""
        if self._is_review_task(request):
            self._review_calls += 1
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        """拦截异步工具调用：目标为 review 时累计计数，放行。"""
        if self._is_review_task(request):
            self._review_calls += 1
        return await handler(request)

    # ------------------------------------------------------------------
    # 终局检测：模型拟结束且 review 未执行 → 注入强制补审指令
    # ------------------------------------------------------------------

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        try:
            return self._gate(state)
        except Exception:  # noqa: BLE001 — 闸门故障不得中断创作主流程
            logger.exception("ReviewGate after_model 异常，降级放行")
            return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        try:
            return self._gate(state)
        except Exception:  # noqa: BLE001 — 闸门故障不得中断创作主流程
            logger.exception("ReviewGate aafter_model 异常，降级放行")
            return None

    def _gate(self, state: Any) -> dict[str, Any] | None:
        """闸门判定（纯逻辑，便于测试）。

        Returns:
            {"messages": [HumanMessage]} 强制补审指令；None 表示放行。
        """
        # 条件一：本运行 review 已执行过 → 闸门已满足，放行
        if self._review_calls > 0:
            return None
        # 条件二：storyline.md 尚未产出 → 无产物可审，放行
        if not self.storyline_path.exists():
            return None
        # 条件三：模型并非拟终局（仍有工具调用）→ 循环自然继续，放行
        last = _last_message(state)
        if last is None or getattr(last, "tool_calls", None):
            return None
        # 条件四：注入已达上限 → 放行（防死循环），仅首次留日志
        if self._injections >= self.max_injections:
            if not self._exhausted_logged:
                self._exhausted_logged = True
                print(
                    "[ReviewGate] 强制补审注入已达上限"
                    f"（{self.max_injections} 次），模型仍未调用 review，"
                    f"放行返回（workspace={self.workspace_root}）"
                )
            return None
        # 三条件齐备：打断终局，强制补审
        self._injections += 1
        return {"messages": [HumanMessage(content=(
            "[review 闸门] 检测到 storyline.md 已产出，但本次运行从未调用 review "
            "子代理（收尾链被截断）。按 review 协议（收尾审查一次、按需修订一次）：立即调用 "
            "`review` 子代理统一审查全部产物，读取 review/storybuilding.md 审查"
            "报告，按需修订一次，然后再返回最终结果。产物已修改而未审查，"
            "不得直接结束。"
        ))]}


def _last_message(state: Any) -> Any:
    """安全取 state 消息列表的最后一条（dict / 对象两种形态兼容）。"""
    messages = (
        state.get("messages") if isinstance(state, dict)
        else getattr(state, "messages", None)
    )
    if not messages:
        return None
    return messages[-1]


def _mapping_value(mapping: object, key: str) -> Any:
    """安全地从字典或对象中取值。"""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return getattr(mapping, key, None)


def build(abc):
    """架构清单挂载钩子（M2，进化 #10）：review 执行下限闸——每轮运行
    产物有产出而 review 未执行时，终局前强制补审一次（对齐「每个增量
    单元完成后必审」的每单元审语义）。"""
    return ReviewGateMiddleware(abc.workspace_path)


__all__ = ["ReviewGateMiddleware", "build"]
