"""SkillActivationGuardMiddleware — 流程技能误激活哨兵（一次性指路，不拦内容）。

trace-3d0d75f283524df9a4787ec871ae3512 显示：提案轮模型一口气读取全部三份
互斥流程技能（storybuilding-initial / storybuilding-expand-storyline /
storybuilding-expand-character），一次 LLM 调用带着三套互斥流程指令生成
方向提案——~8-10K 字冗余上下文、调用变慢变贵、互斥流程同池的混淆风险。

设计（与 ReceiptGateMiddleware「重复读一次性指路」同族范式，#1 进化）：
  - 三份流程技能互斥：每个增量单元只执行一份。本中间件对流程技能
    SKILL.md 的 read_file 做事件驱动回应——不预判分流判据（判据在对话
    上下文与工作区实况里，中间件不可见，预判必误伤），只观察事实序列。
  - 本轮（自上次产物写入以来）首份流程技能读取 → 放行并记名。
  - 再读另一份流程技能 → 返回内容前加一次性提醒头（内容照给，不拦截、
    不重写）：声明本轮已激活 X、三份互斥，换技能的合法时机是单元切换
    且分流判据已变化；此后本单元内不再提醒（防提示刷屏形成新节奏器）。
  - 检测到产物写入（write_file / edit_file 命中受保护产物路径）→ 视为
    进入新增量单元，记录清零重置——连写批次内合法换技能（R 值跨越 3、
    导航指针变向）不受误伤。
  - specs（storybuilding-specs）不在流程技能互斥集合内（内容规范手册，
    与流程技能正交），不计数、不打扰。
  - 任何内部异常原样放行（哨兵故障不得中断创作主流程，对齐
    receipt_gate / review_gate 的降级哲学）。

hook 签名（langchain 1.4.3，inspect_middleware_protocol 2026-10-08 核对）：
  wrap_tool_call(request, handler) / awrap_tool_call(request, handler)。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from .path_guard import normalize_workspace_write_path

logger = logging.getLogger(__name__)

# 三份互斥流程技能的 SKILL.md 路径片段（read_file 常见形态：技能文件
# 由 skills 加载器注入工作区虚拟路径，路径含技能目录名即可判定）
_PROCESS_SKILL_MARKERS: dict[str, str] = {
    "storybuilding-initial": "storybuilding-initial",
    "storybuilding-expand-storyline": "storybuilding-expand-storyline",
    "storybuilding-expand-character": "storybuilding-expand-character",
}
_SPEC_SKILL_MARKER = "storybuilding-specs"

# 受保护产物路径前缀（normalize 后口径，与 ReceiptGate 一致）——
# 命中即视为完成一个增量单元，激活记录重置
_UNIT_DONE_PREFIXES = ("/storyline.md", "/worldview.md", "/character/", "/object/")


def _mapping_value(mapping: object, key: str) -> Any:
    """安全地从字典或对象中取值（与其它中间件一致的取值方式）。"""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return getattr(mapping, key, None)


class SkillActivationGuardMiddleware(AgentMiddleware):
    """流程技能误激活哨兵：同单元第二份流程技能读取加一次性指路头。"""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        # 本单元已激活的流程技能名（None = 尚未激活）
        self._active_skill: str | None = None
        # 本单元提醒只发一次（防刷屏成新节奏器）
        self._reminded = False

    # ------------------------------------------------------------------
    # 内部判定
    # ------------------------------------------------------------------

    def _match_process_skill(self, path_text: str) -> str | None:
        """路径文本匹配流程技能目录名；specs 不在互斥集合，返回 None。"""
        if _SPEC_SKILL_MARKER in path_text:
            return None
        for name, marker in _PROCESS_SKILL_MARKERS.items():
            if marker in path_text:
                return name
        return None

    def _is_unit_done_write(self, request: Any) -> bool:
        """判定本次写入是否命中受保护产物（= 完成一个增量单元）。"""
        tool_call = getattr(request, "tool_call", {})
        name = _mapping_value(tool_call, "name")
        if name not in ("write_file", "edit_file"):
            return False
        args = _mapping_value(tool_call, "args")
        if not isinstance(args, dict):
            return False
        raw_path = args.get("file_path")
        if not isinstance(raw_path, str) or not raw_path:
            return False
        try:
            normalized = normalize_workspace_write_path(
                raw_path, self.workspace_root
            )
        except ValueError:
            return False
        return normalized.startswith(_UNIT_DONE_PREFIXES) or normalized in (
            "/storyline.md",
            "/worldview.md",
        )

    def _build_reminder(
        self, request: Any, result: Any, active: str, incoming: str
    ) -> Any:
        """构造一次性指路头（内容照给，不拦截读取）。"""
        tool_call = getattr(request, "tool_call", {})
        tool_call_id = str(_mapping_value(tool_call, "id") or "")
        if isinstance(result, ToolMessage):
            original = result.content if isinstance(result.content, str) else ""
            return ToolMessage(
                content=(
                    "[技能哨兵·互斥提醒] 本单元已激活流程技能 "
                    f"{active}，又读取了互斥技能 {incoming}。三份流程技能"
                    "（initial / expand-storyline / expand-character）互斥，"
                    "每个增量单元只执行一份——按已激活的 "
                    f"{active} 执行本单元；换技能的合法时机是单元切换"
                    "（上一单元产物已写入）且分流判据已变化（R 值跨越 3 "
                    "或导航指针变向）。以下是所读文件内容：\n\n" + original
                ),
                name=str(result.name or "read_file"),
                tool_call_id=str(result.tool_call_id or tool_call_id),
            )
        if isinstance(result, str):
            return (
                "[技能哨兵·互斥提醒] 本单元已激活流程技能 "
                f"{active}，又读取了互斥技能 {incoming}。三份流程技能"
                "（initial / expand-storyline / expand-character）互斥，"
                "每个增量单元只执行一份——按已激活的 "
                f"{active} 执行本单元；换技能的合法时机是单元切换"
                "（上一单元产物已写入）且分流判据已变化（R 值跨越 3 "
                "或导航指针变向）。以下是所读文件内容：\n\n" + result
            )
        return result

    def _after_read(self, request: Any, result: Any) -> Any:
        """read_file 后处理：首份记名放行；互斥第二份加一次性提醒头。"""
        try:
            tool_call = getattr(request, "tool_call", {})
            if str(_mapping_value(tool_call, "name") or "") != "read_file":
                return result
            args = _mapping_value(tool_call, "args")
            if not isinstance(args, dict):
                return result
            path_text = str(
                args.get("file_path") or args.get("path") or ""
            )
            incoming = self._match_process_skill(path_text)
            if incoming is None:
                return result  # 非流程技能（含 specs）不打扰

            if self._active_skill is None:
                self._active_skill = incoming  # 本单元首份，记名放行
                return result
            if self._active_skill == incoming:
                return result  # 重读同一份不打扰（缓存/ReadCache 层已有提醒）

            if self._reminded:
                return result  # 本单元只提醒一次，防刷屏
            self._reminded = True
            return self._build_reminder(
                request, result, self._active_skill, incoming
            )
        except Exception:  # noqa: BLE001 — 哨兵故障不得中断创作主流程
            logger.exception("SkillActivationGuard 判定异常，原样放行")
            return result

    def _reset_if_unit_done(self, request: Any) -> None:
        """受保护产物写入 = 完成一个增量单元，激活记录清零。"""
        try:
            if self._is_unit_done_write(request):
                self._active_skill = None
                self._reminded = False
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # hook（签名与基类一致：request, handler）
    # ------------------------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        self._reset_if_unit_done(request)
        result = handler(request)
        return self._after_read(request, result)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        self._reset_if_unit_done(request)
        result = await handler(request)
        return self._after_read(request, result)


def build(abc):
    """架构清单挂载钩子（M2）：domain 哨兵——流程技能误激活一次性指路。"""
    return SkillActivationGuardMiddleware(abc.workspace_path)


__all__ = ["SkillActivationGuardMiddleware"]
