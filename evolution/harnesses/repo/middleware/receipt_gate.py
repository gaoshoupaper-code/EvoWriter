"""ReceiptGateMiddleware — 产出单元拍板硬闸（每运行禁写形态，事件驱动回应）。

双 agent 管线（REQ-20261011-142529 FR-003，DEC-010）的单元闸门：**每轮运行**
在 confirm_with_user 一次有效回复（用户拍板）之前，拦截本泳道受保护产物的
写入——单元流程 = 起草 → confirm 拍板 → 批准后落盘，目录里只有已确认内容
（目录即进度）。受保护面 = 本泳道 architecture.json 的 write_permissions
（storydesign：00-03/character/core/worldview core；storystructure：
04-06/台账/character/support/worldview setting/object）。

设计要点（沿用进化 #8/#10 的教训）：
  - 确认信号不解析消息内容——confirm_with_user 的挂起与返回值由执行端
    HITL 通道驱动（resume 仅用户操作可触发），运行计数天然免疫。
  - 拍板前重复读同一路径 → 一次性指路（治零成本打转），第 3 次起放行。
  - 零周期注入：不做 before_model 催促，闸门只做事件驱动回应。
  - before_agent 每轮重置（用户的新消息 = 新单元 = 重新拍板）。
  - demand.md 元信息 receipt_skip: true（评估集预置流）→ 闸门跳过。
  - 拦截上限（默认 3 次）防死循环：连续无视则降级放行 + 首次日志。

hook 签名（langchain 1.4.3）：before_agent / wrap_tool_call / awrap_tool_call。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import wcmatch.glob as wcglob
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from .path_guard import normalize_workspace_write_path

logger = logging.getLogger(__name__)

_WCMATCH_FLAGS = wcglob.BRACE | wcglob.GLOBSTAR

# 用户确认工具名（confirm_with_user 正常返回 = 用户已回复，闸门释放）
_CONFIRM_TOOL_NAME = "confirm_with_user"

# 提案拍板有效回复次数（一段式：一次有效回复即释放写入）
_REQUIRED_CONFIRMS = 1

# 提案轮重复读指路阈值：同一路径第 2 次读取时加一次性提醒（此后放行）
_REPEAT_READ_REMIND_AT = 2

# 用户明示跳过澄清的短语（子串匹配，命中即放行；防交互过载的逃生门）
_SKIP_PHRASES = (
    "不用问了",
    "不用确认",
    "直接写",
    "直接开写",
    "直接开始",
    "别问了",
    "跳过澄清",
    "跳过确认",
)


class ReceiptGateMiddleware(AgentMiddleware):
    """任务拍板硬闸：每轮运行未拍板时，拦截一切受保护产物写入。"""

    def __init__(
        self,
        workspace_root: Path,
        protected_globs: list[str] | None = None,
        *,
        max_blocks: int = 3,
    ) -> None:
        """
        Args:
            workspace_root:    工作区根目录
            protected_globs:   受保护产物 glob（相对 workspace 根，wcmatch
                               GLOBSTAR 语义；缺省沿用旧五件套口径，兼容
                               旧包与测试）
            max_blocks:        拦截上限（防死循环：模型连续无视则降级放行）
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.max_blocks = max_blocks
        # 受保护面 = 本泳道写入白名单（拍板前禁写；缺省回退旧五件套）
        self.protected_globs = list(protected_globs) if protected_globs else [
            "/storyline.md", "/worldview.md", "/character/*.md", "/object/*.md", "/hooks.md",
        ]
        self._blocks = 0
        self._exhausted_logged = False
        # confirm_with_user 工具正常返回（interrupt 已被用户 resume）= 已拍板。
        # 计数制：一次有效回复（_REQUIRED_CONFIRMS=1，一段式），返回文本命中
        # 跳过短语则立即置满（用户明示跳过提案，防交互过载）。
        self._confirmed = False
        self._confirm_returns = 0
        # demand.md 元信息 receipt_skip: true → 评估集预置流跳过回执轮
        self.skip = self._parse_receipt_skip()
        # 提案轮重复读计数（路径字符串 → 次数；每路径至多提醒一次）
        self._read_counts: dict[str, int] = {}
        # 重复读提醒只发一次（全局一次性，避免提示刷屏形成新节奏器）
        self._read_reminders_exhausted = False

    # ------------------------------------------------------------------
    # 运行周期重置（每轮运行开始：新任务 = 新拍板周期，进化 #10）
    # ------------------------------------------------------------------

    def before_agent(self, state: Any, runtime: Any) -> None:
        """每轮运行开始重置状态：用户的新消息 = 新任务 = 重新拍板。

        resume 续跑不触发本钩子（恢复执行从工具节点进入，不经 agent 入口），
        拍板确认在 confirm 工具返回时记录，不受重置影响。
        """
        self._confirmed = False
        self._confirm_returns = 0
        self._blocks = 0
        self._exhausted_logged = False
        self._read_counts = {}
        self._read_reminders_exhausted = False

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._confirmed = False
        self._confirm_returns = 0
        self._blocks = 0
        self._exhausted_logged = False
        self._read_counts = {}
        self._read_reminders_exhausted = False

    # ------------------------------------------------------------------
    # 拍板前重复读一次性指路（#8：治零成本打转，不拦截读取）
    # ------------------------------------------------------------------

    def _maybe_prefix_repeat_reminder(self, request: Any, result: Any) -> Any:
        """提案轮内第 2 次读取同一路径 → 返回内容前加一次性提醒头。

        内容照给（不拦截读取、不打断正当核对）；此后同路径读取不再加头。
        任何内部异常原样返回结果（指路故障不得中断创作主流程）。
        """
        try:
            if not self._receipt_round_active() or self._confirmed:
                return result
            if self._read_reminders_exhausted:
                return result

            tool_call = getattr(request, "tool_call", {})
            tool_name = _mapping_value(tool_call, "name")
            if str(tool_name or "") != "read_file":
                return result

            args = _mapping_value(tool_call, "args")
            if not isinstance(args, dict):
                return result
            path_key = str(args.get("file_path") or args.get("path") or "")

            count = self._read_counts.get(path_key, 0) + 1
            self._read_counts[path_key] = count
            if count < _REPEAT_READ_REMIND_AT:
                return result
            if count > _REPEAT_READ_REMIND_AT:  # 第 3 次起按文件放行（防误伤）
                return result

            self._read_reminders_exhausted = True
            if isinstance(result, ToolMessage):
                original = result.content if isinstance(result.content, str) else ""
                name = str(result.name or "read_file")
                tool_call_id = str(result.tool_call_id or "")
                return ToolMessage(
                    content=(
                        "[拍板闸门·重复读] 这是本轮第 2 次读取同一文件："
                        "你已经掌握其内容，重读不会带来新信息。当前唯一待办："
                        "消化需求与现有产物，完整起草本单元产物，调用 "
                        "confirm_with_user 提交拍板（不要用纯文本回复代替）。"
                        "以下是所读文件内容：\n\n"
                        + original
                    ),
                    name=name,
                    tool_call_id=tool_call_id,
                )
            return result
        except Exception:  # noqa: BLE001 — 指路故障不得中断创作主流程
            return result

    def _receipt_round_active(self) -> bool:
        """拍板待决判定：未跳过 + 本轮未拍板（进化 #10：每轮口径）。"""
        return not self.skip and not self._confirmed

    # ------------------------------------------------------------------
    # 产物写入拦截（同步 / 异步）
    # ------------------------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if self._is_confirm_call(request):
            result = handler(request)
            # 正常返回 = interrupt 已被用户 resume（GraphInterrupt 异常路径
            # 不会走到这里），确认为真实用户信号
            self._record_confirm_return(result)
            return result
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        result = handler(request)
        return self._maybe_prefix_repeat_reminder(request, result)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if self._is_confirm_call(request):
            result = await handler(request)
            self._record_confirm_return(result)
            return result
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        result = await handler(request)
        return self._maybe_prefix_repeat_reminder(request, result)

    def _record_confirm_return(self, result: Any) -> None:
        """记录一次有效拍板返回；达到阈值或命中跳过短语即置已拍板。"""
        self._confirm_returns += 1
        text = result if isinstance(result, str) else ""
        if any(p in text for p in _SKIP_PHRASES):
            self._confirm_returns = _REQUIRED_CONFIRMS
        self._confirmed = self._confirm_returns >= _REQUIRED_CONFIRMS

    def _is_confirm_call(self, request: Any) -> bool:
        """判定是否为 confirm_with_user 工具调用（与 review 计数同款判定式）。"""
        tool_call = getattr(request, "tool_call", {})
        return _mapping_value(tool_call, "name") == _CONFIRM_TOOL_NAME

    # ------------------------------------------------------------------
    # 核心判定
    # ------------------------------------------------------------------

    def _maybe_block(self, request: Any) -> ToolMessage | None:
        """判定本次写入是否应被回执闸门拦截。

        Returns:
            ToolMessage 表示拦截；None 表示放行。任何内部异常降级放行。
        """
        try:
            # 确认信号：confirm_with_user 工具正常返回（interrupt 已被
            # 用户 resume）= 本轮已拍板
            if self._confirmed:
                return None
            # 跳过开关（评估集预置流）
            if self.skip:
                return None

            tool_call = getattr(request, "tool_call", {})
            tool_name = _mapping_value(tool_call, "name")
            if tool_name not in ("write_file", "edit_file"):
                return None

            args = _mapping_value(tool_call, "args")
            if not isinstance(args, dict):
                return None

            raw_path = args.get("file_path")
            if not isinstance(raw_path, str) or not raw_path:
                return None

            try:
                normalized = normalize_workspace_write_path(
                    raw_path, self.workspace_root
                )
            except ValueError:
                # 非法路径不归本闸门管，放行交 PathGuard 处理
                return None

            if not self._is_protected(normalized):
                return None

            # 拦截上限（防死循环：模型连续无视则降级放行 + 首次日志）
            if self._blocks >= self.max_blocks:
                if not self._exhausted_logged:
                    self._exhausted_logged = True
                    logger.warning(
                        "[回执闸门] 拦截已达上限（%d 次），模型仍尝试写产物，"
                        "降级放行（workspace=%s）",
                        self.max_blocks,
                        self.workspace_root,
                    )
                return None

            self._blocks += 1
            return self._block_message(tool_call)
        except Exception:  # noqa: BLE001 — 闸门故障不得中断创作主流程
            logger.exception("ReceiptGate 判定异常，降级放行")
            return None

    def _is_protected(self, normalized_path: str) -> bool:
        """受保护路径判定：白名单 glob 命中即受保护（normalize 后的虚拟路径口径）。"""
        return any(
            wcglob.globmatch(normalized_path, pattern, flags=_WCMATCH_FLAGS)
            for pattern in self.protected_globs
        )

    def _block_message(self, tool_call: Any) -> ToolMessage:
        """构造拦截消息：引导走 confirm 工具（对齐业务拦截消息规范）。"""
        tool_call_id = _mapping_value(tool_call, "id")
        return ToolMessage(
            content=(
                "[单元拍板闸门] 本轮产出单元尚未拍板：本泳道产物禁止写入。"
                "按主文档单元流程执行——先在对话里完整起草本单元产物全文，"
                "经 confirm_with_user 提交拍板（question 放草稿全文 + 检验"
                "结果），一次有效回复即拍板，批准后再按批准稿落盘"
                "（用户明示「不用问了直接写」即跳过拍板，以推荐稿落盘）。"
                "不要用纯文本回复代替工具调用。"
            ),
            name=str(_mapping_value(tool_call, "name") or "write_file"),
            tool_call_id=str(tool_call_id or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )

    def _parse_receipt_skip(self) -> bool:
        """解析 demand.md 元信息 receipt_skip: true（评估集预置流跳过回执）。"""
        demand = self.workspace_root / "demand.md"
        if not demand.exists():
            return False
        try:
            text = demand.read_text(encoding="utf-8")
        except OSError:
            return False
        for line in text.splitlines()[:30]:  # 元信息在文件头 HTML 注释内
            stripped = line.strip().lstrip("-").strip()
            if stripped.lower().startswith("receipt_skip"):
                return "true" in stripped.lower()
        return False


def _mapping_value(mapping: object, key: str) -> Any:
    """安全地从字典或对象中取值（与其它中间件一致的取值方式）。"""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return getattr(mapping, key, None)


def build(abc):
    """架构清单挂载钩子（M2）：domain 闸门——单元拍板硬闸（保护面=本泳道白名单）。"""
    return ReceiptGateMiddleware(
        abc.workspace_path,
        protected_globs=list(getattr(abc.agent, "write_permissions", []) or []),
    )


__all__ = ["ReceiptGateMiddleware"]
