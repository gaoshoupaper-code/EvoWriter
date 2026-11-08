"""REQ-20261108-191146: user and experiment observation boundaries."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.db as db
from app.core.settings import settings
from app.view import active, traces, workbenches, events


@pytest.fixture
def store(tmp_path):
    old = settings.evolution_db
    settings.evolution_db = str(tmp_path / "isolation.db")
    db._conn = None
    db.init_db()
    active._reset_diff_state_for_test()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    settings.evolution_db = old
    active._reset_diff_state_for_test()


def seed(tid, purpose, tokens, workload="creation"):
    db.execute("""INSERT INTO runs(trace_id,workspace_id,status,started_at,ingested_at,
                  run_purpose,schema_version,service,workload,duration_ms)
                  VALUES (?,'book','completed','2026-11-08','2026-11-08',?,2,'executor',?,100)""",
               (tid, purpose, workload))
    db.execute("""INSERT INTO nodes(node_id,trace_id,kind,label,status,usage_input,usage_output,usage_total)
                  VALUES (?,?,'llm','call','completed',?,0,?)""", (tid + "-n", tid, tokens, tokens))


def client():
    app = FastAPI()
    for router in (traces.router, active.router, workbenches.router):
        app.include_router(router, prefix="/api")
    return TestClient(app)


def test_history_and_profiles_separate_user_and_all_experiment_roles(store):
    seed("user", "user_generation", 10)
    seed("generation", "optimization", 20)
    seed("score", "evolution_eval", 30, "evaluation")
    seed("improve", "evolution_evolve", 40, "evolution")
    seed("unknown", "", 1000)
    api = client()
    assert [r["trace_id"] for r in api.get("/api/traces").json()["items"]] == ["user"]
    result = api.get("/api/traces?observation_scope=experiment").json()
    assert {r["trace_id"] for r in result["items"]} == {"generation", "score", "improve"}
    assert sum(r["token_total"] for r in result["items"]) == 90
    user = api.get("/api/analysis/profiles?hours=8760").json()["profiles"]
    experiment = api.get("/api/analysis/profiles?hours=8760&observation_scope=experiment").json()["profiles"]
    assert sum(p["sample_size"] for p in user) == 1
    assert sum(p["usage"]["total_tokens"] for p in user) == 10
    assert sum(p["sample_size"] for p in experiment) == 3
    assert sum(p["usage"]["total_tokens"] for p in experiment) == 90


def test_profile_depth_uses_all_scoped_nodes_without_double_counting_tool_usage(store):
    seed("user-depth", "user_generation", 10)
    seed("experiment-depth", "optimization", 20)
    for trace_id, depth in (("user-depth", 8), ("experiment-depth", 99)):
        db.execute("""INSERT INTO nodes(node_id,trace_id,kind,label,status,depth,usage_total)
                      VALUES (?,?,'tool','nested tool','completed',?,1000)""",
                   (trace_id + "-tool", trace_id, depth))
    api = client()
    for scope, depth, tokens in (("user", 8, 10), ("experiment", 99, 20)):
        profiles = api.get("/api/analysis/profiles?hours=8760&observation_scope=" + scope).json()["profiles"]
        creation = next(row for row in profiles if row["workload"] == "creation")
        assert creation["usage"]["total_tokens"] == tokens
        assert creation["usage"]["max_span_depth"] == depth


def test_uningested_active_sources_never_default_to_user(store):
    active._active_cache = [{"trace_id": "user", "run_purpose": "user_generation"},
                            {"trace_id": "experiment", "run_purpose": "optimization",
                             "external_refs": {"experiment_id": "exp-1", "sample_id": "sample-1"}},
                            {"trace_id": "unknown"}]
    with patch.object(active, "_get_evolution_active_runs", return_value=[]):
        api = client()
        assert [r["trace_id"] for r in api.get("/api/active-runs").json()] == ["user"]
        rows = api.get("/api/active-runs?observation_scope=experiment").json()
    assert [r["trace_id"] for r in rows] == ["experiment"]
    assert rows[0]["experiment_id"] == "exp-1"


def test_scoped_stream_filters_finished_event_before_ingestion(store):
    async def read():
        bus = events.EventBus()
        bus.publish("run_finished", {"trace_id": "exp", "run": {"run_purpose": "optimization"}})
        bus.publish("run_finished", {"trace_id": "unknown", "run": None})
        bus.publish("run_finished", {"trace_id": "user", "run": {"run_purpose": "user_generation"}})
        with patch.object(events, "_bus", bus):
            response = await events.event_stream(since=0, observation_scope="user")
            frame = await anext(response.body_iterator)
            await response.body_iterator.aclose()
        return frame
    frame = asyncio.run(read())
    assert '"trace_id": "user"' in frame
    assert '"trace_id": "exp"' not in frame


def test_unknown_active_usage_is_not_counted_as_user(store):
    from app.view.usage import _active_user_rows
    with patch.object(active, "get_active_runs", return_value=[
        {"trace_id": "unknown", "owner_user_id": "writer", "usage_total": 1000},
        {"trace_id": "user", "owner_user_id": "writer", "run_purpose": "user_generation", "usage_total": 10},
    ]):
        assert [row["trace_id"] for row in _active_user_rows()] == ["user"]


def test_partial_and_unknown_usage_are_not_reported_as_known_zero(store):
    from app.view.usage import list_user_usage
    db.execute("INSERT INTO user_cache(user_id,username,disabled,synced_at) VALUES ('writer','Writer',0,'now')")
    seed("unknown-usage", "user_generation", None)
    db.execute("UPDATE runs SET owner_user_id='writer'")
    with patch.object(active, "get_active_runs", return_value=[]):
        row = list_user_usage()[0]
        assert row.total_tokens is None
        assert row.total_partial
        seed("known-usage", "user_generation", 10)
        db.execute("UPDATE runs SET owner_user_id='writer'")
        row = list_user_usage()[0]
    assert row.total_tokens == 10
    assert row.total_partial
    profiles = client().get("/api/analysis/profiles?hours=8760").json()["profiles"]
    creation = next(row for row in profiles if row["workload"] == "creation")
    assert creation["usage"]["total_tokens"] == 10
    assert creation["usage"]["total_partial"]


def test_saved_experiment_identity_overrides_legacy_user_default(store):
    from app.experiments import repo
    from app.view.usage import list_user_usage
    repo.init_schema()
    db.execute("INSERT INTO user_cache(user_id,username,disabled,synced_at) VALUES ('writer','Writer',0,'now')")
    seed("user", "user_generation", 10)
    seed("legacy-sample", "user_generation", 20)
    seed("legacy-refs", "user_generation", 30)
    seed("system-evidence", "evidence_compile", 1000, "evidence_compile")
    db.execute("UPDATE runs SET owner_user_id='writer'")
    db.execute("UPDATE runs SET external_refs_json=? WHERE trace_id='legacy-refs'", (json.dumps({"experiment_id": "exp-1"}),))
    repo.insert("self_evolution_runs", {"id": "exp-1", "created_at": "now", "samples": [{"id": "sample-1", "trace_ids": ["legacy-sample"]}], "rounds": []})
    api = client()
    assert [r["trace_id"] for r in api.get("/api/traces").json()["items"]] == ["user"]
    assert {r["trace_id"] for r in api.get("/api/traces?observation_scope=experiment").json()["items"]} == {"legacy-sample", "legacy-refs"}
    with patch.object(active, "get_active_runs", return_value=[]):
        assert list_user_usage()[0].total_tokens == 10
    profile = api.get("/api/analysis/profiles?hours=8760").json()["profiles"]
    assert sum(row["usage"]["total_tokens"] or 0 for row in profile) == 10
