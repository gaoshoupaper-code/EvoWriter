"""ReceiptGateMiddleware — 理解回执硬闸（首运行禁写形态，进化 #5）。

交互模式（默认）的入口闸门：storyline.md 尚不存在的线程，第 1 次运行
拦截一切受保护产物写入（storyline.md / worldview.md / character/*.md），
强制先交「理解回执」（需求复述 + 假设清单 + 故事核心五字段草案，进化 #2）；
用户回复确认/修改 = 第 2 次运行的触发器，运行计数即确认信号。

设计（与 ReviewGateMiddleware 同族范式）：
  - 确认信号不解析消息内容——[配比导航]（QuotaConvergence）与
    [review 闸门]（ReviewGate）注入的消息本身是 HumanMessage，按内容判定
    「用户已回复」会被系统注入伪造；运行计数天然免疫（执行端仅在用户输入
    时发起下一次运行）。
  - wrap_tool_call 拦 write_file / edit_file 指向受保护路径 → 返回
    ToolMessage（status="error" + business_intercept 标记，对齐
    StorylineSingleLineLimit 的拦截消息规范：不触发写重试、不触发
    PlatformArtifactCapture 回读）。
  - storyline.md 已存在（续写线程）→ 闸门自动失效，零成本放行。
  - demand.md 元信息 receipt_skip: true（评估集预置流）→ 闸门跳过。
  - 拦截上限（默认 3 次）防死循环：模型连续无视拦截则放行（降级放行，
    人工 review 兜底），首次留日志——护栏故障不得中断创作主流程
    （对齐 review_gate / storyline_contract_guard 的降级哲学）。

hook 签名（langchain 1.4.3，inspect_middleware_protocol 2026-10-02 核对）：
  before_agent/abefore_agent(state, runtime)；wrap_tool_call(request, handler) /
  awrap_tool_call(request, handler)。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

from .path_guard import normalize_workspace_write_path

logger = logging.getLogger(__name__)

# 受保护产物路径（normalize 后的虚拟路径口径，与 SingleLineLimit 一致）
_PROTECTED_FILES = ("/storyline.md", "/worldview.md")
_PROTECTED_DIR_PREFIX = "/character/"


class ReceiptGateMiddleware(AgentMiddleware):
    """理解回执硬闸：首轮未确认回执时，拦截一切受保护产物写入。"""

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
        self._run_count = 0
        self._blocks = 0
        self._exhausted_logged = False
        # 回执轮指令注入计数（上限防刷屏：模型长跑不结束时停止注入）
        self._directives = 0
        self._max_directives = 8
        # demand.md 元信息 receipt_skip: true → 评估集预置流跳过回执轮
        self.skip = self._parse_receipt_skip()

    # ------------------------------------------------------------------
    # 运行计数（用户回复 = 下一次运行；第 2 次运行起视为回执已确认）
    # ------------------------------------------------------------------

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._run_count += 1

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._run_count += 1

    # ------------------------------------------------------------------
    # 回执轮指令注入（首次运行：不写产物，只交理解回执）
    # ------------------------------------------------------------------

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._inject_receipt_directive()

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._inject_receipt_directive()

    def _receipt_round_active(self) -> bool:
        """回执轮判定：首次运行 + 未跳过 + storyline.md 尚未创建。"""
        return (
            self._run_count == 1
            and not self.skip
            and not self.storyline_path.exists()
        )

    def _inject_receipt_directive(self) -> dict[str, Any] | None:
        """回执轮导航指令：压住配比导航的「继续增量」，引导输出理解回执。"""
        try:
            if not self._receipt_round_active():
                return None
            if self._directives >= self._max_directives:
                return None
            self._directives += 1
            return {"messages": [HumanMessage(content=(
                "[交互模式·回执轮] 本轮为首次运行：忽略上面的增量推进导航——"
                "本轮不写任何产物（写入会被硬拦截）。读取 demand.md 后输出理解回执："
                "需求复述 + 假设清单 + 故事核心五字段草案（Logline / 核心主题 / "
                "类型基调 / 节奏曲线 / 最终结局——结局确认后系统级钉死不可改，"
                "回执轮是唯一能修改它的地方），请用户确认或修改草案后返回。"
                "用户回复后的下一次运行才能动笔。"
            ))]}
        except Exception:  # noqa: BLE001 — 指令注入故障不得中断创作主流程
            logger.exception("ReceiptGate 指令注入异常，跳过")
            return None

    # ------------------------------------------------------------------
    # 产物写入拦截（同步 / 异步）
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 核心判定
    # ------------------------------------------------------------------

    def _maybe_block(self, request: Any) -> ToolMessage | None:
        """判定本次写入是否应被回执闸门拦截。

        Returns:
            ToolMessage 表示拦截；None 表示放行。任何内部异常降级放行。
        """
        try:
            # 第 2 次运行起：用户已回复（= 确认信号），闸门满足
            if self._run_count > 1:
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
        """构造拦截消息：引导输出理解回执（对齐业务拦截消息规范）。"""
        tool_call_id = _mapping_value(tool_call, "id")
        return ToolMessage(
            content=(
                "[回执闸门] 本线程为首轮运行且 storyline.md 尚未创建：理解回执未确认，"
                "受保护产物（storyline.md / worldview.md / character/*.md）禁止写入。"
                "请先按系统提示词 §7.1 输出理解回执（需求复述 + 假设清单 + "
                "故事核心五字段草案），请用户确认或修改草案，然后返回。"
                "用户回复后的下一次运行方可写产物。"
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


__all__ = ["ReceiptGateMiddleware"]
