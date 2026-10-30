"""卷纲交审门槛：草稿卷不能打断交审；定稿卷按状态行放行（payoff 校验已移除）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.writing_artifacts import (
    WritingState, save_writing_state, volume_review_block_reason,
)


def volume_text(payload="行动合规"):
    return (
        "# 第01卷\n> 状态：定稿\n本卷章号范围：1-1\n\n"
        "## 发展阶段\n阶段一\n\n"
        f"## 爽点节奏表\n{payload}\n\n"
        "## 爽点质检表\n逐项检查\n\n## 卷末钩子\n后续事件\n"
    )


@pytest.fixture
def review_module(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "evolution/harnesses/repo/tools/request_review.py"
    spec = importlib.util.spec_from_file_location("guarded_request_review", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_block_reason_follows_final_status_line(tmp_path):
    """REQ-20261030-200030：门槛只看定稿状态行（结构/收据校验已移除）。"""
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir(parents=True)
    path.write_text(volume_text().replace("状态：定稿", "状态：起草中"), encoding="utf-8")
    assert volume_review_block_reason(tmp_path) == "第 1 卷尚未定稿。"
    path.write_text(volume_text(), encoding="utf-8")
    assert volume_review_block_reason(tmp_path) is None


def test_stage_review_rejects_missing_or_draft_volume_without_interrupt(tmp_path, review_module, monkeypatch):
    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    monkeypatch.setattr(review_module, "interrupt", lambda payload: pytest.fail("草稿不能交审"))
    tool = review_module.build_request_review_tool(tmp_path)
    assert "[交审失败]" in tool.func("stage", "卷纲好了")
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(volume_text().replace("状态：定稿", "状态：起草中"), encoding="utf-8")
    assert "[交审失败]" in tool.func("stage", "卷纲好了")


def test_ready_volume_review_carries_injected_tool_call_identity(tmp_path, review_module):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(volume_text(), encoding="utf-8")
    tool = review_module.build_request_review_tool(tmp_path, trace_id="trace-1")
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    result = agent.invoke({"messages": [AIMessage(content="", tool_calls=[{
        "name": "request_review", "id": "call-review-1",
        "args": {"review_kind": "stage", "question": "卷纲已定稿"},
    }])]}, config={"configurable": {"thread_id": "review-test"}})
    payload = result["__interrupt__"][0].value
    assert payload["kind"] == "review"
    assert payload["review_kind"] == "stage"
    assert payload["review_id"] == "call-review-1"


def test_resumed_review_delivers_opinion_without_second_interrupt(tmp_path, review_module):
    """打回后的恢复会话：作者意见直接回给模型，不二次打断（resuming_review_ids 跳过门槛）。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command

    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(volume_text(), encoding="utf-8")

    def compile_with(tool):
        graph = StateGraph(MessagesState)
        graph.add_node("tools", ToolNode([tool]))
        graph.add_edge(START, "tools")
        graph.add_edge("tools", END)
        return graph.compile(checkpointer=saver)

    call = {"name": "request_review", "id": "review-1",
            "args": {"review_kind": "stage", "question": "请审卷纲"}}
    config = {"configurable": {"thread_id": "resume-test"}}
    saver = InMemorySaver()

    first = compile_with(review_module.build_request_review_tool(tmp_path)).invoke(
        {"messages": [AIMessage(content="", tool_calls=[call])]}, config=config,
    )
    assert first["__interrupt__"][0].value["review_kind"] == "stage"

    # 作者打回后重修：卷纲回到起草中，恢复会话重组装（带 resuming_review_ids）
    # 必须把意见送回模型，而不是被「尚未定稿」门槛再次拦下。
    # ToolRuntime 注入在当前依赖组合下不可用（已知环境缺陷），包装层显式
    # 传 tool_call_id，保证测试确定性。
    path.write_text(volume_text().replace("状态：定稿", "状态：起草中"), encoding="utf-8")
    from langchain_core.tools import StructuredTool

    inner = review_module.build_request_review_tool(
        tmp_path, resuming_review_ids={"review-1"},
    ).func
    resumed_tool = StructuredTool.from_function(
        name="request_review", description="",
        func=lambda review_kind, question, options=None: inner(
            review_kind, question, options, SimpleNamespace(tool_call_id="review-1"),
        ),
        args_schema=review_module.RequestReviewInput, infer_schema=False,
    )
    resumed = compile_with(resumed_tool).invoke(
        Command(resume="打回：调整开篇节奏"), config=config,
    )
    assert resumed["messages"][-1].content == "打回：调整开篇节奏"
    assert not resumed.get("__interrupt__")


def test_style_and_batch_reviews_do_not_require_volume_final(tmp_path, review_module, monkeypatch):
    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    payloads = []
    monkeypatch.setattr(review_module, "interrupt", lambda payload: payloads.append(payload) or "确认通过")
    tool = review_module.build_request_review_tool(tmp_path)
    for kind in ("style", "batch"):
        assert tool.func(kind, "请审查", runtime=SimpleNamespace(tool_call_id=kind)) == "确认通过"
    assert [payload["review_id"] for payload in payloads] == ["style", "batch"]
