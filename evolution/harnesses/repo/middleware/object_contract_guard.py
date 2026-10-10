"""ObjectContractGuardMiddleware — 物品卡契约运行时护栏（REQ-20261004-221109 FR-004）。

双向拦截（contracts.object_contract 判定器唯一实现，两侧不得各自实现）：
  1. 写 ``/object/*.md`` 时：卡片三段式结构 / 类型·可见性·变化枚举 /
     文件名即唯一 ID / 重名查重 / 轨迹事件锚点在 storyline.md 存在
  2. 写 ``/storyline.md`` 时反查：本次修订删除（或改名）的事件
     未被任何现有卡片轨迹引用——堵死「修订删事件、卡片指向空气」入口

违规 → ToolMessage 硬拦截（business_intercept 模式，错误信息含具体差距）。

防死循环（沿用 StorylineContractGuard 口径）：
  同一规则连续拒绝 3 次后不再拦截该规则的违规（放行写入），并在下一轮
  模型调用前注入「强制收尾」指令；每次拦截记 executor 日志。

口径与既有护栏一致：
  - write_file：预估内容 = args.content。
  - edit_file：预估内容 ≈ 磁盘内容.replace(old, new, 1)；
    old_string 不在磁盘内容中 → 放行交 file_state_tracker 拦。
  - 护栏自身异常 → 降级放行 + 日志（护栏故障不得中断创作主流程）。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

from contracts.object_contract import check_object_write, find_dangling_refs

from .path_guard import normalize_workspace_write_path

logger = logging.getLogger(__name__)

_STORYLINE_FILE = "/storyline.md"
_OBJECT_DIR = "/object"

# 同一规则连续拒绝上限（第 3 次拒绝后，下一次同规则违规放行并强制收尾）
DEFAULT_MAX_REJECTS = 3


class ObjectContractGuardMiddleware(AgentMiddleware):
    """物品卡契约 / storyline 修订反查 运行时护栏。

    拦截 write_file / edit_file 到 ``/object/*.md`` 与 ``/storyline.md`` 的调用；
    违规写入被弹回并返回定向错误信息；同一规则连续 3 次被拒 → 放行 + 注入强制收尾指令。
    """

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

    # ── 强制收尾注入（复用 StorylineContractGuard 收尾注入语义）──

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not self._wrapup_gaps:
            return None
        gaps = "；".join(self._wrapup_gaps)
        self._wrapup_gaps.clear()
        return {"messages": [HumanMessage(content=(
            f"[护栏强制收尾] 以下物品卡校验已连续 {self.max_rejects} 次未通过，本次写入已放行：{gaps}。"
            "停止继续修正该问题，基于现有内容收尾：核对物品卡轨迹表与 storyline.md 事件表的一致性，"
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
            logger.exception("object 契约护栏内部异常，降级放行")
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

        raw_path = args.get("file_path")
        try:
            normalized = normalize_workspace_write_path(raw_path, self.workspace_path)
        except ValueError:
            return None  # 非法路径交 PathGuard

        if normalized == _STORYLINE_FILE:
            subject = "storyline"
            filename = "storyline.md"
            violations = self._check_storyline_revision(tool_name, args)
        elif normalized.startswith(_OBJECT_DIR + "/") and normalized.endswith(".md"):
            subject = "object"
            filename = normalized.rsplit("/", 1)[-1]
            violations = self._check_object_write(filename, tool_name, args)
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
                    "object 护栏放行（连续%d次被拒） rule=%s detail=%s",
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
                "object 护栏拦截 rule=%s seq=%d detail=%s",
                rule, self._reject_counts[rule],
                "；".join(m for m in blocked_msgs),
            )

        tool_call_id = (
            tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
        )
        detail = "；".join(blocked_msgs[:6]) + ("…" if len(blocked_msgs) > 6 else "")
        if subject == "object":
            head = f"物品卡 {filename} 写入校验未通过：{detail}。"
        else:
            head = f"storyline.md 修订与物品卡冲突：{detail}。"
        return ToolMessage(
            content=head + "完整物品卡规范（三段式/类型·可见性·变化枚举/轨迹锚点）见技能 "
            "storybuilding-specs，动笔前未读取请先读取再修正。"
            "请修正上述问题后重新写入；同一问题连续 3 次被拒后系统将强制收尾。",
            name=str(tool_name),
            tool_call_id=str(tool_call_id or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )

    # ── 两个方向的判定（预估内容口径与 storyline 护栏一致）──

    def _check_object_write(self, filename: str, tool_name: str, args: dict) -> list:
        physical = self.workspace_path / "object" / filename
        current = self._read_file(physical)

        if tool_name == "write_file":
            content = args.get("content")
            if not isinstance(content, str):
                return []
            projected = content
        else:
            old_string = args.get("old_string")
            new_string = args.get("new_string")
            if not isinstance(old_string, str) or not isinstance(new_string, str):
                return []
            if old_string not in current:
                return []  # 模拟替换失败：交 file_state_tracker 拦
                        # replace_all=True 且多处出现 → 全量替换（与工具落盘语义一致，防首处外绕过）
            if args.get("replace_all") and current.count(old_string) > 1:
                projected = current.replace(old_string, new_string)
            else:
                projected = current.replace(old_string, new_string, 1)

        existing = {
            p.name: self._read_file(p)
            for p in sorted((self.workspace_path / "object").glob("*.md"))
            if p.name != filename
        }
        storyline = self._read_file(self.workspace_path / "storyline.md")
        return check_object_write(filename, projected, existing, storyline)

    def _check_storyline_revision(self, tool_name: str, args: dict) -> list:
        current = self._read_file(self.workspace_path / "storyline.md")

        if tool_name == "write_file":
            content = args.get("content")
            if not isinstance(content, str):
                return []
            projected = content
        else:
            old_string = args.get("old_string")
            new_string = args.get("new_string")
            if not isinstance(old_string, str) or not isinstance(new_string, str):
                return []
            if old_string not in current:
                return []
                        # replace_all=True 且多处出现 → 全量替换（与工具落盘语义一致，防首处外绕过）
            if args.get("replace_all") and current.count(old_string) > 1:
                projected = current.replace(old_string, new_string)
            else:
                projected = current.replace(old_string, new_string, 1)

        all_cards = {
            p.name: self._read_file(p)
            for p in sorted((self.workspace_path / "object").glob("*.md"))
        }
        return find_dangling_refs(current, projected, all_cards)

    @staticmethod
    def _read_file(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="gb18030", errors="replace")


__all__ = ["DEFAULT_MAX_REJECTS", "ObjectContractGuardMiddleware"]


def build(abc):
    """架构清单挂载钩子：domain 护栏——物品卡契约校验。"""
    return ObjectContractGuardMiddleware(abc.workspace_path)
