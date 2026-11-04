"""RevisionLimitMiddleware — 修订次数硬上限中间件。

职责：
  拦截 DeepAgent 的 task 工具调用，当目标是登记了上限的审查类子代理
  （如 review、volume_qc）时按目标分别累计调用次数，超过上限后返回
  强制终止的 ToolMessage，阻止继续调用。

使用方式：
  在构建 DeepAgent 子代理时加入中间件列表。
  limits: {子代理名: 上限次数} 映射；未登记的目标不计数不拦截。
  兼容旧构造：max_revisions + review_name 等价于 limits={"review": max_revisions}。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp


class RevisionLimitMiddleware(AgentMiddleware):
    """修订次数硬上限中间件。

    通过拦截 task 工具调用，按目标子代理分别累计计数，
    超过该目标上限后返回包含终止指令的 ToolMessage，阻止继续修订。
    """

    def __init__(
        self,
        *,
        limits: Mapping[str, int] | None = None,
        max_revisions: int = 1,
        review_name: str = "review",
        workspace_root: Path | None = None,
    ) -> None:
        """
        Args:
            limits: {子代理名: 最大调用次数}；None 时回退旧双参构造。
            max_revisions: 旧构造——review 子代理最大被调用次数，默认 1。
            review_name: 旧构造——审查子代理的注册名称，默认 "review"。
        """
        if limits is None:
            limits = {review_name: max_revisions}
        self.limits: dict[str, int] = dict(limits)
        self.review_name = review_name
        self.max_revisions = self.limits.get(review_name, max_revisions)
        self._counts: dict[str, int] = {}
        self.workspace_root = workspace_root

    # ------------------------------------------------------------------
    # 调用周期重置（子代理每次被 task 委托开始时触发）
    # ------------------------------------------------------------------
    # 计数周期 = 「子代理每被父 agent task 委托一次」。
    # 子代理 graph 一次编译、会话内多次复用同一实例，必须靠 before_agent 在每次
    # graph 执行开始时清零计数，否则额度会跨调用累积（见需求基准计数边界决策）。

    @property
    def _revision_count(self) -> int:
        """向后兼容：主审查目标（review）的当前计数。"""
        return self._counts.get(self.review_name, 0)

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._counts = {}

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._counts = {}

    def _matched_target(self, request: Any) -> str | None:
        """返回 task 调用的目标名；目标未登记上限或非 task 调用时返回 None。"""
        tool_call = getattr(request, "tool_call", {})
        tool_name = _mapping_value(tool_call, "name")
        if tool_name != "task":
            return None
        args = _mapping_value(tool_call, "args")
        if not isinstance(args, dict):
            return None
        # task 工具的 args 中 subagent_type 指定目标子代理名称
        target = args.get("subagent_type") or args.get("name") or ""
        return target if target in self.limits else None

    def _limit_message(self, request: Any, target: str) -> ToolMessage:
        """构造达到审查上限的终止消息：停止审查 + 指示子代理在返回摘要中转述（解读A 可见性）。"""
        tool_call = getattr(request, "tool_call", {})
        tool_call_id = _mapping_value(tool_call, "id")
        return ToolMessage(
            content=(
                f"已达到本轮审查上限（{self.limits[target]} 次 / 本轮创作）。"
                "审查已完成，请接受当前版本，"
                f"不要再调用 {target}。"
                f"请在返回给父代理的摘要中明确注明：「本轮 {target} 审查已用完，已跳过额外 {target}」，"
                "再基于当前内容收尾返回。"
            ),
            name="task",
            tool_call_id=str(tool_call_id or ""),
        )

    # ------------------------------------------------------------------
    # 工具调用拦截（同步 / 异步）
    # ------------------------------------------------------------------

    def _completion_guard(self, request: Any, target: str | None) -> ToolMessage | None:
        if self.workspace_root is None or target != "review":
            return None
        from ..text_completion import review_block_reason

        state = getattr(request, "state", {})
        call = getattr(request, "tool_call", {})
        reason = review_block_reason(state, self.workspace_root, (call.get("args") or {}).get("description", ""))
        if reason:
            return ToolMessage(content=f"[审稿拒绝] {reason}", status="error", tool_call_id=call.get("id", ""))
        return None

    def _completed_review(self, request: Any, target: str | None, result: Any, *, failed: bool = False) -> Any:
        if self.workspace_root is None or target != "review":
            return result
        from ..text_completion import mark_review_result

        call = getattr(request, "tool_call", {})
        return mark_review_result(
            result, state=getattr(request, "state", {}), root=self.workspace_root,
            call_id=call.get("id", ""), description=(call.get("args") or {}).get("description", ""),
            failed=failed,
        )

    def _failed_review(self, request: Any, target: str | None, error: Exception) -> Any:
        if self.workspace_root is None or target != "review":
            raise error
        from ..text_completion import review_target

        call = getattr(request, "tool_call", {})
        if review_target(getattr(request, "state", {}), self.workspace_root, (call.get("args") or {}).get("description", "")) is None:
            raise error
        return self._completed_review(request, target, ToolMessage(
            content=f"审稿执行失败：{type(error).__name__}: {error}",
            status="error", tool_call_id=call.get("id", ""),
        ), failed=True)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        """拦截同步工具调用：按目标检查次数 → 放行或返回终止消息。"""
        target = self._matched_target(request)
        blocked = self._completion_guard(request, target)
        if blocked is not None:
            return blocked
        if target is not None:
            count = self._counts.get(target, 0) + 1
            self._counts[target] = count
            if count > self.limits[target]:
                return self._limit_message(request, target)
        try:
            result = handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            return self._failed_review(request, target, error)
        return self._completed_review(request, target, result)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        target = self._matched_target(request)
        blocked = self._completion_guard(request, target)
        if blocked is not None:
            return blocked
        if target is not None:
            count = self._counts.get(target, 0) + 1
            self._counts[target] = count
            if count > self.limits[target]:
                return self._limit_message(request, target)
        try:
            result = await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as error:
            return self._failed_review(request, target, error)
        return self._completed_review(request, target, result)


def _mapping_value(mapping: object, key: str) -> Any:
    """安全地从字典或对象中取值。"""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return getattr(mapping, key, None)


def build(abc):
    """架构清单挂载钩子：审查闭环硬上限。

    review 的上限沿用 agent 清单 max_revisions（自动正文按本批可写章数放大）；
    其他委托目标（如卷纲质检 volume_qc，REQ-20261031-142504）的上限取
    子代理清单自己的 max_revisions 声明（清单默认 1）。
    """
    from contracts.writing_artifacts import (
        MODE_AUTO, STAGE_TEXT, resolve_confirmed_stage, scan_writing_progress,
        stage_mode, text_batch_size,
    )

    review_cap = abc.agent.max_revisions
    stage = resolve_confirmed_stage(abc.workspace_path)
    if stage == STAGE_TEXT and stage_mode(abc.workspace_path, stage) == MODE_AUTO:
        progress = scan_writing_progress(abc.workspace_path)
        stock = set(progress.chapter_outlines) - set(progress.chapter_texts)
        # 自动正文一轮会连写多章；额度按本批实际可写章数计算，仍保留总上限。
        chapters = max(1, min(text_batch_size(abc.workspace_path), len(stock)))
        review_cap *= chapters
    limits = {"review": review_cap}
    delegates = getattr(abc.agent, "delegates", None) or []
    for name in delegates:
        if name == "review":
            continue
        manifest = getattr(abc, "manifest", None)
        sub = manifest.agent(name) if manifest is not None else None
        sub_cap = getattr(sub, "max_revisions", 0) if sub is not None else 0
        if sub_cap:
            limits[name] = sub_cap
    from ..text_completion import enabled_for

    return RevisionLimitMiddleware(limits=limits, workspace_root=abc.workspace_path if enabled_for(abc) else None)
