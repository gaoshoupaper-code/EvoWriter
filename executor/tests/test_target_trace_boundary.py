"""Untrusted trace indexes cannot make the parent read outside a task."""
from __future__ import annotations

import json

from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


def test_external_trace_index_cannot_point_at_another_workspace(tmp_path):
    workspace = tmp_path / "own"
    thread = ThreadSummary(thread_id="own", workspace_id="own", session_name="own",
                           workspace_path=str(workspace), created_at="now", updated_at="now")
    worker_recorder = TraceRecorder()
    trace_id = worker_recorder.create_run(thread, "target").trace_id
    index = workspace / "traces/index.json"
    records = json.loads(index.read_text(encoding="utf-8"))
    records[trace_id]["path"] = str(tmp_path / "other/traces/private.jsonl")
    index.write_text(json.dumps(records), encoding="utf-8")
    parent_recorder = TraceRecorder()
    assert not parent_recorder.register_external_run(trace_id, str(workspace))
