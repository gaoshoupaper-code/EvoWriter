"""StorylineIntegrityMiddleware — storyline.md 写入完整性护栏（REQ-20260930-002231 FR-011）。

职责：
  拦截 storybuilding 子代理对 ``/storyline.md`` 的 write_file / edit_file 调用，
  校验写入不得丢失既有内容：线区块、区块内事件行、线名/事件名。
  疑似丢失 → ToolMessage 硬拦截并点名丢失项；Agent 重试同一写入（坚持语义）
  → 放行一次（合法删减的逃生门，FR-011 失败语义「明确指令重试」）。

背景（单文件增量编辑的丢内容风险）：
  storyline.md 成为唯一手写产物后，Agent 局部编辑/整文件重写可能意外丢掉
  其他线区块或事件行。本护栏在写入落盘前对比「磁盘现状 vs 写入后预估内容」，
  把静默丢失变成显式拦截。

口径：
  - write_file：写入后内容 = args.content。
  - edit_file：写入后内容 ≈ 磁盘内容.replace(old_string, new_string)
    （old_string 不在磁盘内容中 → 无法预估 → 放行交 file_state_tracker 拦）。
  - 丢失判定：预估内容中，既有线区块头消失 / 区块事件行消失 / 线名事件名消失。
  - 护栏自身异常 → 降级放行 + 日志（护栏故障不得中断创作主流程）。
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from .path_guard import normalize_workspace_write_path

_STORYLINE_FILE = "/storyline.md"

# 线区块头（与 contracts._BLOCK_HEADER_RE 同构——此处无需类型捕获，仅定位区块归属）
_BLOCK_HEADER = re.compile(
    r"(?m)^##\s+([^#·•\n]+?)\s*[·•]\s*\**(主线|支线|角色线|暗线)\**(?:\s*[·•]|\s*$)"
)

_TABLE_HEADER_HINT = re.compile(r"^\|.*时序.*\|.*事件.*\|")


def _split_cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _extract_structure(text: str) -> tuple[set[str], dict[str, set[str]]]:
    """提取结构指纹：(线名集合, {线名: 事件名集合})。

    事件行按区块归属：从最近一个线区块头之后出现的事件表数据行。
    """
    lines_set: set[str] = set()
    events_by_line: dict[str, set[str]] = {}
    current: str | None = None
    table_cols: dict[str, int] | None = None

    for raw in text.splitlines():
        line = raw.strip()
        m = _BLOCK_HEADER.match(line)
        if m:
            current = m.group(1).strip().replace("**", "")
            lines_set.add(current)
            events_by_line.setdefault(current, set())
            table_cols = None
            continue
        if not line.startswith("|"):
            # 空行结束一张表；其他非表格行忽略
            if line:
                table_cols = None
            continue
        if re.fullmatch(r"\|[\s|:-]+\|", line):
            continue
        if _TABLE_HEADER_HINT.match(line):
            cols: dict[str, int] = {}
            for ci, cell in enumerate(_split_cells(line)):
                cell_clean = cell.replace("**", "").strip()
                if "时序" in cell_clean:
                    cols["时序"] = ci
                elif "事件" in cell_clean:
                    cols["事件"] = ci
            table_cols = cols if "事件" in cols else None
            continue
        if table_cols is None or current is None:
            continue
        cells = _split_cells(line)
        idx = table_cols["事件"]
        if idx < len(cells):
            name = cells[idx].replace("**", "").strip()
            if name:
                events_by_line[current].add(name)
    return lines_set, events_by_line


def _diff_losses(old_text: str, new_text: str) -> list[str]:
    """对比结构指纹，返回丢失项描述列表（空列表=无丢失）。"""
    old_lines, old_events = _extract_structure(old_text)
    new_lines, _new_events = _extract_structure(new_text)
    losses: list[str] = []
    for name in sorted(old_lines - new_lines):
        losses.append(f"故事线区块「{name}」")
    # 事件按名称全局比对（跨区块重排不算丢失，同名消失才算）
    _, new_events_map = _extract_structure(new_text)
    all_new_events: set[str] = set()
    for evs in new_events_map.values():
        all_new_events |= evs
    for line_name, events in old_events.items():
        for ev in sorted(events - all_new_events):
            losses.append(f"事件「{ev}」（原属 {line_name}）")
    return losses


class StorylineIntegrityMiddleware(AgentMiddleware):
    """storyline.md 写入完整性护栏。

  拦截 write_file / edit_file 到 ``/storyline.md`` 且预估内容丢失既有
  线区块/事件/名称的调用；同一丢失项重试一次后放行（逃生门）。
    """

    # 逃生门指纹：最近一次拦截的丢失项集合（frozenset 可哈希对比）
    _last_blocked_losses: frozenset[str] | None

    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = workspace_path.resolve()
        self._last_blocked_losses = None

    def _read_current(self) -> str:
        physical = self.workspace_path / "storyline.md"
        if not physical.exists():
            return ""
        try:
            return physical.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return physical.read_text(encoding="gb18030", errors="replace")

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
        """校验本次写入是否丢失既有内容；护栏自身异常降级放行。"""
        try:
            return self._check(request)
        except Exception:  # noqa: BLE001 — 护栏故障不得中断主流程
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
        if not current:
            return None  # 初构（无旧内容）无完整性基线

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
            projected = current.replace(old_string, new_string, 1)

        losses = _diff_losses(current, projected)
        if not losses:
            self._last_blocked_losses = None
            return None

        loss_fingerprint = frozenset(losses)
        if self._last_blocked_losses == loss_fingerprint:
            # 逃生门：Agent 重试同一写入（坚持语义）——视为委托明确要求的删减，放行
            self._last_blocked_losses = None
            return None

        self._last_blocked_losses = loss_fingerprint
        tool_call_id = (
            tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
        )
        loss_text = "、".join(losses[:8]) + ("…" if len(losses) > 8 else "")
        return ToolMessage(
            content=(
                f"storyline.md 完整性校验未通过：预估写入将丢失 {loss_text}。"
                "若这是意外丢失，请恢复上述内容后重新写入；"
                "若这是委托明确要求的删减，请原样重试同一写入即可通过。"
            ),
            name=str(tool_name),
            tool_call_id=str(tool_call_id or ""),
            status="error",
            response_metadata={"business_intercept": True},
        )


def build(abc):
    """架构清单挂载钩子：domain 护栏——单文件增量编辑防丢线区块/事件。"""
    return StorylineIntegrityMiddleware(abc.workspace_path)
