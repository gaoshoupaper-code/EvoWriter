"""OutlineContractGuardMiddleware — outline 产物结构契约运行时护栏（REQ-20261011-142529 FR-001/006）。

contracts.outline_ledger_contract 判定器唯一消费方（两侧不得各自实现）：
写 outline/ 设计端产物（00-03 必备章节/金手指代价）、结构端产物（04-06
必备章节、期待台账八列、章纲批五列表/前情接点/批内章号连续）时校验
结构语法（DEC-007：结构硬拦在 contracts；爽点密度等语义指标归体检观测）。

口径与既有护栏一致（hooks_contract_guard 同款）：
  - write_file：预估内容 = args.content。
  - edit_file：预估内容 ≈ 磁盘内容.replace(old, new)；replace_all 全量投影。
  - 防死循环：同一文件连续拒绝 3 次后放行 + 注入强制收尾。
  - 护栏自身异常 → 降级放行 + 日志。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

from contracts.outline_ledger_contract import validate_outline_file

logger = logging.getLogger(__name__)

DEFAULT_MAX_REJECTS = 3


class OutlineContractGuardMiddleware(AgentMiddleware):
    """outline 产物结构契约运行时护栏（双泳道通用）。"""

    def __init__(self, workspace_path: Path, *, max_rejects: int = DEFAULT_MAX_REJECTS) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.max_rejects = max_rejects
        self._reject_counts: dict[str, int] = {}
        self._wrapup_gaps: list[str] = []

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._reject_counts.clear()
        self._wrapup_gaps.clear()

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._reject_counts.clear()
        self._wrapup_gaps.clear()

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not self._wrapup_gaps:
            return None
        gaps = "；".join(self._wrapup_gaps)
        self._wrapup_gaps.clear()
        return {"messages": [HumanMessage(content=(
            f"[护栏强制收尾] 以下 outline 结构校验已连续 {self.max_rejects} 次未通过，本次写入已放行：{gaps}。"
            "停止继续修正该问题，基于现有内容收尾：核对产物与模板的结构差距，"
            "然后在返回中如实说明上述未达标项，交下一轮拍板处理。"
        ))]}

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return await handler(request)

    def _maybe_block(self, request: Any) -> ToolMessage | None:
        try:
            return self._check(request)
        except Exception:  # noqa: BLE001 — 护栏故障不得中断主流程
            logger.exception("outline 契约护栏内部异常，降级放行")
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

        raw_path = str(args.get("file_path") or "").strip().replace("\\", "/")
        rel = raw_path.lstrip("/")
        # 非相对 workspace 的写法（绝对路径等）交 PathGuard / 白名单处理
        if raw_path.startswith("/") and len(raw_path.split("/")) > 2:
            rel = raw_path.lstrip("/")

        current = self._read(rel)
        projected = self._projected(tool_name, args, current)
        if projected is None:
            return None

        violations = validate_outline_file(rel, projected)
        if not violations:
            self._reject_counts.pop(rel, None)
            return None

        if self._reject_counts.get(rel, 0) >= self.max_rejects:
            self._wrapup_gaps.append("；".join(violations))
            logger.warning(
                "outline 护栏放行（连续%d次被拒） file=%s detail=%s",
                self.max_rejects, rel, violations,
            )
            return None
        self._reject_counts[rel] = self._reject_counts.get(rel, 0) + 1
        logger.warning("outline 护栏拦截 file=%s seq=%d detail=%s",
                       rel, self._reject_counts[rel], violations)

        tool_call_id = (
            tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
        )
        return ToolMessage(
            content=(
                f"[outline 结构契约] {rel} 写入校验未通过：{'；'.join(violations[:6])}"
                "。按模板补齐结构后重新写入（模板见主文档对应步骤）；"
                "同一文件连续 3 次被拒后系统将强制收尾。"
            ),
            name=str(tool_name),
            tool_call_id=str(tool_call_id or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )

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
        if args.get("replace_all") and current.count(old_string) > 1:
            return current.replace(old_string, new_string)
        return current.replace(old_string, new_string, 1)

    def _read(self, rel: str) -> str:
        physical = self.workspace_path / rel
        if not physical.exists():
            return ""
        try:
            return physical.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return physical.read_text(encoding="gb18030", errors="replace")


__all__ = ["DEFAULT_MAX_REJECTS", "OutlineContractGuardMiddleware"]


def build(abc):
    """架构清单挂载钩子：domain 护栏——outline 结构契约校验（双泳道通用）。"""
    return OutlineContractGuardMiddleware(abc.workspace_path)
