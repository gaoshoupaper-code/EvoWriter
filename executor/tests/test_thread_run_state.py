"""run-state 恢复对账端点测试（D-对账）。

轻量 TestClient（只挂 threads 路由 + 依赖覆盖），锁定：
  - 无 run → status="idle"；非 awaiting 终态原样返回、awaiting=None。
  - awaiting run → 从 hitl/tool_start 事件重建 question/options/multi_select。
  - 事件形态不符（无 question，如图像评审）→ awaiting=None 宁缺毋滥。
  - thread 不存在 → 404。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.platform.trace import TraceDetail, TraceRunSummary
from app.platform.trace.recorder import TraceLogEvent
from app.routers import threads as threads_router


def _run(trace_id: str, status: str) -> TraceRunSummary:
    return TraceRunSummary(
        trace_id=trace_id,
        workspace_id="ws1",
        thread_id="t1",
        session_name="s",
        workspace_path="/tmp/ws",
        endpoint="screenplay.generate.stream",
        status=status,
        started_at="2026-10-18T20:16:00+00:00",
        path=f"traces/20261018-2016/{trace_id}.jsonl",
    )


def _event(seq: int, type_: str, **extra) -> TraceLogEvent:
    base = dict(
        trace_id="trace-1",
        event_id=f"trace-1-{seq}",
        sequence=seq,
        type=type_,
        status=extra.pop("status", "running"),
        timestamp=f"2026-10-18T20:{10 + seq:02d}:00+00:00",
        source="runtime",
    )
    base.update(extra)
    return TraceLogEvent.model_validate(base)


class _FakeStore:
    def __init__(self) -> None:
        self._thread = SimpleNamespace(
            thread_id="t1", workspace_id="ws1", workspace_path="/tmp/ws"
        )

    def get_thread(self, owner_id: str, thread_id: str):
        return self._thread if thread_id == "t1" else None


class _FakeRecorder:
    """按测试意图构造 list_runs / read_run 返回值。"""

    def __init__(self, runs, detail=None) -> None:
        self._runs = runs
        self._detail = detail

    def list_runs(self, thread):
        return sorted(self._runs, key=lambda r: r.started_at, reverse=True)

    def read_run(self, thread, trace_id):
        return self._detail


def _client(recorder) -> TestClient:
    app = FastAPI()
    app.include_router(threads_router.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    threads_router.get_thread_store = lambda: _FakeStore()  # type: ignore[assignment]
    threads_router.get_trace_recorder = lambda: recorder  # type: ignore[assignment]
    return TestClient(app)


_AWAITING_EVENTS = [
    _event(8, "tool_start", status="running", tool_call_id="call-x",
           tool_name="confirm_with_user", agent_name="author-subagent",
           tool_args={"question": "选个方向", "options": [
               {"label": "A", "description": "打脸"},
               {"label": "B", "description": "搞笑"},
           ], "multi_select": True}),
    _event(9, "hitl", status="awaiting_input", tool_call_id="call-x",
           agent_name="author-subagent"),
]


def _detail(events) -> TraceDetail:
    return TraceDetail(run=_run("trace-1", "awaiting_input"), events=events,
                       nodes=[], context=[], todos=[])


def test_no_runs_returns_idle():
    resp = _client(_FakeRecorder([], None)).get("/threads/t1/run-state")
    assert resp.status_code == 200
    assert resp.json() == {"trace_id": None, "status": "idle", "awaiting": None}


def test_terminal_status_passes_through():
    rec = _FakeRecorder([_run("trace-1", "completed")], None)
    resp = _client(rec).get("/threads/t1/run-state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trace_id"] == "trace-1"
    assert body["status"] == "completed"
    assert body["awaiting"] is None


def test_awaiting_reconstructs_question_and_options():
    rec = _FakeRecorder([_run("trace-1", "awaiting_input")], _detail(_AWAITING_EVENTS))
    resp = _client(rec).get("/threads/t1/run-state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_input"
    awaiting = body["awaiting"]
    assert awaiting is not None
    assert awaiting["kind"] == "choice"
    assert awaiting["question"] == "选个方向"
    assert awaiting["multi_select"] is True
    assert [o["label"] for o in awaiting["options"]] == ["A", "B"]
    assert awaiting["source"] == "author-subagent-confirm"
    assert awaiting["asked_at"]


def test_awaiting_without_question_degrades_to_none():
    """tool_args 无 question（图像评审等形态）→ awaiting=None，不误渲染拍板卡。"""
    events = [
        _event(3, "tool_start", status="running", tool_call_id="call-y",
               tool_name="ask_image_review",
               tool_args={"round": 1, "versions": []}),
        _event(4, "hitl", status="awaiting_input", tool_call_id="call-y"),
    ]
    rec = _FakeRecorder([_run("trace-1", "awaiting_input")], _detail(events))
    resp = _client(rec).get("/threads/t1/run-state")
    body = resp.json()
    assert body["status"] == "awaiting_input"
    assert body["awaiting"] is None


@pytest.mark.parametrize("review_kind", ["batch", "style", "stage"])
def test_restored_review_preserves_kind_and_current_review_id(review_kind):
    """同一 trace 多次交审后，只恢复最后一张审查卡及独立幂等编号。"""
    events = [
        *_AWAITING_EVENTS,
        _event(10, "tool_start", tool_call_id="review-old", tool_name="request_review",
               tool_args={"review_kind": "stage", "question": "卷纲已完成"}),
        _event(11, "hitl", status="awaiting_input", tool_call_id="review-old"),
        _event(12, "tool_start", tool_call_id="review-current", tool_name="request_review",
               agent_name="author", tool_args={"review_kind": review_kind, "question": "本批已完成"}),
        _event(13, "hitl", status="awaiting_input", tool_call_id="review-current"),
    ]
    rec = _FakeRecorder([_run("trace-1", "awaiting_input")], _detail(events))
    resp = _client(rec).get("/threads/t1/run-state")
    assert resp.status_code == 200
    awaiting = resp.json()["awaiting"]
    assert awaiting["kind"] == "review"
    assert awaiting["review_kind"] == review_kind
    assert awaiting["review_id"] == "review-current"
    assert awaiting["source"] == "review"
    assert awaiting["question"] == "本批已完成"
    assert awaiting["multi_select"] is False


def test_restored_review_without_kind_does_not_become_choice():
    events = [
        _event(1, "tool_start", tool_call_id="bad-review", tool_name="request_review",
               tool_args={"question": "待审产物"}),
        _event(2, "hitl", status="awaiting_input", tool_call_id="bad-review"),
    ]
    rec = _FakeRecorder([_run("trace-1", "awaiting_input")], _detail(events))
    assert _client(rec).get("/threads/t1/run-state").json()["awaiting"] is None


def test_thread_not_found_404():
    resp = _client(_FakeRecorder([], None)).get("/threads/other/run-state")
    assert resp.status_code == 404
