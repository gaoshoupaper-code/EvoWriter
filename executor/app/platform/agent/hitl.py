"""HITL（Human-In-The-Loop）interrupt payload 统一协议（DD4）。

LangGraph 的 ``interrupt()`` 机制用于在 agent 执行中暂停等待人类反馈。本模块
定义统一的 payload 契约，让任意 domain 都能定义自己的 HITL 反馈类型，前端按
``kind`` 字段路由渲染。

协议设计（DD4）：
- payload 必须含 ``kind`` 字段，标识反馈类型，前端据此选择渲染组件。
- ``kind="choice"``：现有写作访谈的选项化反馈（向后兼容）。
- 各 domain 可扩展自己的 ``kind`` 和对应 payload schema。

resume 值（用户反馈回传）同样按 ``kind`` 区分：
- ``choice``：字符串（现有行为，``InterviewOptions.buildResume()`` 拼接）。

向后兼容：无 ``kind`` 字段的 payload 视为 ``choice``（现有访谈 payload 不含 kind）。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# ── kind 类型 ──────────────────────────────────────────────

Kind = Literal["choice"]
"""HITL 反馈类型。前端按此字段路由渲染组件。扩展新 domain 的 kind 时加入此 Literal。"""

# ── choice（现有写作访谈）payload ──────────────────────────


class AskUserOption(TypedDict, total=False):
    """访谈选项（与 writer/tools/ask_user.py 的 AskUserOption 对齐）。"""

    label: str
    description: str


class ChoiceInterruptPayload(TypedDict, total=False):
    """kind="choice" 的 interrupt payload（向后兼容现有访谈）。"""

    kind: Kind
    source: str
    question: str
    options: list[AskUserOption]
    multi_select: bool


# ── 路由工具 ───────────────────────────────────────────────


def interrupt_kind(payload: Any) -> Kind:
    """从 interrupt payload 提取 kind，无 kind 字段时回退 "choice"（向后兼容）。

    现有写作访谈的 payload 不含 kind（``ask_user`` 工具直接构造 {question,options,...}），
    需兼容。新代码构造 payload 时应显式带 kind。
    """
    if isinstance(payload, dict):
        k = payload.get("kind")
        if k in ("choice",):
            return k  # type: ignore[return-value]
    return "choice"


__all__ = [
    "Kind",
    "AskUserOption",
    "ChoiceInterruptPayload",
    "interrupt_kind",
]
