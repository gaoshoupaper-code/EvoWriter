"""HooksContractGuardMiddleware — 钩子登记契约运行时护栏（REQ-20261010-000638 FR-004 合并版）。

双向拦截（contracts.hooks_contract 判定器唯一实现，两侧不得各自实现）：
  1. 写 ``/hooks.md`` 时：九列结构 / H{n} 编号·层级·类型·状态枚举 /
     已放弃必填备注 / 已收必有兑现事件 / 埋设事件必填 / 事件名锚点存在
  2. 写 ``/storyline.md`` 时反查：本次修订删除（或改名）的事件
     未被钩子登记引用——堵死「修订删事件、登记指向空气」入口

违规 → ToolMessage 硬拦截（business_intercept 模式，错误信息含具体差距）。

口径与既有护栏一致（ObjectContractGuard 同款）：
  - write_file：预估内容 = args.content。
  - edit_file：预估内容 ≈ 磁盘内容.replace(old, new, 1)；
    old_string 不在磁盘内容中 → 放行交 file_state_tracker 拦。
  - 防死循环：同一规则连续拒绝 3 次后放行 + 注入强制收尾。
  - 护栏自身异常 → 降级放行 + 日志（护栏故障不得中断创作主流程）。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

from contracts.hooks_contract import check_hooks_write, find_dangling_hook_refs


logger = logging.getLogger(__name__)

_STORYLINE_FILE = "/storyline.md"
_HOOKS_FILE = "/hooks.md"

# 同一规则连续拒绝上限（第 3 次拒绝后，下一次同规则违规放行并强制收尾）
DEFAULT_MAX_REJECTS = 3


class HooksContractGuardMiddleware(AgentMiddleware):
    """许诺台账契约 / storyline 修订反查 运行时护栏。"""

    def __init__(self, workspace_path: Path, *, max_rejects: int = DEFAULT_MAX_REJECTS) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.max_rejects = max_rejects
        self._reject_counts: dict[str, int] = {}
        self._wrapup_gaps: list[str] = []

    # ── 运行周期重置（每次 graph 执行开始，对齐既有护栏口径）──

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._reject_counts.clear()
        self._wrapup_gaps.clear()

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._reject_counts.clear()
        self._wrapup_gaps.clear()

    # ── 强制收尾注入（复用既有护栏收尾注入语义）──

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not self._wrapup_gaps:
            return None
        gaps = "；".join(self._wrapup_gaps)
        self._wrapup_gaps.clear()
        return {"messages": [HumanMessage(content=(
            f"[护栏强制收尾] 以下钩子登记校验已连续 {self.max_rejects} 次未通过，本次写入已放行：{gaps}。"
            "停止继续修正该问题，基于现有内容收尾：核对 hooks.md 与 storyline.md 事件表的一致性，"
            "按流程调用 review 审查一次、按需修订一次，然后返回，并在返回中如实说明上述未达标项。"
        ))]}

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

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
        """契约校验；护栏自身异常降级放行。"""
        try:
            return self._check(request)
        except Exception:  # noqa: BLE001 — 护栏故障不得中断主流程
            logger.exception("hooks 契约护栏内部异常，降级放行")
            return None

    def _check(self, request: Any) -> ToolMessage | None:
        tool_call = getattr(request, "tool_call", {})
        tool_name = (
            tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
        )
        if tool_name not in ("write_file", "edit_file"):
            return None

        args = tool_call.get("args") if isinstance(tool_call, dict) else getattr(tool_call, "args", None)
        if not isinstance(args, dict):
            return None

        # 只匹配两个精确虚拟路径；不做白名单归一（/hooks.md 不在静态白名单，
        # 由架构清单 write_permissions 动态授权——此处仅做等值判断，非法路径交 PathGuard）
        raw_path = str(args.get("file_path") or "").strip().replace("\\", "/")
        if raw_path == _HOOKS_FILE:
            subject = "hooks"
            violations = self._check_hooks_write(tool_name, args)
        elif raw_path == _STORYLINE_FILE:
            subject = "storyline"
            violations = self._check_storyline_revision(tool_name, args)
        else:
            return None

        if not violations:
            self._reject_counts.clear()
            return None

        # 防死循环：已达连续拒绝上限的规则 → 放行其违规并排队强制收尾
        blocked_msgs: list[str] = []
        blocked_rules: set[str] = set()
        for v in violations:
            if self._reject_counts.get(v.rule, 0) >= self.max_rejects:
                self._wrapup_gaps.append(v.message)
                logger.warning(
                    "hooks 护栏放行（连续%d次被拒） rule=%s detail=%s",
                    self.max_rejects, v.rule, v.message,
                )
            else:
                blocked_msgs.append(v.message)
                blocked_rules.add(v.rule)

        if not blocked_msgs:
            return None  # 全部违规规则均到上限：放行本次写入，收尾指令待注入

        for rule in sorted(blocked_rules):
            self._reject_counts[rule] = self._reject_counts.get(rule, 0) + 1
            logger.warning(
                "hooks 护栏拦截 rule=%s seq=%d detail=%s",
                rule, self._reject_counts[rule],
                "；".join(m for m in blocked_msgs),
            )

        tool_call_id = (
            tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
        )
        detail = "；".join(blocked_msgs[:6]) + ("…" if len(blocked_msgs) > 6 else "")
        if subject == "hooks":
            head = "hooks.md 写入校验未通过："
        else:
            head = "storyline.md 修订与许诺台账冲突："
        return ToolMessage(
            content=head + detail + "。请修正上述问题后重新写入；同一问题连续 3 次被拒后系统将强制收尾。",
            name=str(tool_name),
            tool_call_id=str(tool_call_id or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )

    # ── 两个方向的判定（预估内容口径与既有护栏一致）──

    def _projected(self, tool_name: str, args: dict, current: str) -> str | None:
        if tool_name == "write_file":
            content = args.get("content")
            return content if isinstance(content, str) else None
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        if old_string not in current:
            return None  # 模拟替换失败：交 file_state_tracker 拦
        # replace_all=True 且多处出现 → 全量替换（与工具实际落盘语义一致；
        # 只按首处投影会让其余出现处的替换绕过校验）
        if args.get("replace_all") and current.count(old_string) > 1:
            return current.replace(old_string, new_string)
        return current.replace(old_string, new_string, 1)

    def _check_hooks_write(self, tool_name: str, args: dict) -> list:
        current = self._read("hooks.md")
        projected = self._projected(tool_name, args, current)
        if projected is None:
            return []
        return check_hooks_write(projected, self._read("storyline.md"))

    def _check_storyline_revision(self, tool_name: str, args: dict) -> list:
        current = self._read("storyline.md")
        projected = self._projected(tool_name, args, current)
        if projected is None:
            return []
        return find_dangling_hook_refs(current, projected, self._read("hooks.md"))

    def _read(self, filename: str) -> str:
        physical = self.workspace_path / filename
        if not physical.exists():
            return ""
        try:
            return physical.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return physical.read_text(encoding="gb18030", errors="replace")


__all__ = ["DEFAULT_MAX_REJECTS", "HooksContractGuardMiddleware"]


def build(abc):
    """架构清单挂载钩子：domain 护栏——钩子登记契约校验。"""
    return HooksContractGuardMiddleware(abc.workspace_path)
