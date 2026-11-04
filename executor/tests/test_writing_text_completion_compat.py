"""正文交付门：旧调用、真实失败、消息压缩及审批恢复的图回归。"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from app.platform.agent.loader import load_package
from contracts.writing_artifacts import WritingState, save_writing_state

_HARNESS_DIR = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH",
    str(Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"),
))
load_package(_HARNESS_DIR, mod_name="harness_text_compat")

from harness_text_compat.middleware.revision_limit import RevisionLimitMiddleware  # noqa: E402
from harness_text_compat.middleware.text_completion import TextCompletionMiddleware, build  # noqa: E402
from harness_text_compat.text_completion import FAILURE_NOTE, completion_snapshot, delivery_block_reason  # noqa: E402
from harness_text_compat.tools.read_artifact import build_read_artifact_tool  # noqa: E402
from harness_text_compat.tools.request_review import build_request_review_tool  # noqa: E402
from harness_text_compat.tools.save_artifact import build_save_artifact_tool  # noqa: E402


HEADER = "# 第001章 · 夜航\n> 状态：初稿｜2026-11-04\n> 进度：已落 1 批\n\n"
REL = "正文/第001章.md"
USER = HumanMessage(content="完成本章", id="turn-1")


class _Model(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0
    fail: bool = False

    @property
    def _llm_type(self):
        return "text-completion-compat"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("review unavailable")
        assert self.calls <= len(self.responses), "unexpected additional model call"
        return ChatResult(generations=[ChatGeneration(message=self.responses[self.calls - 1])])


def _call(name, identity, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "id": identity, "args": args}])


def _save(identity="save-1", chapter=1):
    return _call("save_artifact", identity, kind="chapter_text", chapter=chapter, mode="full",
                 content=HEADER.replace("第001章", f"第{chapter:03d}章") + "已有完整正文。", unit_kind="ordinary")


def _review(identity="review-1", description="审第1章"):
    return _call("task", identity, subagent_type="review", description=description)


def _save_tool(root, *, context="正文规程"):
    registry = {}
    if (root / REL).exists():
        build_read_artifact_tool(root, registry).invoke({"kind": "chapter_text", "chapter": 1})
    return build_save_artifact_tool(root, registry, {}, writing_context=context, enabled_skill_names=set())


def _task(attempts, *, failure=None, artifact=None):
    def task(subagent_type: str, description: str, runtime: ToolRuntime):
        """实际执行本次指定章节的审稿。"""
        attempts.append(description)
        if failure == "exception":
            raise RuntimeError("review unavailable")
        message = ToolMessage(content="审稿失败" if failure else "通过，无修改意见",
                              status="error" if failure else "success", artifact=artifact,
                              tool_call_id=runtime.tool_call_id)
        return Command(update={"messages": [message]})
    return task


def _agent(root, model, attempts, *, checkpointer=None, failure=None, artifact=None, cap=1):
    return create_agent(
        model=model,
        tools=[_save_tool(root), _task(attempts, failure=failure, artifact=artifact),
               build_request_review_tool(root, enforce_text_completion=True)],
        middleware=[TextCompletionMiddleware(root), RevisionLimitMiddleware(max_revisions=cap, workspace_root=root)],
        checkpointer=checkpointer or InMemorySaver(),
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_old_edit_only_and_legacy_save_do_not_create_a_new_review_obligation(tmp_path, legacy):
    if not legacy:
        path = tmp_path / REL
        path.parent.mkdir(parents=True)
        path.write_text(HEADER + "旧句。", encoding="utf-8")
    message = (_save() if legacy else
               _call("save_artifact", "edit-old", kind="chapter_text", chapter=1,
                     mode="edit", anchor="旧句。", replacement="新句。"))
    model = _Model(responses=[message, AIMessage(content="旧调用处理完成。")])
    agent = create_agent(model=model, tools=[_save_tool(tmp_path, context=None if legacy else "正文规程")],
                         middleware=[TextCompletionMiddleware(tmp_path)], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "compat"}}

    result = agent.invoke({"messages": [USER]}, config)

    assert result["messages"][-1].content == "旧调用处理完成。"
    assert model.calls == 2
    state = agent.get_state(config).values
    assert completion_snapshot(state, tmp_path)["chapters"] == {}
    assert delivery_block_reason(state, tmp_path) is None


@pytest.mark.parametrize("variant", ["legacy_workspace", "non_text", "character_repair", "no_workspace"])
def test_inapplicable_author_contexts_pass_through_the_real_graph(tmp_path, variant):
    save_writing_state(tmp_path, WritingState(confirmed_stage="chapter" if variant == "non_text" else "text"))
    if variant == "legacy_workspace":
        (tmp_path / "storyline.md").write_text("旧结构", encoding="utf-8")
    abc = SimpleNamespace(agent=SimpleNamespace(name="author"), workspace_path=tmp_path,
                          ctx=SimpleNamespace(character_repair=variant == "character_repair"))
    middleware = TextCompletionMiddleware() if variant == "no_workspace" else build(abc)
    model = _Model(responses=[AIMessage(content="照常返回。")])

    result = create_agent(model=model, middleware=[middleware]).invoke({"messages": [USER]})

    assert middleware.root is None
    assert result["messages"][-1].content == "照常返回。"
    assert model.calls == 1


@pytest.mark.parametrize("failure", ["exception", "error_message"])
def test_actual_review_failure_is_attempted_once_and_disclosed_in_both_delivery_paths(tmp_path, failure):
    attempts = []
    model = _Model(responses=[_save(), _review(), _review("review-retry"), AIMessage(content="交付当前稿。"),
                              _call("request_review", "delivery", review_kind="batch", question="第1章已完成。")])
    agent = _agent(tmp_path, model, attempts, failure=failure)
    config = {"configurable": {"thread_id": "failure"}}

    result = agent.invoke({"messages": [USER]}, config)

    assert attempts == ["审第1章"]
    assert FAILURE_NOTE in result["messages"][-1].content
    assert completion_snapshot(agent.get_state(config).values, tmp_path)["chapters"]["1"]["review"] == "failed"
    delivered = agent.invoke({"messages": []}, config)
    assert FAILURE_NOTE in delivered["__interrupt__"][0].value["question"]
    assert attempts == ["审第1章"]


def test_review_only_followup_turn_discloses_its_real_failure_in_natural_delivery(tmp_path):
    attempts = []
    model = _Model(responses=[_save(), _review(), AIMessage(content="交付当前稿。")])
    agent = _agent(tmp_path, model, attempts, failure="exception")
    config = {"configurable": {"thread_id": "review-only-followup"}}
    agent.invoke({"messages": [USER]}, config, interrupt_after=["tools"])
    before = (tmp_path / REL).read_bytes()
    assert attempts == []

    result = agent.invoke({"messages": [HumanMessage(content="审稿后交付已有初稿。", id="turn-2")]}, config)

    assert attempts == ["审第1章"]
    assert model.calls == 3
    assert FAILURE_NOTE in result["messages"][-1].content
    assert (tmp_path / REL).read_bytes() == before
    assert sum(isinstance(m, ToolMessage) and m.name == "save_artifact" for m in result["messages"]) == 1


@pytest.mark.parametrize("failed", [False, True])
def test_sqlite_reopen_and_message_compaction_keep_real_save_and_review_facts(tmp_path, failed):
    old_chapter = tmp_path / "正文/第005章.md"
    old_chapter.parent.mkdir(parents=True)
    old_chapter.write_text(HEADER.replace("第001章", "第005章") + "最初已经存在的旧稿。", encoding="utf-8")
    database = str(tmp_path / "checkpoint.sqlite")
    config = {"configurable": {"thread_id": "persisted"}}
    reviewer = _Model(responses=[AIMessage(content="通过，无修改意见")] * 2, fail=failed)

    def compile_agent(model, saver):
        return create_deep_agent(
            model=model, tools=[_save_tool(tmp_path)],
            subagents=[{"name": "review", "description": "审稿", "system_prompt": "只审不改。",
                        "model": reviewer, "tools": []}],
            middleware=[TextCompletionMiddleware(tmp_path), RevisionLimitMiddleware(workspace_root=tmp_path)],
            checkpointer=saver,
        )

    with SqliteSaver.from_conn_string(database) as saver:
        agent = compile_agent(_Model(responses=[_save(), _review(), AIMessage(content="完成。")]), saver)
        agent.invoke({"messages": [USER]}, config)
        state = agent.get_state(config).values
        entry = completion_snapshot(state, tmp_path)["chapters"]["1"]
        assert entry["review"] == ("failed" if failed else "success")
        # 压缩移除全部旧消息，凭证只能靠真实 checkpoint 私有状态恢复。
        agent.update_state(config, {"messages": [
            *[RemoveMessage(id=message.id) for message in state["messages"] if message.id],
            AIMessage(content="历史已压缩。", id="summary"),
        ]})
    assert reviewer.calls == 1

    with SqliteSaver.from_conn_string(database) as saver:
        model = _Model(responses=[_review("retry-after-reopen"), AIMessage(content="沿用已有结果。")])
        agent = compile_agent(model, saver)
        result = agent.invoke({"messages": []}, config)
        restored = completion_snapshot(agent.get_state(config).values, tmp_path)

        assert reviewer.calls == 1
        assert restored["chapters"]["1"]["unit"] == "save-1"
        assert restored["chapters"]["1"]["review"] == entry["review"]
        assert delivery_block_reason(agent.get_state(config).values, tmp_path) is None
        assert model.calls == 2
        if failed:
            assert FAILURE_NOTE in result["messages"][-1].content
        else:
            # 真正追加一个未审单元后暂停，下一轮只审初始旧稿。
            pending = compile_agent(_Model(responses=[_call(
                "save_artifact", "save-pending", kind="chapter_text", chapter=1,
                mode="append", content="新追加尚未审稿的单元。", unit_kind="ordinary",
            )]), saver)
            pending.invoke({"messages": [HumanMessage(content="补写第1章。", id="turn-write")]}, config,
                           interrupt_after=["tools"])
            assert completion_snapshot(pending.get_state(config).values, tmp_path)["chapters"]["1"]["review"] is None
            old_model = _Model(responses=[_review("old-review", description="审第5章"), AIMessage(content="旧稿审毕。")])
            compile_agent(old_model, saver).invoke(
                {"messages": [HumanMessage(content="只核对旧稿第5章。", id="turn-2")]}, config,
            )
            assert reviewer.calls == 2


@pytest.mark.parametrize("artifact", ["opaque-review-evidence", ["opaque", {"page": 1}]])
def test_review_preserves_non_mapping_artifact_and_records_native_metadata(tmp_path, artifact):
    attempts = []
    model = _Model(responses=[_save(), _review(), AIMessage(content="完成。")])
    agent = _agent(tmp_path, model, attempts, artifact=artifact)

    result = agent.invoke({"messages": [USER]}, {"configurable": {"thread_id": "artifact"}})

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "review-1")
    assert message.artifact == artifact
    assert message.response_metadata["writer_text"]["event"] == "review"
    assert message.response_metadata["writer_text"]["chapter"] == 1
    assert message.response_metadata["writer_text"]["outcome"] == "success"


def test_ambiguous_and_wrong_chapter_reviews_do_not_certify_another_chapter_or_consume_quota(tmp_path):
    attempts = []
    model = _Model(responses=[
        _save(), _save("save-2", chapter=2), _review("ambiguous", description="请审稿"),
        _review("multi", description="审第1章和第2章"), _review(),
        _call("request_review", "early-delivery", review_kind="batch", question="两章已完成。"),
        _review("review-2", description="审第2章"), AIMessage(content="两章完成。"),
    ])
    agent = _agent(tmp_path, model, attempts, cap=2)
    config = {"configurable": {"thread_id": "chapters"}}

    result = agent.invoke({"messages": [USER]}, config)

    assert attempts == ["审第1章", "审第2章"]
    replies = {m.tool_call_id:m for m in result["messages"] if isinstance(m, ToolMessage)}
    assert replies["review-1"].response_metadata["writer_text"]["chapter"] == 1
    assert "第2章" in replies["early-delivery"].content
    assert "交审失败" in replies["early-delivery"].content
    assert not result.get("__interrupt__")
    chapters = completion_snapshot(agent.get_state(config).values, tmp_path)["chapters"]
    assert chapters["1"]["review"] == chapters["2"]["review"] == "success"


def _tools_graph(tool, saver):
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile(checkpointer=saver)


def test_already_pending_hitl_returns_author_opinion_before_applying_the_new_gate(tmp_path):
    save_call = _save().tool_calls[0]
    save_call["args"]["content"] = HEADER.replace("初稿", "起草中") + "未完稿。"
    saved = _save_tool(tmp_path).invoke({"type": "tool_call", **save_call})
    review_call = _call("request_review", "approved-delivery", review_kind="batch", question="旧审批。").tool_calls[0]
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "approval"}}
    first = _tools_graph(build_request_review_tool(tmp_path), saver).invoke({"messages": [
        USER, AIMessage(content="", tool_calls=[save_call]), saved,
        AIMessage(content="", tool_calls=[review_call]),
    ]}, config)
    assert first["__interrupt__"][0].value["review_id"] == "approved-delivery"
    state = _tools_graph(build_request_review_tool(tmp_path), saver).get_state(config).values
    assert delivery_block_reason(state, tmp_path) is not None

    resumed = _tools_graph(build_request_review_tool(
        tmp_path, enforce_text_completion=True, resuming_review_ids={"approved-delivery"},
    ), saver).invoke(Command(resume="打回：保留前文，调整结尾。"), config)

    assert not resumed.get("__interrupt__")
    assert resumed["messages"][-1].content == "打回：保留前文，调整结尾。"


def test_request_review_without_tool_runtime_keeps_legacy_interrupt_behavior(tmp_path):
    tool = build_request_review_tool(tmp_path, enforce_text_completion=True)
    graph = StateGraph(MessagesState)

    def ask(state):
        return {"messages": [AIMessage(content=tool.func("batch", "旧调用交审。", runtime=None))]}

    graph.add_node("ask", ask)
    graph.add_edge(START, "ask")
    graph.add_edge("ask", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "no-runtime"}}

    first = agent.invoke({"messages": [USER]}, config)
    resumed = agent.invoke(Command(resume="确认旧调用。"), config)

    assert first["__interrupt__"][0].value["question"] == "旧调用交审。"
    assert resumed["messages"][-1].content == "确认旧调用。"
