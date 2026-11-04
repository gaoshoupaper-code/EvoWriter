"""现代正文起草的交付门：保存状态与一次实际审稿缺一不可。"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr, hook_config
from langchain_core.messages import AIMessage, RemoveMessage
from typing_extensions import NotRequired

from ..text_completion import (
    STATE_KEY, completion_snapshot, current_turn, delivery_block_reason, enabled_for, failed_review_note,
)
from .tool_call_recovery import _FAILURE_TEXT as TOOL_CALL_EXHAUSTED


class TextCompletionState(AgentState):
    text_completion: Annotated[NotRequired[dict[str, Any]], PrivateStateAttr]
    text_completion_output_count: Annotated[NotRequired[int], PrivateStateAttr]
    text_completion_output_turn: Annotated[NotRequired[str | None], PrivateStateAttr]


class TextCompletionMiddleware(AgentMiddleware[TextCompletionState]):
    state_schema = TextCompletionState

    def __init__(self, workspace_root: Path | None = None):
        self.root = Path(workspace_root) if workspace_root is not None else None

    def before_model(self, state, runtime):
        if self.root is None:
            return None
        snapshot = completion_snapshot(state, self.root)
        return {STATE_KEY: snapshot} if snapshot != state.get(STATE_KEY) else None

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        return self._guard(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self._guard(state)

    def _guard(self, state):
        messages = state.get("messages", [])
        if self.root is None or not messages:
            return None
        message = messages[-1]
        if not isinstance(message, AIMessage) or message.tool_calls or message.content == TOOL_CALL_EXHAUSTED:
            return None
        if completion_snapshot(state, self.root).get("active_turn") != current_turn(state):
            return None
        reason = delivery_block_reason(state, self.root)
        if not reason:
            note = failed_review_note(state, self.root)
            if note and note not in str(message.content):
                content = f"{message.content}\n\n{note}" if isinstance(message.content, str) else [
                    *message.content, {"type": "text", "text": note},
                ]
                return {"messages": [message.model_copy(update={"content": content})]}
            return None
        turn = current_turn(state)
        count = state.get("text_completion_output_count", 0) if state.get("text_completion_output_turn") == turn else 0
        output = [RemoveMessage(id=message.id)] if message.id else []
        if count >= 2:
            return {"messages": [*output, AIMessage(content=f"正文交付未完成：{reason}已尝试纠正2次，本轮停止；请核对实际正文后继续。") ]}
        return {
            "jump_to": "model", "text_completion_output_count": count + 1,
            "text_completion_output_turn": turn,
            "messages": [*output, AIMessage(content=(
                f"正文尚不能交付：{reason}请核对实际保存状态并补做必要步骤。"
                "审稿必须在初稿完成后实际委托；已有审稿结果不重复调用，失败则注明降级。"
            ))],
        }


def build(abc):
    return TextCompletionMiddleware(abc.workspace_path if enabled_for(abc) else None)
