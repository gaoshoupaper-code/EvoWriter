"""The evolver can retrieve evidence only through the active assessment."""
import json
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.experiments.history import make_history_tools


def sample(package, arm, phase, round_number, grade):
    return {
        "id": f"sample-{package}-{arm}", "task_id": f"{package}-{arm}", "package_id": package, "package_title": package,
        "round": round_number, "phase": phase, "repeat": 1, "arm": arm, "status": "completed",
        "target": {"chapter": 1}, "artifacts": {"正文/第001章.md": f"{package}终稿"},
        "scores": {"overall": grade, "dimensions": {"文风": grade},
                   "reasons": {"文风": [f"{package}评语"]}},
        "trace_ids": [f"{package}-{arm}-trace"],
    }


@pytest.fixture
def archive():
    return {
        "id": "this-run", "task_type": "chapter_text",
        "settings": {"optimization_ids": ["公开练习"], "regression_ids": {"chapter_text": ["保密复测"]}},
        "private": {"packages": {key: {"purpose": purpose, "input": {"demand_md": f"{key}题文"}}
                                 for key, purpose in [("公开练习", "optimization"), ("保密复测", "regression")]},
                    "rules": {"chapter_text": {"dimensions": [{"key": "文风"}]}}},
        "rounds": [{"round": 1, "status": "regression_failed", "design": "找到审稿遗漏",
                    "summary": "修订审稿提示", "diff": "+ 检查人物位置", "error": None,
                    "regressions": {"chapter_text": {"raw": "保密复测原文"}}},
                   {"round": 2, "status": "failed", "error": "启动检查失败",
                    "startup_check": {"status": "failed", "error": "装配失败"}}],
        "samples": [sample("公开练习", "baseline", "baseline", 0, 3),
                    sample("公开练习", "candidate", "exploration", 1, 4),
                    sample("保密复测", "baseline", "regression", 1, 4),
                    sample("保密复测", "candidate", "regression", 1, 3.5)],
    }


def calls(run):
    return {t.name: t.invoke for t in make_history_tools(run)}


def test_history_and_holdout_are_disjoint(archive):
    tools = calls(archive)
    history = tools["list_assessment_history"]({})
    assert [item["round"] for item in history] == [0, 1, 2]
    read_round = tools["read_assessment_round"]
    samples = read_round({"round_number": 1, "section": "samples"})["items"]
    assert len(samples) == 1 and samples[0]["sample_number"] == 1
    assert samples[0]["evidence_ref"] == archive["samples"][1]["id"]
    summary = read_round({"round_number": 1, "section": "regression"})
    assert summary == {"round": 1, "drops": {"文风": -0.5}}
    for section in ("design", "diff", "samples", "summary", "error", "regression"):
        result = read_round({"round_number": 1, "section": section})
        assert "保密复测" not in json.dumps(result, ensure_ascii=False)
    assert "启动检查失败" in read_round({"round_number": 2, "section": "error"})["content"]
    assert "装配失败" in read_round({"round_number": 2, "section": "startup_check"})["content"]
    read_sample = tools["read_improvement_sample"]
    for section, expected in [("manuscript", "公开练习终稿"), ("scores", "公开练习评语"), ("assignment", "公开练习题文")]:
        result = read_sample({"round_number": 1, "sample_number": 1, "section": section})
        assert expected in result["content"]
    assert "error" in read_sample({"round_number": 1, "sample_number": 2, "section": "scores"})
    with pytest.raises(ValidationError):
        read_round({"run_id": "other-run", "round_number": 1, "section": "samples"})


def test_assessment_assignment_uses_the_bound_case_revision(archive, monkeypatch):
    from app.experiments import repo
    from contracts.target_tasks import TaskInput

    packages = archive["private"].pop("packages")
    assignment = TaskInput(demand_md="原题", confirmation_rules="给定规则", skills=[
        {"name": "过期资料包技能", "stage": "text", "content": "使用旧技能"}])
    original = packages["公开练习"]
    original.update(input=assignment.model_dump(), input_revision=assignment.input_revision)
    archive["assessment_version"] = 1
    archive["private"]["package_revisions"] = {"公开练习": assignment.input_revision}
    monkeypatch.setattr(repo, "get_package", packages.get)
    read = calls(archive)["read_improvement_sample"]
    query = {"round_number": 0, "sample_number": 1, "section": "assignment"}
    actual = json.loads(read(query)["content"])
    assert actual["demand_md"] == "原题" and actual["skills"] == []
    original["input_revision"] = "other"
    assert "error" in read(query)


def test_search_only_returns_matching_evidence_in_active_assessment(archive):
    first = deepcopy(archive)
    first["id"] = "earlier-assessment"
    first["samples"][0]["artifacts"]["正文/第001章.md"] = "先前档案独有原文"
    first["rounds"][0]["design"] = "先前档案独有设计"
    old_search = calls(first)["search_assessment_history"]
    search = calls(archive)["search_assessment_history"]
    assert old_search({"query": "先前档案独有"})["items"]
    assert search({"query": "先前档案独有"}) == {"items": [], "next_offset": None}
    assert search({"query": "保密复测"})["items"] == []
    first_page = search({"query": "公开练习终稿", "limit": 1})
    continuation = search({"query": "公开练习终稿", "offset": first_page["next_offset"], "limit": 1})
    matches = first_page["items"] + continuation["items"]
    assert [(m["round_number"], m["sample_number"], m["section"]) for m in matches] == [
        (0, 1, "manuscript"), (1, 1, "manuscript")]
    assert continuation["next_offset"] is None and all("公开练习终稿" in m["excerpt"] for m in matches)
    drops = [m for m in search({"query": "文风"})["items"] if m["section"] == "regression"]
    assert len(drops) == 1 and drops[0]["round_number"] == 1 and "-0.5" in drops[0]["excerpt"]
    for status in ("passed", "regression", "evolving"):
        archive["rounds"][0]["status"] = status
        assert not [m for m in search({"query": "文风"})["items"] if m["section"] == "regression"]
    with pytest.raises(ValidationError):
        search({"query": "公开练习", "run_id": first["id"]})


@pytest.fixture
def history_db(monkeypatch):
    from app.experiments import assessment

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", conn)
    conn.executescript("""
        CREATE TABLE runs(trace_id TEXT PRIMARY KEY, service TEXT, run_snapshot_json TEXT,
                          workspace_id TEXT, thread_id TEXT, run_purpose TEXT, integrity_status TEXT);
        CREATE TABLE event_payloads(trace_id TEXT, sequence INTEGER, type TEXT, payload_json TEXT);
        CREATE TABLE trace_payload_links(trace_id TEXT, event_id TEXT, field_name TEXT, payload_id TEXT);
        CREATE TABLE payload_objects(payload_id TEXT, expires_at TEXT, deleted_at TEXT);
    """)
    assessment.init_schema(conn)
    yield conn
    conn.close()


def test_local_sample_trace_order_and_content(archive, monkeypatch, tmp_path, history_db):
    from app.core.settings import settings
    from contracts.trace.payload import ContentAddressedPayloadStore

    monkeypatch.setattr(settings, "trace_payload_dir", str(tmp_path))
    store = ContentAddressedPayloadStore(tmp_path)
    draft = store.put({"content": "审前初稿"})
    holdout = store.put({"content": "保密复测原文"})
    conn = history_db
    current = archive["samples"][1]
    trace_id = current["trace_ids"][0]
    def add_trace(key, task):
        conn.execute("INSERT INTO runs(trace_id,service,run_snapshot_json) VALUES(?, 'executor', ?)",
                     (key, json.dumps({"target_task": {"task_id": task}})))
    add_trace(trace_id, current["task_id"])
    add_trace("foreign-trace", "another-task")
    current["trace_ids"].append("foreign-trace")
    conn.executemany("INSERT INTO payload_objects VALUES (?,NULL,NULL)", [(draft.payload_id,), (holdout.payload_id,)])
    conn.executemany("INSERT INTO trace_payload_links VALUES (?,?,?,?)",
                     [(trace_id, "2", "output", draft.payload_id), ("foreign-trace", "6", "output", holdout.payload_id)])
    events = [
        ("skill_activation", {"skill_name": "对话练习"}),
        ("artifact_revision", {"artifact": {"logical_key": "正文/第001章.md"}, "payload_refs": {"output": draft.model_dump()}}),
        ("tool_start", {"tool_name": "task", "tool_call_id": "call1", "tool_args": {"subagent_type": "review"}}),
        ("tool_end", {"tool_name": "task", "tool_call_id": "call1", "tool_output": {"content": "审稿意见"}}),
        ("artifact_revision", {"artifact": {"logical_key": "正文/第001章.md"}, "output": {"content": "修稿"}}),
    ]
    for seq, (kind, values) in enumerate(events, 1):
        event = {"trace_id": trace_id, "event_id": str(seq), "sequence": seq, "type": kind,
                 "status": "completed", "timestamp": "2026-11-19T00:00:00Z", "source": "runtime", **values}
        conn.execute("INSERT INTO event_payloads VALUES (?, ?, ?, ?)", (trace_id, seq, kind, json.dumps(event)))
    tools = calls(archive)
    query = {"round_number": 1, "sample_number": 1, "trace_number": 1}
    timeline = tools["list_writing_events"]
    first = timeline({**query, "limit": 3})
    second = timeline({**query, "after_sequence": first["next_sequence"]})
    assert "warning" not in first
    assert first["items"][0]["skill"] == "对话练习"
    assert [event["sequence"] for event in first["items"] + second["items"]] == [1, 2, 3, 4, 5]
    for seq, expected in [(2, "审前初稿"), (3, "review"), (4, "审稿意见"), (5, "修稿")]:
        result = tools["read_writing_event"]({**query, "sequence": seq})
        assert expected in result["content"]
    event.update(sequence=6, event_id="6", output=None, payload_refs={"output": holdout.model_dump()})
    conn.execute("INSERT INTO event_payloads VALUES (?,6,'artifact_revision',?)", (trace_id, json.dumps(event)))
    incomplete = tools["read_writing_event"]({**query, "sequence": 6})
    assert "保密复测" not in str(incomplete)
    assert "unavailable" in incomplete["content"]
    conn.execute("UPDATE payload_objects SET deleted_at='expired' WHERE payload_id=?", (draft.payload_id,))
    assert "unavailable" in tools["read_writing_event"]({**query, "sequence": 2})["content"]
    assert "error" in timeline({**query, "trace_number": 2})
    with pytest.raises(ValidationError):
        tools["read_writing_event"]({**query, "sequence": 2, "payload_id": "foreign-payload"})


def test_missing_identity_needs_a_matching_parent_verified_archived_trace(archive, history_db, monkeypatch, tmp_path):
    from app.core.settings import settings
    from contracts.trace.payload import ContentAddressedPayloadStore

    current = archive["samples"][1]
    task = current["id"]
    trace = current["trace_ids"][0]
    current.update(task_id=task, completed=True, generation_status="completed", input_revision="revision",
                   source_commit="commit", checks={"complete": True, "identity": {
                       "source_commit": "commit", "input_revision": "revision", "target": current["target"]}})
    ws = f"target-{task}"
    conn = history_db
    conn.execute("INSERT INTO runs VALUES(?, 'executor', ?, ?, ?, 'optimization', 'incomplete')",
                 (trace, json.dumps({"display_context": {}}), ws, f"{ws}-1"))
    conn.execute("INSERT INTO assessment_entries VALUES(?, 'trace', ?, 1, ?, ?)",
                 (archive["id"], task, current["source_commit"], json.dumps({"trace_ids": [trace]})))
    monkeypatch.setattr(settings, "trace_payload_dir", str(tmp_path))
    manuscript = "属于本题的初稿" * 1200
    value = ContentAddressedPayloadStore(tmp_path).put({"content": manuscript})
    conn.execute("INSERT INTO payload_objects VALUES(?,NULL,NULL)", (value.payload_id,))
    conn.execute("INSERT INTO trace_payload_links VALUES(?, 'one', 'output', ?)", (trace, value.payload_id))
    event = {"trace_id": trace, "event_id": "one", "sequence": 1, "type": "artifact_revision", "status": "completed",
             "source": "runtime", "timestamp": "2026-11-19T00:00:00Z", "payload_refs": {"output": value.model_dump()}}
    conn.execute("INSERT INTO event_payloads VALUES(?, 1,'artifact_revision',?)", (trace, json.dumps(event)))
    query = {"round_number": 1, "sample_number": 1, "trace_number": 1}
    timeline = calls(archive)["list_writing_events"]
    read = calls(archive)["read_writing_event"]
    listing = timeline(query)
    assert listing["items"][0]["sequence"] == 1
    detail = read({**query, "sequence": 1})
    more = read({**query, "sequence": 1, "offset": detail["next_offset"]})
    assert json.loads(detail["content"] + more["content"])["snapshot"]["content"] == manuscript
    for result in (listing, detail, more):
        assert "身份快照缺失" in result["warning"]
        assert result["integrity_status"] == "incomplete"

    conn.execute("UPDATE trace_payload_links SET trace_id='foreign' WHERE event_id='one'")
    assert "unavailable_fields" in read({**query, "sequence": 1})["content"]
    conn.execute("UPDATE trace_payload_links SET trace_id=? WHERE event_id='one'", (trace,))
    conn.execute("UPDATE payload_objects SET deleted_at='expired'")
    assert "unavailable_fields" in read({**query, "sequence": 1})["content"]
    for key, bad in (("workspace_id", ws + "-foreign"), ("thread_id", ws + "-10"), ("thread_id", ws + "-1-other"),
                     ("run_purpose", "user_generation"), ("service", "evolution"), ("integrity_status", "verified"),
                     ("run_snapshot_json", json.dumps({"harness_commit": "other"})),
                     ("run_snapshot_json", json.dumps({"target_task": {"task_id": "foreign"}})),
                     ("run_snapshot_json", json.dumps({"target_task": {"task_id": task, "source_commit": "other"}})),
                     ("run_snapshot_json", json.dumps({"target_task": {}}))):
        before = conn.execute(f"SELECT {key} FROM runs WHERE trace_id=?", (trace,)).fetchone()[0]
        conn.execute(f"UPDATE runs SET {key}=? WHERE trace_id=?", (bad, trace))
        assert "error" in timeline(query) and "error" in read({**query, "sequence": 1})
        conn.execute(f"UPDATE runs SET {key}=? WHERE trace_id=?", (before, trace))
    for key, bad in (("generation_status", "running"), ("completed", False), ("input_revision", "different"),
                     ("source_commit", "different"), ("checks", {"complete": False}), ("task_id", "foreign")):
        before = current[key]
        current[key] = bad
        assert "error" in timeline(query)
        current[key] = before
    for key, bad in (("assessment_id", "other"), ("entry_id", "holdout"), ("version", "other"),
                     ("round_number", 0), ("payload", json.dumps({"trace_ids": ["other"]}))):
        before = conn.execute(f"SELECT {key} FROM assessment_entries").fetchone()[0]
        conn.execute(f"UPDATE assessment_entries SET {key}=?", (bad,))
        assert "error" in timeline(query)
        conn.execute(f"UPDATE assessment_entries SET {key}=?", (before,))
    current["phase"] = "regression"
    assert "error" in timeline(query)
