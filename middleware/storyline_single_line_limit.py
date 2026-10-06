"""StorylineSingleLineLimitMiddleware — 单次单线生成硬上限中间件（区块增量口径）。

职责：
  拦截 storybuilding 子代理对 ``/storyline.md`` 的 write_file / edit_file 调用，
  约束单次 storybuilding 子代理调用最多新增 ``max_new_lines`` 个线区块。
  超限则返回 ToolMessage 硬拦截，引导代理停止新增、基于现有内容收尾。

口径（REQ-20260930-002231 FR-007，单文件产物结构）：
  - 新增故事线 = storyline.md 中的线区块数（``## {线名} · {类型} · {状态}`` 区块头）
    净增加，不再看 storyline/S{XX}-*.md 文件创建。
  - write_file（整文件写入）：新内容区块头数 − 磁盘当前内容区块头数。
  - edit_file（局部替换）：new_string 区块头数 − old_string 区块头数
    （old_string 是磁盘文本的子串，其中的区块头被替换掉）。
  - 净增 ≤ 0（修订/删减）一律放行且不占额度；净增 > 0 才计数，超限拦截。
  - 计数周期 = 每次 storybuilding 子代理调用（before_agent 重置，v13 语义）；
    ``reset_per_invocation=False`` 保留为运行级绝对上限语义（跨委托累计）。

区块头计数复用 ``contracts.storybuilding_quota.count_line_block_headers``——
判定器唯一，护栏与配比导航对「什么是一个线区块」的认定不漂移。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from contracts.storybuilding_quota import count_line_block_headers
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from .path_guard import normalize_workspace_write_path

_STORYLINE_FILE = "/storyline.md"


class StorylineSingleLineLimitMiddleware(AgentMiddleware):
    """单次单线生成硬上限中间件（storyline.md 区块净增口径）。

  拦截 write_file / edit_file 到 ``/storyline.md`` 且线区块净增的调用，
  实例生命周期内累计计数，超过 ``max_new_lines`` 返回 ToolMessage 阻止写入。
  非 storyline.md 路径与区块数不增的写入一概放行。
    """

    def __init__(
        self, workspace_path: Path, *, max_new_lines: int = 1,
        reset_per_invocation: bool = True,
    ) -> None:
        """
        Args:
            workspace_path:  工作区根目录绝对路径，用于把虚拟路径映射到物理磁盘读旧内容。
            max_new_lines:   计数周期内最大新增线区块数，默认 1。
            reset_per_invocation: True（v13 单专家语义）= 计数周期为「每次 task
                委托」，before_agent 清零；False = 计数跨委托累计，
                max_new_lines 成为整个运行的新增绝对上限。
        """
        self.workspace_path = workspace_path.resolve()
        self.max_new_lines = max_new_lines
        self._reset_per_invocation = reset_per_invocation
        self._new_line_count = 0

    # ------------------------------------------------------------------
    # 调用周期重置（子代理每次被 task 调用开始时触发）
    # ------------------------------------------------------------------

    def before_agent(self, state: Any, runtime: Any) -> None:
        if self._reset_per_invocation:
            self._new_line_count = 0

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        if self._reset_per_invocation:
            self._new_line_count = 0

    # ------------------------------------------------------------------
    # 工具调用拦截（同步 / 异步）
    # ------------------------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return await handler(request)

    # ------------------------------------------------------------------
    # 核心判定
    # ------------------------------------------------------------------

    def _maybe_block(self, request: Any) -> ToolMessage | None:
        """判定本次写入是否构成「新增线区块」且超限。

        Returns:
            ``ToolMessage`` 表示超限拦截；``None`` 表示放行。

        步骤：
        1. 仅拦 write_file / edit_file（read 等一概放行）。
        2. 自行 normalize 路径；非法路径放行交 PathGuard；非 /storyline.md 放行。
        3. 估算线区块净增：write_file 按新内容 vs 磁盘旧内容；edit_file 按
           new_string vs old_string。净增 ≤ 0 放行不计数。
        4. 计数超限 → 拦截。
        """
        tool_call = getattr(request, "tool_call", {})
        tool_name = _mapping_value(tool_call, "name")
        if tool_name not in ("write_file", "edit_file"):
            return None

        args = _mapping_value(tool_call, "args")
        if not isinstance(args, dict):
            return None

        raw_path = args.get("file_path")
        try:
            normalized = normalize_workspace_write_path(raw_path, self.workspace_path)
        except ValueError:
            # 非法路径不归本中间件管，放行交 PathGuard 处理
            return None

        if normalized != _STORYLINE_FILE:
            return None

        delta = self._estimate_block_delta(tool_name, args)
        if delta <= 0:
            return None

        self._new_line_count += delta
        if self._new_line_count <= self.max_new_lines:
            return None

        return self._limit_message(tool_call)

    def _estimate_block_delta(self, tool_name: str, args: dict) -> int:
        """估算本次写入造成的线区块净增加量（保守取向：估算不出按 0 放行）。"""
        if tool_name == "write_file":
            content = args.get("content")
            if not isinstance(content, str):
                return 0
            physical = self.workspace_path / "storyline.md"
            old_text = (
                physical.read_text(encoding="utf-8") if physical.exists() else ""
            )
            return count_line_block_headers(content) - count_line_block_headers(old_text)

        # edit_file：old_string 是磁盘文本子串，其区块头被替换为 new_string 的
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return 0
        return count_line_block_headers(new_string) - count_line_block_headers(old_string)

    def _limit_message(self, tool_call: Any) -> ToolMessage:
        """构造达上限的拦截消息：停止新增 + 指示子代理在返回摘要中转述（解读A 可见性）。

        - status="error"：拦截 = write_file 未执行、未落盘任何文件。下游
          ``PlatformArtifactCaptureMiddleware`` 据 status 判定写入成功会回读不存在的
          文件，strict 路径(A/B)抛 EvidenceCaptureError 导致 task 崩溃重启
          （上下文暴跌根因）。标 error 让其跳过。
        - response_metadata["business_intercept"]=True：区分「业务约束拦截」与
          「基础设施写失败」。``WriteResultInspectorMiddleware`` 见此标记跳过
          （不转抛 WriteFailedError 触发重试）——业务拦截是正常流程，不该重试。
        """
        tool_call_id = _mapping_value(tool_call, "id")
        if self._reset_per_invocation:
            limit_text = (
                f"已达单次单线生成上限（{self.max_new_lines} 条 / 本轮 storybuilding）。"
                "请停止在 storyline.md 中新增故事线区块（可继续修订既有内容）。"
                "请在返回给父代理的摘要中明确注明：「本轮因达到单线生成上限，已跳过后续新增」，"
                "再基于当前已有内容收尾返回。"
            )
        else:
            limit_text = (
                f"已达本次运行新增故事线上限（{self.max_new_lines} 条，跨全部委托累计）。"
                "请停止在 storyline.md 中新增故事线区块（可继续修订既有内容）。"
                "请在返回摘要中明确注明：「故事线新增额度已用尽，后续委托无法再新增」，"
                "再基于当前已有内容收尾返回。"
            )
        return ToolMessage(
            content=limit_text,
            name="write_file",
            tool_call_id=str(tool_call_id or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )


def _mapping_value(mapping: object, key: str) -> Any:
    """安全地从字典或对象中取值（与其它中间件一致的取值方式）。"""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return getattr(mapping, key, None)


# 连续增量负载（REQ-20260922-162823 FR-002，DEC-011 同一判定器）：
#   - 有配比（full/semi）：单线护栏预算 = 目标线总数 + 余量（防中途废稿重写卡护栏）
#   - 无配比（minimal / 生产缺字段，DEC-013 软终止）：固定宽松上限防失控
_MINIMAL_LINE_BUDGET = 8
_LINE_BUDGET_MARGIN = 2


def resolve_line_budget(target) -> int:
    """按 demand 目标配比计算本次运行的新增故事线预算（护栏 max_new_lines）。"""
    if target is None:
        return _MINIMAL_LINE_BUDGET
    return target.total() + _LINE_BUDGET_MARGIN


def _load_quota_target(abc):
    from contracts.storybuilding_quota import parse_demand_quota

    demand_path = abc.workspace_path / "demand.md"
    demand_md = demand_path.read_text(encoding="utf-8") if demand_path.exists() else ""
    return parse_demand_quota(demand_md)


def build(abc):
    """架构清单挂载钩子：domain 护栏——单线新增预算按 demand 配比动态放宽。"""
    return StorylineSingleLineLimitMiddleware(
        abc.workspace_path,
        max_new_lines=resolve_line_budget(_load_quota_target(abc)),
    )
