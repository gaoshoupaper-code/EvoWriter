"""StageGateMiddleware — 阶段写入硬闸（REQ-20261018-115508 FR-006，DEC-008 L1）。

当前大段（装配时按目录扫描冻结；DEC-002）决定本轮可用的写入门面：

  - save_artifact：kind 必须属于 stage_allowed_kinds(stage)——题材段只能写
    主题，章纲段才能写章纲/文风卡……越段写硬拦（要改上游先回上游段重走）。
  - update_foreshadow_table / rewrite_rhythm_table：仅章纲段。
  - append_chapter_deviation：仅正文段（该章已人修定稿的前置由工具二道防线再校验）。

标注修订任务放行（REQ-20261027-213246 FR-005/DEC-004）：产物页划词标注打包
的定点微调**不回段、不切段**——装配时读 workspace 标注存储，把「已进任务包
且仍待处理」的标注所属 kind 追加进放行集（仅放行锚点 edit；mode=full 仍拦，
防整篇重写把无关标注打成已失效）。任务包处理完（标注转已解决/已失效）后，
后续装配自然收回放行。

读档（read_artifact）与拍板（confirm_with_user）不受限。段间移动
（前进/跳段/回退）只经界面人工确认（REQ-20261019-232617 FR-004：模型
工具箱无跳段工具，只能文字建议作者在界面操作）。
legacy 工作区（旧结构扫描不出五段进度 → 阶段停在题材且无对应产物可写）
被本闸结构性挡住一切产物写入——只读语义由结构自然成立（DEC-010）。

创作纪律（字数/供给点/情绪限流/库存线）是软检，不归本闸（DEC-008 L3）。
拦截事件经 ToolMessage status=error 透出；工具入参（锚点→替换）由既有
TraceMiddleware 进 trace（L4）。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from contracts.writing_artifacts import (
    KIND_THEME,
    STAGE_CHAPTER,
    STAGE_LABELS,
    STAGE_TEXT,
    parse_relpath,
    stage_allowed_kinds,
)

logger = logging.getLogger(__name__)

_SAVE_TOOL = "save_artifact"
_FORESHADOW_TOOL = "update_foreshadow_table"
_RHYTHM_TOOL = "rewrite_rhythm_table"
_DEVIATION_TOOL = "append_chapter_deviation"

# executor 标注存储（app.domains.writing.annotations）的落盘位置与字段口径；
# harness 不 import executor（自包含），只按文件约定读。
_ANNOTATIONS_FILE = ".writing/annotations.json"

# legacy 只读段（S1/C6 修复）：不在 _STAGE_KINDS 里 → 一切产物写入拦死
_LEGACY_STAGE = "__legacy_readonly__"

_STAGE_ONLY_TOOLS: dict[str, str] = {
    _FORESHADOW_TOOL: STAGE_CHAPTER,
    _RHYTHM_TOOL: STAGE_CHAPTER,
    _DEVIATION_TOOL: STAGE_TEXT,
}


def annotation_task_kinds(workspace_path: Path) -> set[str]:
    """标注任务越段放行集：已进任务包（dispatched）且仍待处理（pending）的
    标注所属产物 kind。文件缺失/解析失败 → 空集（闸门语义与既有完全一致）。"""
    try:
        raw = json.loads(
            (Path(workspace_path) / _ANNOTATIONS_FILE).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return set()
    kinds: set[str] = set()
    for item in raw.get("annotations", []):
        if not isinstance(item, dict):
            continue
        if item.get("status") != "pending" or not item.get("dispatched"):
            continue
        ref = parse_relpath(str(item.get("artifact_rel") or ""))
        # 主题不在标注范围（DEC-006，创建端已拦；闸门侧双保险排除）
        if ref is not None and ref.kind != KIND_THEME:
            kinds.add(ref.kind)
    return kinds


class StageGateMiddleware(AgentMiddleware):
    """阶段写入硬闸：越段写拦截（fail-closed，不产生任何文件变更）。"""

    def __init__(self, stage: str, *, max_blocks: int = 5,
                 annotation_kinds: set[str] | None = None):
        self.stage = stage
        self.max_blocks = max_blocks
        self.annotation_kinds = annotation_kinds or set()
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
                    # 标注任务放行（REQ-20261027-213246）：任务包内产物的定点
                    # 微调不回段——仅限锚点 edit（mode=full 仍拦）。
                    if kind in self.annotation_kinds and args.get("mode", "edit") == "edit":
                        return None
                    return self._block(
                        request,
                        f"当前是「{STAGE_LABELS.get(self.stage, self.stage)}」段，"
                        f"只允许写入产物种类 {sorted(allowed)}，收到 {kind!r}。"
                        "要改上游产物：先请作者在界面回退到对应段重走重确认；"
                        "标注任务条目请用锚点 edit 定点修改；"
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
    """架构清单挂载钩子：装配时冻结当前段（FR-002 人工段指针口径）。

    段解析与 executor 路由同口径（contracts.resolve_confirmed_stage 唯一
    实现）：人工段指针优先，缺失按目录扫描兜底（FR-005 存量迁移语义）。
    legacy 工作区（旧五件套/七步/三步制）构造空段闸——一切写入拦死
    （FR-012 只读，S1/C6 修复）。
    """
    from contracts.writing_artifacts import (
        classify_writing_workspace,
        resolve_confirmed_stage,
    )

    if classify_writing_workspace(abc.workspace_path) == "legacy":
        return StageGateMiddleware(_LEGACY_STAGE)
    return StageGateMiddleware(
        resolve_confirmed_stage(abc.workspace_path),
        annotation_kinds=annotation_task_kinds(abc.workspace_path),
    )


__all__ = ["StageGateMiddleware", "annotation_task_kinds"]
