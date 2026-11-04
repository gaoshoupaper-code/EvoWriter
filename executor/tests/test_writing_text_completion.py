"""正文交付门：真实保存、子代理审稿、并行工具与有界收尾。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from app.platform.agent.loader import load_package
from contracts.writing_artifacts import WritingState, save_writing_state

_HARNESS_DIR = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH",
    str(Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"),
))
load_package(_HARNESS_DIR)

from harness_current.middleware.revision_limit import RevisionLimitMiddleware  # noqa: E402
from harness_current.middleware.text_completion import TextCompletionMiddleware  # noqa: E402
from harness_current.middleware.tool_call_recovery import ToolCallRecoveryMiddleware  # noqa: E402
from harness_current.tools.read_artifact import build_read_artifact_tool  # noqa: E402
from harness_current.tools.request_review import build_request_review_tool  # noqa: E402
from harness_current.tools.save_artifact import build_save_artifact_tool  # noqa: E402


class _Model(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0

    @property
    def _llm_type(self):
        return "text-completion-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        assert self.calls < len(self.responses), "unexpected extra model call"
        message = self.responses[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def _call(name, call_id, **args):
    return {"name": name, "id": call_id, "args": args}


def _ai(*calls):
    return AIMessage(content="", tool_calls=list(calls))


def _save(call_id, *, chapter=1, status="初稿", mode="full", **kwargs):
    args = {"kind": "chapter_text", "chapter": chapter, "mode": mode}
    if mode == "full":
        args.update(content=f"# 第{chapter:03}章\n> 状态：{status}\n\n沈砚把旧票压在柜上。", unit_kind="ordinary")
    args.update(kwargs)
    return _call("save_artifact", call_id, **args)


def _review(call_id="review-1", chapter=1):
    return _call("task", call_id, subagent_type="review", description=f"审第{chapter}章")


def _deliver(call_id="deliver"):
    return _call("request_review", call_id, review_kind="batch", question="请审阅正文。")


def _agent(root, model, *, saver=None, review=None, cap=1):
    save_writing_state(root, WritingState(confirmed_stage="text", stage="text"))
    registry = {}
    tools = [
        build_save_artifact_tool(root, registry, {}, writing_context="现代正文规程", enabled_skill_names=set()),
        build_read_artifact_tool(root, registry),
        build_request_review_tool(root, enforce_text_completion=True),
    ]
    reviewer = review or _Model(responses=[AIMessage(content="通过，无修改意见。")])
    limit = RevisionLimitMiddleware(max_revisions=cap, workspace_root=root)
    graph = create_deep_agent(
        model=model, tools=tools,
        subagents=[{"name": "review", "description": "整章审稿", "model": reviewer,
                    "system_prompt": "只审不改。", "tools": []}],
        middleware=[TextCompletionMiddleware(root),
                    ToolCallRecoveryMiddleware(["save_artifact", "read_artifact", "request_review", "task"]), limit],
        checkpointer=saver or InMemorySaver(),
    )
    return graph, reviewer, limit


def _config(thread="completion"):
    return {"configurable": {"thread_id": thread}, "recursion_limit": 150}


def test_real_async_graph_rejects_early_delivery_and_review_without_spending_quota(tmp_path):
    async def run():
        model = _Model(responses=[
            _ai(_save("new", status="起草中")), _ai(_deliver("early-deliver")),
            _ai(_call("request_review", "early-stage", review_kind="stage", question="库存耗尽，先交审。")),
            _ai(_review("early-review")),
            _ai(_save("complete", mode="edit", anchor="> 状态：起草中", replacement="> 状态：初稿")),
            _ai(_review()),
            _ai(_save("revision", mode="edit", anchor="沈砚把旧票压在柜上。", replacement="沈砚将旧票压在柜上。")),
            _ai(_deliver()),
        ])
        graph, review, limit = _agent(tmp_path, model)
        config = _config()
        result = await graph.ainvoke({"messages": [HumanMessage(content="写第1章", id="turn-1")]}, config)
        state = (await graph.aget_state(config)).values
        replies = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}

        assert "[交审失败]" in replies["early-deliver"].content
        assert "[交审失败]" in replies["early-stage"].content
        assert "[审稿拒绝]" in replies["early-review"].content
        assert review.calls == 1
        assert limit._revision_count == 1
        assert replies["review-1"].response_metadata["writer_text"]["unit"] == "new"
        assert state["text_completion"]["chapters"]["1"]["review"] == "success"
        assert "沈砚将旧票" in (tmp_path / "正文/第001章.md").read_text(encoding="utf-8")
        assert result["__interrupt__"][0].value["review_id"] == "deliver"

    asyncio.run(run())


def test_parallel_save_read_and_early_task_fold_once_without_state_write_conflicts(tmp_path):
    model = _Model(responses=[
        _ai(_save("chapter-1"), _save("chapter-2", chapter=2),
            _call("read_artifact", "read", kind="chapter_text", chapter=1), _review("early")),
        _ai(_review("review-1", 1)), _ai(_review("review-2", 2)), _ai(_deliver()),
    ])
    review = _Model(responses=[AIMessage(content="第1章通过。"), AIMessage(content="第2章通过。")])
    graph, review, limit = _agent(tmp_path, model, review=review, cap=2)
    config = _config("parallel")
    result = graph.invoke({"messages": [HumanMessage(content="写两章", id="turn-1")]}, config)
    state = graph.get_state(config).values
    replies = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}

    assert "[审稿拒绝]" in replies["early"].content
    assert review.calls == 2
    assert limit._revision_count == 2
    chapters = state["text_completion"]["chapters"]
    assert set(chapters) == {"1", "2"}
    assert all(entry["review"] == "success" for entry in chapters.values())
    assert replies["review-1"].response_metadata["writer_text"]["chapter"] == 1
    assert replies["review-2"].response_metadata["writer_text"]["chapter"] == 2
    assert result["__interrupt__"][0].value["review_id"] == "deliver"


def test_new_unit_after_review_invalidates_delivery_without_automatic_second_review(tmp_path):
    model = _Model(responses=[
        _ai(_save("new")), _ai(_review()),
        _ai(_save("append", mode="append", content="他收起船票。", unit_kind="ordinary")),
        _ai(_deliver("stale")),
        AIMessage(content="已审稿，全部完成。"), AIMessage(content="可以交付了。"), AIMessage(content="终稿完成。"),
    ])
    graph, review, limit = _agent(tmp_path, model)
    config = _config("stale")
    result = graph.invoke({"messages": [HumanMessage(content="写正文", id="turn-1")]}, config)
    state = graph.get_state(config).values
    entry = state["text_completion"]["chapters"]["1"]

    assert review.calls == 1
    assert limit._revision_count == 1
    assert entry["unit"] == "append"
    assert entry["review"] is None
    assert "正文交付未完成" in result["messages"][-1].content
    assert state["text_completion_output_count"] == 2
    assert not result.get("__interrupt__")


def test_natural_final_is_bounded_but_new_user_chat_does_not_force_writing(tmp_path):
    saver = InMemorySaver()
    model = _Model(responses=[_ai(_save("new")), *[AIMessage(content="审稿修订均已完成。") for _ in range(3)]])
    graph, review, _ = _agent(tmp_path, model, saver=saver)
    config = _config("chat")
    result = graph.invoke({"messages": [HumanMessage(content="写正文", id="turn-1")]}, config)
    assert model.calls == 4
    assert review.calls == 0
    assert "正文交付未完成" in result["messages"][-1].content

    chat = _Model(responses=[AIMessage(content="第1章已保存，尚未审稿。")])
    reopened, _, _ = _agent(tmp_path, chat, saver=saver)
    reply = reopened.invoke({"messages": [HumanMessage(content="写到哪了？", id="turn-2")]}, config)
    assert chat.calls == 1
    assert reply["messages"][-1].content == "第1章已保存，尚未审稿。"


def test_literal_call_exhaustion_does_not_gain_another_two_completion_retries(tmp_path):
    literal = 'call:default_api:save_artifact{kind:"chapter_text",chapter:1}'
    model = _Model(responses=[_ai(_save("new")), *[AIMessage(content=literal) for _ in range(3)]])
    graph, review, _ = _agent(tmp_path, model)
    config = _config("literal")
    result = graph.invoke({"messages": [HumanMessage(content="写正文", id="turn-1")]}, config)
    state = graph.get_state(config).values

    assert model.calls == 4
    assert review.calls == 0
    assert state["tool_call_recovery_count"] == 2
    assert not state.get("text_completion_output_count")
    assert "工具调用未执行" in result["messages"][-1].content
