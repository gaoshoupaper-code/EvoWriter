import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.experiments import runtime_evidence
from contracts.trace import TraceLogEvent, TraceUsage


RECOVER_TRACE = runtime_evidence._recover_trace


@pytest.fixture
def trace(monkeypatch):
    sample = {
        "id": "sample-1", "task_id": "sample-1-a2", "source_commit": "candidate",
        "input_revision": "input", "artifact_digest": "digest", "target": {"chapter": 1},
        "trace_ids": ["submitted"], "evaluation_trace_ids": ["judge"],
        "generation_retries": [{"trace_ids": ["failed"], "usage": {"total_tokens": 30000}}],
    }
    row = {"service": "executor", "integrity_status": "verified", "status": "completed",
           "run_snapshot_json": json.dumps({"target_task": {
               key: sample[key] for key in ("task_id", "source_commit", "input_revision", "artifact_digest", "target")}})}
    events = []
    seen = []

    def add(kind, status="completed", **fields):
        sequence = len(events) + 1
        events.append(TraceLogEvent(trace_id="submitted", event_id=f"event-{sequence}", sequence=sequence,
                                    type=kind, status=status, source="runtime", agent_name="text-subagent",
                                    timestamp="2026-11-22T00:00:00Z", **fields))
        return events[-1]

    def query_one(sql, params):
        seen.append(params[0])
        return row if params[0] == "submitted" else None

    def query_all(sql, params):
        seen.append(params[0])
        return [{"payload_json": event.model_dump_json()} for event in events] if params[0] == "submitted" else []

    monkeypatch.setattr(runtime_evidence.db, "query_one", query_one)
    monkeypatch.setattr(runtime_evidence.db, "query_all", query_all)
    monkeypatch.setattr(runtime_evidence, "_recover_trace", lambda trace_id, bound_sample: None)
    add("run_start", "running")
    add("llm_start", "running", run_id="llm")
    add("llm_end", run_id="llm", usage={"input_tokens": 40000, "output_tokens": 20000, "total_tokens": 60000})
    return sample, row, events, seen, add


def tool(add, call, *, result=None, status="completed", name="task"):
    add("tool_start", "running", tool_call_id=call, tool_name=name)
    return add("tool_end", status, tool_call_id=call, tool_name=name, tool_output=result or {"content": "审稿通过"})


def test_counts_requests_and_the_successful_attempt_only(trace):
    sample, _, _, seen, add = trace
    tool(add, "wrong", result={"status": "error", "content": "缺少章号"})
    add("tool_start", "running", tool_call_id="fixed", tool_name="task")
    add("tool_error", "failed", tool_call_id="fixed", tool_name="task", error="TimeoutError: retry")
    tool(add, "fixed")
    tool(add, "cancelled", status="cancelled")
    add("run_end")

    evidence = runtime_evidence.collect(sample)
    assert evidence["tools"] == {"successes": 1, "total": 3}
    assert evidence["tokens"] == 60000
    assert evidence["summary"]["skill_status"] == "not_read"
    assert set(seen) == {"submitted"}
    assert any("缺少章号" in item["text"] for item in evidence["summary"]["evidence"])


def test_started_call_counts_as_unsuccessful_when_run_explicitly_cancels(trace):
    sample, row, _, _, add = trace
    row["status"] = "cancelled"
    tool(add, "other")
    add("tool_start", "running", tool_call_id="cancelled", tool_name="save_artifact")
    cancellation = add("run_cancelled", "cancelled")
    evidence = runtime_evidence.collect(sample)
    assert evidence["tools"] == {"successes": 1, "total": 2}
    assert any(f"submitted:{cancellation.sequence}" in item["text"] for item in evidence["summary"]["evidence"])


def test_active_recovery_keeps_the_same_trace(trace, monkeypatch):
    sample, row, _, seen, add = trace
    row["integrity_status"] = "incomplete"
    tool(add, "review")
    add("run_end")

    def recover(trace_id, bound_sample):
        seen.append(trace_id)
        assert bound_sample["task_id"] == "sample-1-a2"
        row["integrity_status"] = "verified"

    monkeypatch.setattr(runtime_evidence, "_recover_trace", recover)
    assert runtime_evidence.collect(sample)["tokens"] == 60000
    assert set(seen) == {"submitted"}


def test_full_recovery_rejects_another_attempt(trace, monkeypatch):
    sample, row, events, _, add = trace
    row["integrity_status"] = "incomplete"
    add("run_end")
    run = SimpleNamespace(trace_id="submitted", service="executor", workspace_id="workspace",
                          status="completed", run_snapshot={"target_task": {"task_id": "other-attempt"}})
    monkeypatch.setattr(runtime_evidence, "_fetch_trace_content", lambda trace_id: (events, run, {}))
    ingested = []
    monkeypatch.setattr(runtime_evidence.importer, "ingest_events", lambda *args, **kwargs: ingested.append(args))
    RECOVER_TRACE("submitted", sample)
    assert ingested == []
    run.run_snapshot = json.loads(row["run_snapshot_json"])
    RECOVER_TRACE("submitted", sample)
    assert ingested == [(events, "workspace")]


@pytest.mark.parametrize("gap", ["zero_tools", "open_tool", "unverified", "wrong_attempt", "skill_payload"])
def test_missing_runtime_evidence_cannot_produce_scores(trace, gap):
    sample, row, events, _, add = trace
    if gap != "zero_tools":
        tool(add, "review")
    if gap == "open_tool":
        add("tool_start", "running", tool_call_id="unfinished", tool_name="read_artifact")
    if gap == "unverified":
        row["integrity_status"] = "incomplete"
    if gap == "wrong_attempt":
        row["run_snapshot_json"] = '{"target_task":{"task_id":"failed"}}'
    if gap == "skill_payload":
        tool(add, "skill", result={"content": ""}, name="read_writing_skill")
    add("run_end")

    with pytest.raises(runtime_evidence.EvidenceUnavailable):
        runtime_evidence.collect(sample)


def test_partial_usage_retains_tool_and_skill_evidence_after_recovery(trace, monkeypatch):
    sample, _, _, _, add = trace
    add("llm_start", "running", run_id="review")
    add("llm_end", run_id="review", payload_refs={"output": {
        "payload_id": "review-output", "content_hash": "hash", "kind": "semantic_full", "size_bytes": 120}})
    tool(add, "review")
    add("run_end")
    recovered = []
    output = None
    monkeypatch.setattr(runtime_evidence, "_recover_trace", lambda trace_id, sample: recovered.append(trace_id))
    monkeypatch.setattr(runtime_evidence, "read_payload", lambda payload_id: output)

    evidence = runtime_evidence.collect(sample)

    assert recovered == ["submitted"]
    assert evidence["tokens"] is None
    assert evidence["tools"] == {"successes": 1, "total": 1}
    assert evidence["summary"]["skill_status"] == "not_read"
    output = {"messages": [{"usage": {"prompt_tokens": 1500, "completion_tokens": 500}}]}
    assert runtime_evidence.collect(sample)["tokens"] == 62000


def test_zero_token_baseline_requests_recovery_without_discarding_evidence(trace, monkeypatch):
    sample, _, events, _, add = trace
    events[2].usage = TraceUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    tool(add, "review")
    add("run_end")
    assert runtime_evidence.collect(sample)["tokens"] == 0

    recovered = []
    monkeypatch.setattr(runtime_evidence, "_recover_trace", lambda trace_id, sample: recovered.append(trace_id))
    assert runtime_evidence.collect({**sample, "arm": "baseline"})["tokens"] == 0
    assert recovered == ["submitted"]


def test_summary_keeps_reading_and_later_revision_under_budget(trace):
    sample, _, _, _, add = trace
    entry = {"skill_id": "quiet", "name": "静场", "scope": "text-subagent", "content_hash": "hash",
             "source": "skills/quiet", "runtime_path": "/_skills_0/SKILL.md"}
    add("skill_catalog", "running", skill_catalog=[entry])
    add("artifact_revision", artifact={"logical_key": "正文/第001章.md"}, output={"content": "屋里很静。"})
    read = tool(add, "skill", result={"content": "用钟声表现等候中的安静。"}, name="read_writing_skill")
    add("skill_activation", skill_name="静场", skill_catalog=[entry],
        parent_event_id=read.event_id, skill_activation={"trigger_event_id": read.event_id})
    for number in range(400):
        tool(add, f"other-{number}", result={"content": "x" * 300}, name="read_artifact")
    add("artifact_revision", artifact={"logical_key": "正文/第001章.md"}, output={"content": "挂钟滴答三声，门仍没开。"})
    add("run_end")

    evidence = runtime_evidence.collect(sample)
    assert evidence["tools"] == {"successes": 401, "total": 401}
    summary = evidence["summary"]
    assert summary["skill_status"] == "read"
    assert len(json.dumps(summary, ensure_ascii=False).encode()) <= runtime_evidence.SUMMARY_BYTES
    assert sum(summary["omitted"].values()) > 0
    indexed = {item["ref"]: item for item in summary["evidence"]}
    assert len(indexed) == len(summary["evidence"])
    assert "用钟声" in indexed[f"submitted:{read.sequence}"]["text"]
    assert any("挂钟滴答三声" in item["text"] for item in indexed.values())


def test_read_without_later_action_is_visible_and_requires_budgeted_evidence(trace, monkeypatch):
    sample, _, _, _, add = trace
    entry = {"skill_id": "quiet", "name": "静场", "scope": "text-subagent", "content_hash": "hash",
             "source": "skills/quiet", "runtime_path": "/_skills_0/SKILL.md"}
    add("skill_catalog", "running", skill_catalog=[entry])
    add("artifact_revision", artifact={"logical_key": "正文/第001章.md"}, output={"content": "屋里很静。"})
    read = tool(add, "skill", result={"content": "用钟声表现等候中的安静。"}, name="read_writing_skill")
    add("skill_activation", skill_name="静场", skill_catalog=[entry], parent_event_id=read.event_id,
        skill_activation={"trigger_event_id": read.event_id})
    add("run_end")
    summary = runtime_evidence.collect(sample)["summary"]
    assert summary["skill_status"] == "read"
    assert {item["kind"] for item in summary["evidence"]} == {"skill_available", "skill_read", "skill_no_followup"}

    monkeypatch.setattr(runtime_evidence, "SUMMARY_BYTES", 100)
    with pytest.raises(runtime_evidence.EvidenceUnavailable, match="预算"):
        runtime_evidence.collect(sample)


def test_keeps_the_middle_revision_related_to_the_loaded_skill(trace, monkeypatch):
    sample, _, _, _, add = trace
    entry = {"skill_id": "quiet", "name": "静场", "scope": "text-subagent", "content_hash": "hash",
             "source": "skills/quiet", "runtime_path": "/_skills_0/SKILL.md"}
    chapter = {"logical_key": "正文/第001章.md"}
    add("skill_catalog", "running", skill_catalog=[entry])
    read = tool(add, "skill", result={"content": "用钟声表现等候中的安静。"}, name="read_writing_skill")
    add("skill_activation", skill_name="静场", skill_catalog=[entry], parent_event_id=read.event_id,
        skill_activation={"trigger_event_id": read.event_id})
    first = add("artifact_revision", artifact=chapter, output={"content": "桌上只有空杯。"})
    irrelevant = add("artifact_revision", artifact=chapter, output={"content": "墙面传来一阵回音。"})
    relevant = add("artifact_revision", artifact=chapter, output={"content": "三次钟声后，门仍没开。"})
    add("artifact_revision", artifact=chapter, output={"content": "她按住杯沿，门仍没开。"})
    add("run_end")

    full_summary = runtime_evidence.collect(sample)["summary"]
    optional_refs = {f"submitted:{event.sequence}" for event in (first, irrelevant)}
    required = [item for item in full_summary["evidence"] if item["ref"] not in optional_refs]
    budget = len(json.dumps({**full_summary, "evidence": required, "omitted": {"skill_manuscript": 2}},
                            ensure_ascii=False).encode())
    monkeypatch.setattr(runtime_evidence, "SUMMARY_BYTES", budget)
    summary = runtime_evidence.collect(sample)["summary"]
    included = {item["ref"] for item in summary["evidence"]}
    assert f"submitted:{relevant.sequence}" in included
    assert f"submitted:{irrelevant.sequence}" not in included
