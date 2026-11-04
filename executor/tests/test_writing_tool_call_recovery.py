"""正文伪工具调用纠错：真实图状态、有界重试及 checkpoint 轮次隔离。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app.platform.agent.loader import load_package
from contracts.writing_artifacts import WritingState, save_writing_state

_HARNESS_DIR = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH",
    str(Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"),
))
load_package(_HARNESS_DIR)

from harness_current.middleware.tool_call_recovery import (  # noqa: E402
    ToolCallRecoveryMiddleware,
    build,
)
from harness_current.middleware.revision_limit import RevisionLimitMiddleware  # noqa: E402


class _ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "writing-tool-recovery-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        assert self.calls < len(self.responses), "unexpected extra model call"
        message = self.responses[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def _literal() -> AIMessage:
    return AIMessage(content='先保存。call:default_api:save_artifact{content:"正文"}')


def _real_call(call_id: str, content: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{
        "name": "save_artifact", "args": {"content": content}, "id": call_id,
    }])


def _agent(model, saved, checkpointer):
    def save_artifact(content: str) -> str:
        """保存本次正文片段。"""
        saved.append(content)
        return "已保存"

    return create_agent(
        model=model,
        tools=[save_artifact],
        middleware=[ToolCallRecoveryMiddleware(["save_artifact"])],
        checkpointer=checkpointer,
    )


def test_literal_call_recovers_to_one_real_tool_call_and_saves_private_state():
    async def run():
        saved = []
        model = _ScriptedModel(responses=[_literal(), _real_call("save-1", "完整场景"), AIMessage(content="完成")])
        agent = _agent(model, saved, InMemorySaver())
        config = {"configurable": {"thread_id": "recovery"}}
        result = await agent.ainvoke({"messages": [HumanMessage(content="写正文", id="turn-1")]}, config)
        state = (await agent.aget_state(config)).values

        assert saved == ["完整场景"]
        assert model.calls == 3
        assert state["tool_call_recovery_count"] == 1
        assert state["tool_call_recovery_turn"] == "turn-1"
        assert "tool_call_recovery_count" not in result
        assert sum(isinstance(msg, ToolMessage) for msg in state["messages"]) == 1

    asyncio.run(run())


@pytest.mark.parametrize("recovered_tool", ["read_artifact", "save_artifact"])
def test_task_recovery_keeps_real_review_and_its_original_limit(tmp_path, recovered_tool):
    save_writing_state(tmp_path, WritingState(confirmed_stage="text", stage="text"))
    declared_tools = ["read_artifact", "save_artifact"]
    abc = SimpleNamespace(
        agent=SimpleNamespace(name="author", tools=declared_tools, delegates=["review"]),
        trace_name="author-subagent", workspace_path=tmp_path,
    )
    middleware = build(abc)
    assert declared_tools == ["read_artifact", "save_artifact"]
    actions = []

    def read_artifact() -> str:
        """读取实际正文。"""
        actions.append("read")
        return "已保存正文"

    def save_artifact(content: str) -> str:
        """保存局部修订。"""
        actions.append(content)
        return "已保存修订"

    review = _ScriptedModel(responses=[AIMessage(content="审稿意见：修正局部笔误。")])
    review_args = {"subagent_type": "review", "description": "检查完整正文"}
    args = {} if recovered_tool == "read_artifact" else {"content": "修订正文"}
    author = _ScriptedModel(responses=[
        AIMessage(content='call:default_api:task{subagent_type:"review",description:"检查完整正文"}'),
        AIMessage(content="", tool_calls=[{"name": "task", "args": review_args, "id": "review-1"}]),
        AIMessage(content=f"call:default_api:{recovered_tool}{{}}"),
        AIMessage(content="", tool_calls=[{"name": recovered_tool, "args": args, "id": "repair"}]),
        AIMessage(content="", tool_calls=[{"name": "task", "args": review_args, "id": "review-2"}]),
        AIMessage(content="已修订，不再审稿。"),
    ])
    review_limit = RevisionLimitMiddleware(max_revisions=1)
    agent = create_deep_agent(
        model=author,
        tools=[read_artifact, save_artifact],
        subagents=[{
            "name": "review", "description": "检查完整正文", "model": review,
            "system_prompt": "只审不改。", "tools": [],
        }],
        middleware=[middleware, review_limit],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "review-limit"}}
    result = agent.invoke({"messages": [HumanMessage(content="完成本章审稿修订", id="turn-1")]}, config)
    tool_results = {msg.tool_call_id: msg.content for msg in result["messages"] if isinstance(msg, ToolMessage)}

    assert author.calls == 6
    assert review.calls == 1
    assert "审稿意见" in tool_results["review-1"]
    assert "已达到本轮审查上限" in tool_results["review-2"]
    assert actions == (["read"] if recovered_tool == "read_artifact" else ["修订正文"])
    assert agent.get_state(config).values["tool_call_recovery_count"] == 2


@pytest.mark.parametrize("has_tool_call", [False, True])
def test_real_graph_passes_normal_output_and_executes_real_tool_calls_once(has_tool_call):
    saved = []
    message = _real_call("actual-save", "正文") if has_tool_call else AIMessage(content="沈砚走出商行。")
    if has_tool_call:
        message.content = "call:default_api:save_artifact{}"
    responses = [message, AIMessage(content="完成")] if has_tool_call else [message]
    model = _ScriptedModel(responses=responses)
    agent = _agent(model, saved, InMemorySaver())
    config = {"configurable": {"thread_id": "pass-through"}}
    agent.invoke({"messages": [HumanMessage(content="写正文", id="turn-1")]}, config)

    assert saved == (["正文"] if has_tool_call else [])
    assert model.calls == (2 if has_tool_call else 1)
    assert "tool_call_recovery_count" not in agent.get_state(config).values


def test_two_recovery_limit_is_not_reset_by_real_tool_calls():
    saved = []
    model = _ScriptedModel(responses=[
        _literal(), _real_call("save-1", "前文"),
        _literal(), _real_call("save-2", "后文"), _literal(),
    ])
    agent = _agent(model, saved, InMemorySaver())
    config = {"configurable": {"thread_id": "bounded"}}
    result = agent.invoke({"messages": [HumanMessage(content="写正文", id="turn-1")]}, config)
    state = agent.get_state(config).values

    assert model.calls == 5
    assert saved == ["前文", "后文"]
    assert state["tool_call_recovery_count"] == 2
    assert "本轮任务未完成" in result["messages"][-1].content


def test_reopened_checkpoint_keeps_limit_and_new_user_turn_gets_new_budget(tmp_path):
    saved = []
    config = {"configurable": {"thread_id": "persisted"}}
    checkpoint = str(tmp_path / "recovery.sqlite")
    first_model = _ScriptedModel(responses=[_literal(), _literal(), _literal()])
    with SqliteSaver.from_conn_string(checkpoint) as saver:
        agent = _agent(first_model, saved, saver)
        agent.invoke({"messages": [HumanMessage(content="写正文", id="turn-1")]}, config)
        assert agent.get_state(config).values["tool_call_recovery_count"] == 2
    assert first_model.calls == 3
    assert saved == []

    reopened_model = _ScriptedModel(responses=[
        _literal(), _literal(), _real_call("save-new", "接续正文"), AIMessage(content="完成"),
    ])
    with SqliteSaver.from_conn_string(checkpoint) as saver:
        agent = _agent(reopened_model, saved, saver)
        result = agent.invoke({"messages": []}, config)
        assert reopened_model.calls == 1
        assert "本轮任务未完成" in result["messages"][-1].content
        assert agent.get_state(config).values["tool_call_recovery_count"] == 2
        assert saved == []

        agent.invoke({"messages": [HumanMessage(content="核对后继续", id="turn-2")]}, config)
        state = agent.get_state(config).values
        assert reopened_model.calls == 4
        assert saved == ["接续正文"]
        assert state["tool_call_recovery_count"] == 1
        assert state["tool_call_recovery_turn"] == "turn-2"


@pytest.mark.parametrize("message", [
    AIMessage(content="沈砚收好铜印，走出商行。"),
    AIMessage(content="call:default_api:unregistered_tool{}"),
    AIMessage(content="call:default_api:save_artifact_extra{}"),
    AIMessage(content="call:another_api:save_artifact{}"),
    AIMessage(content=[{"type": "reasoning", "text": "call:default_api:save_artifact{}"}]),
    AIMessage(content="call:default_api:save_artifact{}", tool_calls=[{
        "name": "save_artifact", "args": {"content": "正文"}, "id": "real-save",
    }]),
    ToolMessage(content="call:default_api:save_artifact{}", tool_call_id="tool-result"),
])
def test_normal_text_and_real_tool_calls_are_untouched(message):
    middleware = ToolCallRecoveryMiddleware(["save_artifact"])
    assert middleware.after_model({"messages": [message]}, None) is None


def test_only_text_content_blocks_trigger_recovery():
    message = AIMessage(content=[{"type": "text", "text": "call:default_api:read_artifact{}"}])
    result = ToolCallRecoveryMiddleware(["read_artifact"]).after_model({"messages": [message]}, None)
    assert result["jump_to"] == "model"
    assert result["tool_call_recovery_count"] == 1


@pytest.mark.parametrize(("name", "stage", "character_repair", "enabled"), [
    ("author", "text", False, True),
    ("review", "text", False, False),
    ("author", "chapter", False, False),
    ("author", "text", True, False),
])
def test_build_only_enables_the_body_writing_author(tmp_path, name, stage, character_repair, enabled):
    save_writing_state(tmp_path, WritingState(confirmed_stage=stage, stage=stage))
    abc = SimpleNamespace(
        agent=SimpleNamespace(name=name, tools=["save_artifact"]),
        trace_name=f"{name}-subagent",
        workspace_path=tmp_path,
        ctx=SimpleNamespace(character_repair=character_repair),
    )
    result = build(abc).after_model({"messages": [_literal()]}, None)
    assert (result is not None) is enabled


@pytest.mark.parametrize("delegates", [None, [], ["review"]])
def test_build_only_recovers_task_when_delegation_is_registered(tmp_path, delegates):
    save_writing_state(tmp_path, WritingState(confirmed_stage="text", stage="text"))
    agent = SimpleNamespace(name="author", tools=["save_artifact"])
    if delegates is not None:
        agent.delegates = delegates
    abc = SimpleNamespace(agent=agent, trace_name="author-subagent", workspace_path=tmp_path)
    message = AIMessage(content='call:default_api:task{subagent_type:"review"}')
    result = build(abc).after_model({"messages": [message]}, None)
    assert (result is not None) is bool(delegates)


def test_manifest_mounts_recovery_only_on_author():
    manifest = json.loads((_HARNESS_DIR / "architecture.json").read_text(encoding="utf-8"))
    mounted = [agent["name"] for agent in manifest["agents"] if "tool_call_recovery" in agent["middleware"]]
    assert mounted == ["author"]
