"""ReceiptGateMiddleware — 方向提案硬闸（首运行禁写形态，一段式，零注入）。

交互模式（默认）的入口闸门：storyline.md 尚不存在的线程，第 1 次运行
拦截一切受保护产物写入（storyline.md / worldview.md / character/*.md），
强制先走方向提案（进化 #7，替代 #5 的两段式；#8 零注入重构）：
  agent 消化 demand.md、把设计做完，端出 2-3 套真实不同的方向方案
  （每套五件套：方向名/一句话定位/核心画面/关键锚点/方案间差异）经
  confirm_with_user 交用户拍板。一次有效回复即拍板（一段式）：选定 →
  直接动笔；调整/自提 → 消化后直接动笔；用户明示跳过（「不用问了直接写」
  等变体）→ 以推荐方案直接动笔。

设计（与 ReviewGateMiddleware 同族范式）：
  - 确认信号不解析消息内容——[配比导航]（QuotaConvergence）与
    [review 闸门]（ReviewGate）注入的消息本身是 HumanMessage，按内容判定
    「用户已回复」会被系统注入伪造；confirm_with_user 的挂起与返回值由
    执行端 HITL 通道驱动（resume 仅用户操作可触发），运行计数同样天然
    免疫（执行端仅在用户输入时发起下一次运行）。
  - 回执轮暂停走 interrupt 而非文本终局（2026-10-02 线上死锁修复）：
    ArtifactValidationMiddleware 要求 storyline.md 已产出才放行终局，
    回执轮恰好禁止写产物——文本停轮被拦回，模型在两个互锁闸门间反复
    挣扎（连续重复模型调用、输入框全程锁死）。confirm 工具把暂停变为
    真实图挂起，不经过 after_model 产物校验；挂起即正常收尾
    awaiting_input，前端解锁选项区。
  - wrap_tool_call 拦 write_file / edit_file 指向受保护路径 → 返回
    ToolMessage（status="error" + business_intercept 标记，对齐
    StorylineSingleLineLimit 的拦截消息规范：不触发写重试、不触发
    PlatformArtifactCapture 回读）。
  - 提案轮重复读一次性指路（#8）：第 2 次读取同一路径时在返回内容前加
    一次性提醒「唯一待办是调 confirm_with_user」，第 3 次起按文件放行
    ——治「零成本打转」（trace-be5d2ddd：模型重读 specs 8 遍，全程不调
    confirm 不写产物，读文件是唯一无后果动作）。不拦截读取、内容照给，
    正当核对不受阻。
  - 零注入纪律（#8）：删除 before_model 周期性指令注入——每次模型调用
    前注入同一条完整任务书，会把模型反复重置回「准备阶段」，恰是循环
    的拍器（8 轮注入 = 8 轮循环，精确对齐）。模型对提案轮的全部认知
    来自系统提示词常驻「方向提案协议」与技能步骤 0（触发判定 =
    storyline.md 不存在，模型自主探查）；闸门只做事件驱动回应
    （写入拦截 / 重复读指路），不做周期性催促。
  - storyline.md 已存在（续写线程）→ 闸门自动失效，零成本放行。
  - demand.md 元信息 receipt_skip: true（评估集预置流）→ 闸门跳过。
  - 拦截上限（默认 3 次）防死循环：模型连续无视拦截则放行（降级放行，
    人工 review 兜底），首次留日志——护栏故障不得中断创作主流程
    （对齐 review_gate / storyline_contract_guard 的降级哲学）。

hook 签名（langchain 1.4.3，inspect_middleware_protocol 2026-10-08 核对）：
  wrap_tool_call(request, handler) / awrap_tool_call(request, handler)。
  （#8 零注入后本中间件不再覆写 before_agent / before_model。）
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

# 受保护产物路径（normalize 后的虚拟路径口径，与 SingleLineLimit 一致）
_PROTECTED_FILES = ("/storyline.md", "/worldview.md")
_PROTECTED_DIR_PREFIX = "/character/"

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
    """方向提案硬闸：首轮未拍板时，拦截一切受保护产物写入。"""

    def __init__(
        self,
        workspace_root: Path,
        *,
        max_blocks: int = 3,
        storyline_relpath: str = "storyline.md",
    ) -> None:
        """
        Args:
            workspace_root:    工作区根目录（storyline.md 存在性判定用）
            max_blocks:        拦截上限（防死循环：模型连续无视则降级放行）
            storyline_relpath: 产物存在的判定文件（相对 workspace_root）
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.max_blocks = max_blocks
        self.storyline_path = self.workspace_root / storyline_relpath
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
    # 提案轮重复读一次性指路（#8：治零成本打转，不拦截读取）
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
                        "[提案闸门·重复读] 这是本轮第 2 次读取同一文件："
                        "你已经掌握其内容，重读不会带来新信息，也不可能满足"
                        "提案轮要求。当前唯一待办：消化 demand.md，端出 2-3 套"
                        "真实不同的方向方案（每套五件套），调用 confirm_with_user"
                        " 提交（不要用纯文本回复代替）。以下是所读文件内容：\n\n"
                        + original
                    ),
                    name=name,
                    tool_call_id=tool_call_id,
                )
            return result
        except Exception:  # noqa: BLE001 — 指路故障不得中断创作主流程
            return result

    def _receipt_round_active(self) -> bool:
        """提案轮判定：未跳过 + 未拍板 + storyline.md 尚未创建。"""
        return (
            not self.skip
            and not self._confirmed
            and not self.storyline_path.exists()
        )

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
            # 确认信号（任一即闸门满足）：
            #   1. confirm_with_user 工具正常返回（interrupt 已被用户 resume）
            #   2. 提案轮已结束（storyline.md 已产出）
            if self._confirmed or self.storyline_path.exists():
                return None
            # 跳过开关（评估集预置流）
            if self.skip:
                return None
            # 续写线程：storyline.md 已存在，闸门失效
            if self.storyline_path.exists():
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
        """受保护路径判定（normalize 后的虚拟路径口径）。"""
        if normalized_path in _PROTECTED_FILES:
            return True
        return normalized_path.startswith(_PROTECTED_DIR_PREFIX)

    def _block_message(self, tool_call: Any) -> ToolMessage:
        """构造拦截消息：引导走 confirm 工具（对齐业务拦截消息规范）。"""
        tool_call_id = _mapping_value(tool_call, "id")
        return ToolMessage(
            content=(
                "[提案闸门] 本线程为首轮运行且 storyline.md 尚未创建：方向提案未拍板，"
                "受保护产物（storyline.md / worldview.md / character/*.md）禁止写入。"
                "请先按初构技能（storybuilding-initial）步骤 0 与系统提示词「方向提案协议」执行："
                "消化 demand.md、把设计做完，端出 2-3 套真实不同的方向方案（每套五件套：方向名/"
                "一句话定位/核心画面/关键锚点/与其他方案的差异），调用 confirm_with_user 提交；"
                "一次有效回复即拍板（选定/调整/自提均可），拍板后方可写产物"
                "（用户明示「不用问了直接写」即跳过提案，以推荐方案直接动笔）。"
                "不要用纯文本回复代替工具调用——产物未写出时终局会被产物校验拦回。"
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
    """架构清单挂载钩子（M2）：domain 闸门——方向提案硬闸（首运行禁写形态）。"""
    return ReceiptGateMiddleware(abc.workspace_path)


__all__ = ["ReceiptGateMiddleware"]
