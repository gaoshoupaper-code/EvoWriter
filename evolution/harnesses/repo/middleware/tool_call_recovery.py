"""正文作者把工具调用写成普通文字时，给予两次真实调用纠错机会。"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Annotated, Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from typing_extensions import NotRequired

from contracts.writing_artifacts import STAGE_TEXT, resolve_confirmed_stage

_MAX_RECOVERIES = 2
_FAILURE_TEXT = (
    "工具调用未执行：已尝试纠正 2 次，模型仍只输出调用文字。"
    "本轮任务未完成；请核对已保存正文后继续。"
)


class ToolCallRecoveryState(AgentState):
    tool_call_recovery_count: Annotated[NotRequired[int], PrivateStateAttr]
    tool_call_recovery_turn: Annotated[NotRequired[str | None], PrivateStateAttr]


class ToolCallRecoveryMiddleware(AgentMiddleware[ToolCallRecoveryState]):
    state_schema = ToolCallRecoveryState

    def __init__(self, tool_names: Sequence[str] = ()) -> None:
        names = "|".join(re.escape(name) for name in sorted(set(tool_names)))
        self._literal_call = re.compile(
            rf"(?<![\w:])call:default_api:({names})\s*\{{"
        ) if names else None

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: ToolCallRecoveryState, runtime: Any) -> dict[str, Any] | None:
        return self._recover(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: ToolCallRecoveryState, runtime: Any) -> dict[str, Any] | None:
        return self._recover(state)

    def _recover(self, state: ToolCallRecoveryState) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if self._literal_call is None or not messages:
            return None
        message = messages[-1]
        if not isinstance(message, AIMessage) or message.tool_calls:
            return None
        content = message.content
        if isinstance(content, list):
            content = "\n".join(
                block if isinstance(block, str) else block.get("text", "")
                for block in content
                if isinstance(block, str) or (
                    block.get("type") == "text" and isinstance(block.get("text"), str)
                )
            )
        matched = self._literal_call.search(content)
        if matched is None:
            return None

        # 用户消息 ID 随 checkpoint 保存；重开同轮不补额度，下一轮自然换键。
        turn = next((msg.id for msg in reversed(messages) if isinstance(msg, HumanMessage)), None)
        count = int(state.get("tool_call_recovery_count") or 0) if (
            state.get("tool_call_recovery_turn") == turn
        ) else 0
        updates: dict[str, Any] = {"tool_call_recovery_turn": turn}
        output = [RemoveMessage(id=message.id)] if message.id else []
        if count >= _MAX_RECOVERIES:
            updates.update({
                "tool_call_recovery_count": count,
                "messages": [*output, AIMessage(content=_FAILURE_TEXT)],
            })
            return updates

        guidance = (
            f"上一条仅把 {matched.group(1)} 工具调用写成了文字，工具尚未执行。"
            "请使用工具通道发送真实调用，不要再次输出调用文本。"
            "先核对已有工具结果和已保存正文，只补做未执行的步骤；"
            "不要重复保存或重新调用已完成的审稿。"
        )
        updates.update({
            "jump_to": "model",
            "tool_call_recovery_count": count + 1,
            "messages": [*output, AIMessage(content=guidance)],
        })
        return updates


def build(abc):
    enabled = (
        abc.agent.name == "author"
        and abc.trace_name == "author-subagent"
        and not getattr(getattr(abc, "ctx", None), "character_repair", False)
        and resolve_confirmed_stage(abc.workspace_path) == STAGE_TEXT
    )
    tool_names = list(abc.agent.tools) if enabled else []
    if enabled and getattr(abc.agent, "delegates", None):
        tool_names.append("task")
    return ToolCallRecoveryMiddleware(tool_names)
