"""Experiment worker traces remain discoverable before completion."""
from app.platform.trace.recorder import TraceRecorder
from app.platform.trace.recorder import observation_context
from app.schemas.screenplay import ThreadSummary


def test_external_worker_active_trace_and_experiment_refs_are_visible(tmp_path):
    worker = TraceRecorder()
    parent = TraceRecorder()
    thread = ThreadSummary(thread_id="sample-1", workspace_id="exp-workspace", session_name="sample",
                           workspace_path=str(tmp_path), created_at="now", updated_at="now")
    handle = worker.create_run(thread, "generate", "optimization",
                               external_refs_extra={"experiment_id": "exp-1", "sample_id": "sample-1", "task_id": "sample-1"})
    assert parent.register_external_run(handle.trace_id, str(tmp_path))
    rows = parent.list_active_runs()
    assert [row["trace_id"] for row in rows] == [handle.trace_id]
    assert rows[0]["run_purpose"] == "optimization"
    assert rows[0]["external_refs"]["experiment_id"] == "exp-1"
    assert rows[0]["metrics"] is None
    worker.append_event(handle.trace_id, {"type": "llm_end", "status": "completed", "source": "runtime",
                                         "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}})
    assert parent.list_active_runs()[0]["metrics"]["total_tokens"] == 15
    worker.complete_run(thread, handle.trace_id)
    assert parent.list_active_runs() == []


def test_experiment_identity_is_created_before_first_model_call_and_does_not_leak(tmp_path):
    recorder = TraceRecorder()
    thread = ThreadSummary(thread_id="sample-1", workspace_id="exp-workspace", session_name="sample",
                           workspace_path=str(tmp_path), created_at="now", updated_at="now")
    with observation_context({"experiment_id": "exp-1", "sample_id": "sample-1", "task_id": "sample-1"}):
        experiment = recorder.create_run(thread, "generate", "optimization")
    user = recorder.create_run(thread, "generate")
    rows = {row["trace_id"]: row for row in recorder.list_active_runs()}
    assert rows[experiment.trace_id]["external_refs"]["experiment_id"] == "exp-1"
    assert "experiment_id" not in rows[user.trace_id]["external_refs"]
