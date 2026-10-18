"""StageGateMiddleware — 阶段写入硬闸（REQ-20261018-115508 FR-006，DEC-008 L1）。

当前大段（装配时按目录扫描冻结；DEC-002）决定本轮可用的写入门面：

  - save_artifact：kind 必须属于 stage_allowed_kinds(stage)——题材段只能写
    主题，章纲段才能写章纲/文风卡……越段写硬拦（要改上游先回上游段重走）。
  - update_foreshadow_table / rewrite_rhythm_table：仅章纲段。
  - append_chapter_deviation：仅正文段（该章已人修定稿的前置由工具二道防线再校验）。

读档（read_artifact）、拍板（confirm_with_user）、跳段（handoff）不受限。
legacy 工作区（旧结构扫描不出五段进度 → 阶段停在题材且无对应产物可写）
被本闸结构性挡住一切产物写入——只读语义由结构自然成立（DEC-010）。

创作纪律（字数/供给点/情绪限流/库存线）是软检，不归本闸（DEC-008 L3）。
拦截事件经 ToolMessage status=error 透出；工具入参（锚点→替换）由既有
TraceMiddleware 进 trace（L4）。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from contracts.writing_artifacts import (
    STAGE_CHAPTER,
    STAGE_LABELS,
    STAGE_TEXT,
    derive_stage,
    scan_writing_progress,
    stage_allowed_kinds,
)

logger = logging.getLogger(__name__)

_SAVE_TOOL = "save_artifact"
_FORESHADOW_TOOL = "update_foreshadow_table"
_RHYTHM_TOOL = "rewrite_rhythm_table"
_DEVIATION_TOOL = "append_chapter_deviation"

# legacy 只读段（S1/C6 修复）：不在 _STAGE_KINDS 里 → 一切产物写入拦死
_LEGACY_STAGE = "__legacy_readonly__"

_STAGE_ONLY_TOOLS: dict[str, str] = {
    _FORESHADOW_TOOL: STAGE_CHAPTER,
    _RHYTHM_TOOL: STAGE_CHAPTER,
    _DEVIATION_TOOL: STAGE_TEXT,
}


class StageGateMiddleware(AgentMiddleware):
    """阶段写入硬闸：越段写拦截（fail-closed，不产生任何文件变更）。"""

    def __init__(self, stage: str, *, max_blocks: int = 5):
        self.stage = stage
        self.max_blocks = max_blocks
        self._blocks = 0
        self._exhausted_logged = False

    def _mapping(self, request: Any) -> dict:
        tool_call = getattr(request, "tool_call", {})
        if isinstance(tool_call, dict):
            return tool_call
        return {
            "name": getattr(tool_call, "name", None),
            "args": getattr(tool_call, "args", None),
            "id": getattr(tool_call, "id", None),
        }

    def _block(self, request: Any, reason: str) -> ToolMessage:
        m = self._mapping(request)
        self._blocks += 1
        return ToolMessage(
            content=f"[阶段闸] {reason}",
            name=str(m.get("name") or ""),
            tool_call_id=str(m.get("id") or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )

    def _maybe_block(self, request: Any) -> ToolMessage | None:
        try:
            m = self._mapping(request)
            name = str(m.get("name") or "")
            args = m.get("args")
            args = args if isinstance(args, dict) else {}

            if name == _SAVE_TOOL:
                kind = str(args.get("kind") or "")
                allowed = stage_allowed_kinds(self.stage)
                if kind and kind not in allowed:
                    return self._block(
                        request,
                        f"当前是「{STAGE_LABELS.get(self.stage, self.stage)}」段，"
                        f"只允许写入产物种类 {sorted(allowed)}，收到 {kind!r}。"
                        "要改上游产物：先请作者点名跳段（handoff）回对应段重走重确认；"
                        "本段产物按当前段流程推进。",
                    )
                return None

            want_stage = _STAGE_ONLY_TOOLS.get(name)
            if want_stage and self.stage != want_stage:
                return self._block(
                    request,
                    f"工具 {name} 仅在「{STAGE_LABELS.get(want_stage, want_stage)}」段可用，"
                    f"当前是「{STAGE_LABELS.get(self.stage, self.stage)}」段。",
                )
            return None
        except Exception:  # noqa: BLE001 — fail-closed（FR-006）：闸门判定异常
            # 时拒绝而非放行——硬闸语义不允许以放行退出；非写工具不受影响。
            logger.exception("StageGate 判定异常，按拦截处理（fail-closed）")
            try:
                return self._block(request, "闸门内部判定异常，本次调用按拦截处理（fail-closed）。")
            except Exception:  # noqa: BLE001 — 连构造拦截都失败：抛回执行端
                raise

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        # 拦截上限（防日志刷屏：达上限后仍持续拦截，只记一次日志——fail-closed，
        # S2/C8 修复；防死循环交给 recursion_limit 保险丝）
        blocked = self._maybe_block(request)
        if blocked is not None:
            if self._blocks >= self.max_blocks and not self._exhausted_logged:
                self._exhausted_logged = True
                logger.warning(
                    "[阶段闸] 越段写入已连续拦截 %d 次（继续拦截，fail-closed）stage=%s",
                    self.max_blocks, self.stage,
                )
            return blocked
        return handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        blocked = self._maybe_block(request)
        if blocked is not None:
            if self._blocks >= self.max_blocks and not self._exhausted_logged:
                self._exhausted_logged = True
                logger.warning(
                    "[阶段闸] 越段写入已连续拦截 %d 次（继续拦截，fail-closed）stage=%s",
                    self.max_blocks, self.stage,
                )
            return blocked
        return await handler(request)


def build(abc):
    """架构清单挂载钩子：装配时冻结当前段（DEC-002）。

    段解析三来源与 executor 路由同口径（C3 修复）：点名跳段（handoff 写入
    .writing/state.json 的 override_stage，装配发生在 commit 清除之前）>
    目录扫描。legacy 工作区（旧五件套/七步/三步制）构造空段闸——一切写
    入拦死（FR-012 只读，S1/C6 修复）。
    """
    from contracts.writing_artifacts import (
        classify_writing_workspace,
        load_writing_state,
    )

    if classify_writing_workspace(abc.workspace_path) == "legacy":
        return StageGateMiddleware(_LEGACY_STAGE)
    override = load_writing_state(abc.workspace_path).override_stage
    stage = override if override in STAGE_LABELS else derive_stage(
        scan_writing_progress(abc.workspace_path)
    )
    return StageGateMiddleware(stage)


__all__ = ["StageGateMiddleware"]
