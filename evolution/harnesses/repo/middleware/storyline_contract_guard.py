"""StorylineContractGuardMiddleware — storyline.md 结构契约运行时护栏 + 节奏观测。

REQ-20260930-194437 原始职责（FR-003/004/005/007）+ REQ-20261009-182730 变更：
  拦截 storybuilding 子代理对 ``/storyline.md`` 的 write_file / edit_file 调用：
    - 结构契约（区块头类型词、线头两字段、事件表、T 号、列数一致、张力/爽点
      列与取值、设计原则与最终结局钉死、名称唯一）继续硬拦截——判定器唯一实现
      ``contracts.check_storyline_write``，两侧不得各自实现。
    - 事件数量与类型词**不再拦截**（REQ-20261009-182730）：改走观测模式——
      用 ``contracts.iter_line_blocks`` 解析新增/变更区块，按观测配置记录
      遵循率日志；REQ-20261010-000638 起观测同时记录张力/爽点分布，
      配置由 pacing_config 统一加载（含爽点间隔默认值与节拍诊断清单）。

防死循环（DEC-006，现仅覆盖结构规则）：
  同一结构规则连续拒绝 3 次后不再拦截该规则的违规（放行写入），并在下一轮
  模型调用前注入「强制收尾」指令（复用 QuotaConvergence 预算耗尽的收尾
  注入语义）；每次拦截记 executor 日志（规则名/拒绝序号/差距值）——
  拦截数据即线上遵循率观测源。

口径与既有护栏一致：
  - write_file：预估内容 = args.content。
  - edit_file：预估内容 ≈ 磁盘内容.replace(old, new, 1)；
    old_string 不在磁盘内容中 → 放行交 file_state_tracker 拦。
  - 初构（磁盘无文件）：current 为空串，结局规则无基线不校验。
  - 护栏自身异常 → 降级放行 + 日志（护栏故障不得中断创作主流程）。
"""
from __future__ import annotations
import logging
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

from contracts.storyline_contract import check_storyline_write, iter_line_blocks

from .pacing_config import PacingConfig, load_pacing_config
from .path_guard import normalize_workspace_write_path

logger = logging.getLogger(__name__)

_STORYLINE_FILE = "/storyline.md"

# 同一规则连续拒绝上限（第 3 次拒绝后，下一次同规则违规放行并强制收尾）
DEFAULT_MAX_REJECTS = 3

# ── 节奏语义参数（REQ-20261010-000638：参数唯一落点在观测配置，DEC-004）──
# 加载失败 → None：数量观测降级为只记实际数，张力/爽点分布照记实际值。

_PACING_CONFIG: PacingConfig | None = load_pacing_config()


def _value_counts(values: Iterable[str]) -> dict[str, int]:
    """非空取值的计数（空串/占位跳过）。"""
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    return counts


class StorylineContractGuardMiddleware(AgentMiddleware):
    """storyline.md 结构契约 / 事件数量 / 结局不可变 运行时护栏。

  拦截 write_file / edit_file 到 ``/storyline.md`` 的调用；违规写入被弹回并
  返回定向错误信息；同一规则连续 3 次被拒 → 放行 + 注入强制收尾指令。
    """

    def __init__(self, workspace_path: Path, *, max_rejects: int = DEFAULT_MAX_REJECTS) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.max_rejects = max_rejects
        self._reject_counts: dict[str, int] = {}
        self._wrapup_gaps: list[str] = []

    # ── 运行周期重置（每次 graph 执行开始，对齐 RevisionLimit 口径）──

    def before_agent(self, state: Any, runtime: Any) -> None:
        self._reject_counts.clear()
        self._wrapup_gaps.clear()

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        self._reject_counts.clear()
        self._wrapup_gaps.clear()

    # ── 强制收尾注入（DEC-006：复用 QuotaConvergence 收尾注入语义）──

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._inject_wrapup()

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._inject_wrapup()

    def _inject_wrapup(self) -> dict[str, Any] | None:
        if not self._wrapup_gaps:
            return None
        gaps = "；".join(self._wrapup_gaps)
        self._wrapup_gaps.clear()
        return {"messages": [HumanMessage(content=(
            f"[护栏强制收尾] 以下校验已连续 {self.max_rejects} 次未通过，本次写入已放行：{gaps}。"
            "停止继续修正该问题，基于现有内容收尾：核对 storyline.md 事件表时序号、交汇标注与线头字段，"
            "按流程调用 review 审查一次、按需修订一次，然后返回，并在返回中如实说明上述未达标项。"
        ))]}

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
            logger.exception("storyline 契约护栏内部异常，降级放行")
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

        if normalized != _STORYLINE_FILE:
            return None

        current = self._read_current()

        if tool_name == "write_file":
            content = args.get("content")
            if not isinstance(content, str):
                return None
            projected = content
        else:
            old_string = args.get("old_string")
            new_string = args.get("new_string")
            if not isinstance(old_string, str) or not isinstance(new_string, str):
                return None
            if old_string not in current:
                return None  # 模拟替换失败：交 file_state_tracker 拦
            # replace_all=True 且多处出现 → 全量替换（与工具落盘语义一致，防首处外绕过）
            if args.get("replace_all") and current.count(old_string) > 1:
                projected = current.replace(old_string, new_string)
            else:
                projected = current.replace(old_string, new_string, 1)

        violations = check_storyline_write(current, projected)
        if not violations:
            self._reject_counts.clear()
            self._observe_write(current, projected)
            return None

        # 防死循环：已达连续拒绝上限的规则 → 放行其违规并排队强制收尾
        blocked_msgs: list[str] = []
        blocked_rules: set[str] = set()
        for v in violations:
            if self._reject_counts.get(v.rule, 0) >= self.max_rejects:
                self._wrapup_gaps.append(v.message)
                logger.warning(
                    "storyline 护栏放行（连续%d次被拒） rule=%s detail=%s",
                    self.max_rejects, v.rule, v.message,
                )
            else:
                blocked_msgs.append(v.message)
                blocked_rules.add(v.rule)

        if not blocked_msgs:
            # 全部违规规则均到上限：放行本次写入，收尾指令待注入；写入会落盘 → 观测
            self._observe_write(current, projected)
            return None

        for rule in sorted(blocked_rules):
            self._reject_counts[rule] = self._reject_counts.get(rule, 0) + 1
            logger.warning(
                "storyline 护栏拦截 rule=%s seq=%d detail=%s",
                rule, self._reject_counts[rule],
                "；".join(m for m in blocked_msgs),
            )

        tool_call_id = (
            tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
        )
        detail = "；".join(blocked_msgs[:6]) + ("…" if len(blocked_msgs) > 6 else "")
        return ToolMessage(
            content=(
                f"storyline.md 写入校验未通过：{detail}。"
                "请修正上述问题后重新写入完整内容；同一问题连续 3 次被拒后系统将强制收尾。"
            ),
            name=str(tool_name),
            tool_call_id=str(tool_call_id or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )

    # ── 数量/类型词观测（REQ-20261009-182730 FR-004：只记日志，永不拦截）──

    def _observe_write(self, current: str, projected: str) -> None:
        """对本次落盘内容的新增/变更线区块记录观测日志（遵循率数据源）。

        口径与结构规则同源：仅新增或变更区块；非交汇事件计数（「交汇」列
        非空不计入数量、类型词与张力/爽点分布）；缺表区块跳过（已由结构
        规则点名）。观测自身异常降级跳过，不影响写入。
        """
        try:
            cur_texts = {b.name: b.text for b in iter_line_blocks(current)}
            for blk in iter_line_blocks(projected):
                if blk.name in cur_texts and cur_texts[blk.name] == blk.text:
                    continue
                if not blk.events:
                    continue
                non_crossing = [r for r in blk.events if not r.crossing]
                dist: dict[str, int] = {}
                for r in non_crossing:
                    if r.type_word:
                        dist[r.type_word] = dist.get(r.type_word, 0) + 1
                types_part = "/".join(f"{w}x{c}" for w, c in dist.items()) or "-"
                tension_part = "/".join(
                    f"{w}x{c}" for w, c in sorted(_value_counts(r.tension for r in non_crossing).items())
                ) or "-"
                payoff_part = "/".join(
                    f"{w}x{c}" for w, c in sorted(_value_counts(r.payoff for r in non_crossing).items())
                ) or "-"

                config = _PACING_CONFIG
                if config is None:
                    logger.info(
                        "storyline 观测 line_type=%s block=%s non_crossing=%d "
                        "range=None in_range=None types=%s tension=%s payoff=%s custom=None（配置缺失，只记实际数）",
                        blk.type, blk.name, len(non_crossing), types_part, tension_part, payoff_part,
                    )
                    continue

                rng = config.count_ranges.get(blk.type)
                if rng is None:
                    logger.info(
                        "storyline 观测 line_type=%s block=%s non_crossing=%d "
                        "range=None in_range=None types=%s tension=%s payoff=%s（该线类型无参考区间）",
                        blk.type, blk.name, len(non_crossing), types_part, tension_part, payoff_part,
                    )
                    continue
                lo, hi = rng
                in_range = lo <= len(non_crossing) <= hi
                custom_part = (
                    "/".join(f"{w}x{c}" for w, c in dist.items() if w not in config.reference_types) or "-"
                )
                logger.info(
                    "storyline 观测 line_type=%s block=%s non_crossing=%d "
                    "range=[%d,%d] in_range=%s types=%s tension=%s payoff=%s custom=%s",
                    blk.type, blk.name, len(non_crossing), lo, hi, in_range,
                    types_part, tension_part, payoff_part, custom_part,
                )
        except Exception:  # noqa: BLE001 — 观测异常不得影响写入
            logger.exception("storyline 观测异常，降级跳过本次观测")

    def _read_current(self) -> str:
        physical = self.workspace_path / "storyline.md"
        if not physical.exists():
            return ""
        try:
            return physical.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return physical.read_text(encoding="gb18030", errors="replace")


__all__ = ["DEFAULT_MAX_REJECTS", "StorylineContractGuardMiddleware"]


def build(abc):
    """架构清单挂载钩子：domain 护栏——故事线结构契约运行时校验。"""
    return StorylineContractGuardMiddleware(abc.workspace_path)
