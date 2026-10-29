"""Transient validation failures remain drafts; only ready volumes may interrupt for review."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.writing.payoff_validation import ValidationEngine
from app.domains.writing.retry import WriterRetryError
from app.domains.writing.volume_payoff_guard import VolumePayoffGuard, bind_volume_operation
from contracts.payoff_constraints import PayoffCatalog, content_hash
from contracts.writing_artifacts import (
    WritingState, save_writing_state, scan_writing_progress, volume_review_block_reason,
)


def volume_text(payload="行动合规"):
    return (
        "# 第01卷\n> 状态：定稿\n本卷章号范围：1-1\n\n"
        "## 发展阶段\n阶段一\n\n"
        f"## 爽点节奏表\n{payload}\n\n"
        "## 爽点质检表\n逐项检查\n\n## 卷末钩子\n后续事件\n"
    )


def verdict(text):
    return {
        "passed": "违规" not in text,
        "candidate_hash": content_hash(text), "policy_hash": "policy",
        "violations": [] if "违规" not in text else [{"message": "内容违规"}],
    }


def temporary_failure(*, retryable=True, partial=False):
    return WriterRetryError(
        attempt_id="check-1", attempts_made=2, error_class="no_response",
        error_message="Request timed out", is_retryable=retryable, had_partial_response=partial,
    )


def guard(workspace, *, check=verdict, repair=lambda text, errors: text.replace("违规", "合规")):
    return VolumePayoffGuard(
        workspace, operation_id="trace-1", policy_hash="policy", check=check, repair=repair,
    )


def test_transient_checker_failure_can_resume_without_accepting_draft(tmp_path):
    candidate = volume_text()

    def timeout(text):
        raise temporary_failure()

    failed = guard(tmp_path, check=timeout).commit(1, "", candidate, "full")
    assert failed["status"] == "retryable"
    assert failed["repair_attempts"] == 0
    assert scan_writing_progress(tmp_path).volumes == []
    assert volume_review_block_reason(tmp_path)
    persisted = json.loads((tmp_path / ".writing/volume-payoff-checks.json").read_text())
    assert persisted["volumes"]["1"]["active_operation"]["candidate_text"] == candidate
    checked = []
    resumed = guard(tmp_path, check=lambda text: checked.append(text) or verdict(text)).commit(
        1, failed["content"], volume_text("新候选不得替换待复核内容"), "full",
    )
    assert resumed["status"] == "committed"
    assert checked == [candidate]
    assert scan_writing_progress(tmp_path).volumes == [1]
    assert volume_review_block_reason(tmp_path) is None


def test_transient_repair_failure_keeps_consumed_repair_budget(tmp_path):
    def timeout(text, errors):
        raise temporary_failure()

    failed = guard(tmp_path, repair=timeout).commit(1, "", volume_text("违规"), "full")
    assert failed["status"] == "retryable"
    assert failed["repair_attempts"] == 1
    calls = []
    resumed = guard(
        tmp_path, repair=lambda text, errors: calls.append(text) or text.replace("违规", "合规"),
    ).commit(1, failed["content"], volume_text("违规"), "full")
    assert resumed["status"] == "committed"
    assert resumed["repair_attempts"] == 2
    assert len(calls) == 1


def test_failed_repair_retries_cannot_refresh_exhausted_budget(tmp_path):
    def timeout(text, errors):
        raise temporary_failure()

    candidate = volume_text("违规")
    first = guard(tmp_path, repair=timeout).commit(1, "", candidate, "full")
    second = guard(tmp_path, repair=timeout).commit(1, first["content"], candidate, "full")
    calls = []
    third = guard(tmp_path, repair=lambda text, errors: calls.append(text) or text).commit(
        1, second["content"], candidate, "full",
    )
    assert third["status"] == "blocked"
    assert third["repair_attempts"] == 2
    assert calls == []


@pytest.mark.parametrize("retryable,partial", [(False, False), (True, True)])
def test_non_retryable_or_partial_failure_stays_blocked(tmp_path, retryable, partial):
    def timeout(text):
        raise temporary_failure(retryable=retryable, partial=partial)

    candidate = volume_text()
    failed = guard(tmp_path, check=timeout).commit(1, "", candidate, "full")
    assert failed["status"] == "blocked"
    assert guard(tmp_path).commit(1, "", candidate, "full") == failed


def test_transport_timeout_is_not_reported_as_content_violation(monkeypatch):
    monkeypatch.setattr(
        "app.domains.writing.payoff_validation.parse_volume_payoffs", lambda text, policy: ([], []),
    )

    def timeout(messages, purpose):
        raise temporary_failure()

    with pytest.raises(WriterRetryError):
        ValidationEngine(timeout).check(volume_text(), PayoffCatalog("ready", "policy"))


@pytest.fixture
def review_module(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "evolution/harnesses/repo/tools/request_review.py"
    spec = importlib.util.spec_from_file_location("guarded_request_review", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_stage_review_rejects_missing_or_draft_volume_without_interrupt(tmp_path, review_module, monkeypatch):
    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    monkeypatch.setattr(review_module, "interrupt", lambda payload: pytest.fail("草稿不能交审"))
    tool = review_module.build_request_review_tool(tmp_path)
    assert "[交审失败]" in tool.func("stage", "卷纲好了")
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(volume_text().replace("状态：定稿", "状态：起草中"), encoding="utf-8")
    assert "[交审失败]" in tool.func("stage", "卷纲好了")


def test_probe_recorder_without_history_queries_keeps_first_review_check(tmp_path, review_module, monkeypatch):
    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    monkeypatch.setattr(review_module, "interrupt", lambda payload: pytest.fail("草稿不能交审"))
    tool = review_module.build(SimpleNamespace(
        workspace_path=tmp_path,
        ctx=SimpleNamespace(trace_id="probe-trace", trace_recorder=SimpleNamespace(append_event=lambda *args: None)),
    ))
    assert "[交审失败]" in tool.func("stage", "卷纲好了")


def test_failed_revision_cannot_be_reviewed_using_previous_accepted_volume(tmp_path, review_module, monkeypatch):
    save_writing_state(tmp_path, WritingState(confirmed_stage="chapter"))
    candidate = volume_text()
    assert guard(tmp_path).commit(1, "", candidate, "full")["status"] == "committed"
    guard(tmp_path, repair=lambda text, errors: text).commit(1, candidate, volume_text("违规"), "full")
    monkeypatch.setattr(review_module, "interrupt", lambda payload: pytest.fail("失败修订不能交审"))
    tool = review_module.build_request_review_tool(tmp_path, trace_id="trace-1")
    assert "[交审失败]" in tool.func("stage", "修订好了")


def test_ready_volume_review_carries_injected_tool_call_identity(tmp_path, review_module):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    assert guard(tmp_path).commit(1, "", volume_text(), "full")["status"] == "committed"
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


def test_reassembled_review_delivers_rejection_after_new_revision(tmp_path, review_module, monkeypatch):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command

    from app.platform.trace.recorder import TraceRecorder
    from app.schemas.screenplay import ThreadSummary

    monkeypatch.setattr("app.platform.trace.recorder._notify_evolution", lambda *args, **kwargs: None)
    thread = ThreadSummary(
        thread_id="rejection-thread", workspace_id="rejection-workspace", session_name="review",
        workspace_path=str(tmp_path), created_at="2026-10-29T00:00:00+00:00",
        updated_at="2026-10-29T00:00:00+00:00",
    )
    recorder = TraceRecorder()
    trace_id = recorder.create_run(thread, "screenplay.generate.stream").trace_id
    operation = bind_volume_operation(tmp_path, trace_id)
    service = VolumePayoffGuard(
        tmp_path, operation_id=operation, policy_hash="policy", check=verdict,
        repair=lambda text, errors: text,
    )
    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    assert service.commit(1, "", volume_text(), "full")["status"] == "committed"
    saver = InMemorySaver()

    def assemble(active_recorder):
        tool = review_module.build(SimpleNamespace(
            workspace_path=tmp_path,
            ctx=SimpleNamespace(trace_id=trace_id, trace_recorder=active_recorder),
        ))
        graph = StateGraph(MessagesState)
        graph.add_node("tools", ToolNode([tool]))
        graph.add_edge(START, "tools")
        graph.add_edge("tools", END)
        return graph.compile(checkpointer=saver)

    first_agent = assemble(recorder)
    call = {"name": "request_review", "id": "review-before-reject",
            "args": {"review_kind": "stage", "question": "请审卷纲"}}
    config = {"configurable": {"thread_id": "rejection-graph"}}
    first = first_agent.invoke({"messages": [AIMessage(content="", tool_calls=[call])]}, config=config)
    assert first["__interrupt__"][0].value["review_id"] == "review-before-reject"
    recorder.append_event(trace_id, {
        "type": "hitl", "status": "awaiting_input", "source": "runtime",
        "tool_call_id": "review-before-reject", "tool_name": "request_review",
    })
    recorder.await_input_run(thread, trace_id)
    bind_volume_operation(tmp_path, trace_id, "rejection-message", new_revision=True)
    assert volume_review_block_reason(tmp_path, operation_id=trace_id)

    second_agent = assemble(TraceRecorder())
    opinion = "打回：调整开篇节奏，保留结尾。"
    resumed = second_agent.invoke(Command(resume=opinion), config=config)
    assert resumed["messages"][-1].content == opinion
    assert not resumed.get("__interrupt__")

    fresh_call = {**call, "id": "review-after-reject"}
    fresh = second_agent.invoke(
        {"messages": [AIMessage(content="", tool_calls=[fresh_call])]},
        config={"configurable": {"thread_id": "fresh-review-graph"}},
    )
    assert "[交审失败]" in fresh["messages"][-1].content
    assert not fresh.get("__interrupt__")


def test_style_and_batch_reviews_do_not_require_volume_final(tmp_path, review_module, monkeypatch):
    save_writing_state(tmp_path, WritingState(confirmed_stage="volume"))
    payloads = []
    monkeypatch.setattr(review_module, "interrupt", lambda payload: payloads.append(payload) or "确认通过")
    tool = review_module.build_request_review_tool(tmp_path)
    for kind in ("style", "batch"):
        assert tool.func(kind, "请审查", runtime=SimpleNamespace(tool_call_id=kind)) == "确认通过"
    assert [payload["review_id"] for payload in payloads] == ["style", "batch"]
