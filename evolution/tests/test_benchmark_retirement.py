"""REQ-20261108-191146: retired starts and scoped, restartable history cleanup."""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db


@pytest.fixture
def database(tmp_path, monkeypatch):
    connection = sqlite3.connect(tmp_path / "retirement.db", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(db, "_conn", connection)
    db.init_db()
    from app.core.settings import settings
    monkeypatch.setattr(settings, "trace_payload_dir", str(tmp_path / "payloads"))
    from app.benchmark import retirement
    # Other historical suites reload the settings module; bind this fixture's instance.
    monkeypatch.setattr(retirement, "settings", settings)
    yield connection
    connection.close()


def batch(trace_id=None, status="done"):
    from app.benchmark import repo
    identifier = repo.create_batch(case_ids=["legacy-case"], versions=[1], golden_revision="old", seeds=1)
    db.execute("UPDATE benchmark_runs SET status=?, trace_id=? WHERE batch_id=?", (status, trace_id, identifier))
    return identifier


def trace(identifier, purpose="evolution", refs=None):
    db.execute("INSERT INTO runs(trace_id,workspace_id,status,ingested_at,run_purpose,external_refs_json) VALUES (?,?,'completed','2026-01-01',?,?)",
               (identifier, "ws-" + identifier, purpose, json.dumps(refs or {})))


def payload(trace_id, value):
    from contracts.trace.payload import ContentAddressedPayloadStore
    from app.core.settings import settings
    ref = ContentAddressedPayloadStore(settings.trace_payload_path).put(value)
    path = settings.trace_payload_path / (ref.payload_id + ".json")
    db.execute("INSERT OR IGNORE INTO payload_objects(payload_id,content_hash,kind,size_bytes,sensitivity,storage_path,created_at) VALUES (?,?,'semantic_full',1,'private',?,'2026-01-01')",
               (ref.payload_id, ref.content_hash, str(path)))
    db.execute("INSERT INTO trace_payload_links(trace_id,event_id,field_name,payload_id) VALUES (?,?,'output',?)", (trace_id, ref.payload_id, ref.payload_id))
    return path


def test_http_and_internal_starts_retired_before_any_creation(database):
    from app.benchmark import api, runner
    application = FastAPI()
    application.include_router(api.router, prefix="/api")
    with TestClient(application) as client, patch.object(runner, "_dispatch_batch") as dispatch:
        for path in ("/api/benchmark/run", "/api/benchmark/rerun-golden"):
            response = client.post(path, json={})
            assert response.status_code == 410
            assert "退役" in response.json()["detail"]
        with pytest.raises(RuntimeError, match="退役"):
            runner.trigger_run(versions=[1], case_ids=["legacy-case"])
        with pytest.raises(RuntimeError, match="退役"):
            runner.trigger_golden_upgrade_rerun()
        dispatch.assert_not_called()
    assert db.query_one("SELECT COUNT(*) AS n FROM benchmark_runs")["n"] == 0


def test_dry_run_does_not_mutate_and_lists_unowned_traces(database):
    from app.benchmark import retirement
    trace("old")
    trace("unknown")
    old = batch("old")
    document = retirement.build_manifest()
    assert document["batch_ids"] == [old]
    assert any(item["id"] == "unknown" and item["reason"] for item in document["retained"])
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'")
    assert db.query_one("SELECT 1 FROM benchmark_runs WHERE batch_id=?", (old,))


def test_scoped_cleanup_protects_shared_user_new_experiment_and_inputs(database):
    from app.benchmark import retirement
    for identifier in ("old", "new-experiment", "legacy-flow", "shared-trace"):
        trace(identifier)
    trace("user", "user_generation")
    trace("unowned")
    exclusive = payload("old", {"text": "old-only"})
    shared = payload("old", {"text": "shared"})
    payload("user", {"text": "shared"})
    for identifier in ("old", "new-experiment", "legacy-flow", "shared-trace", "user"):
        batch(identifier)
    db.execute("CREATE TABLE IF NOT EXISTS self_evolution_runs(id TEXT PRIMARY KEY,payload TEXT,created_at TEXT)")
    for identifier, version in (("new-experiment", 2), ("legacy-flow", 1)):
        db.execute("INSERT INTO self_evolution_runs VALUES (?,?, '2026-01-01')", (identifier, json.dumps({"id": identifier, "flow_version": version, "samples": [{"trace_ids": [identifier]}]})))
    db.execute("INSERT INTO manual_tests(test_id,case_id,version_type,status,trace_id,created_at) VALUES ('test','case','working','done','shared-trace','2026-01-01')")
    db.execute("INSERT INTO dataset_meta(case_id,layer,status,updated_at) VALUES ('input','golden','active','2026-01-01')")
    document = retirement.build_manifest()
    result = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert not exclusive.exists()
    assert shared.exists()
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'") is None
    for identifier in ("new-experiment", "legacy-flow", "shared-trace", "user", "unowned"):
        assert db.query_one("SELECT 1 FROM runs WHERE trace_id=?", (identifier,))
    assert db.query_one("SELECT COUNT(*) AS n FROM self_evolution_runs")["n"] == 2
    assert db.query_one("SELECT 1 FROM dataset_meta WHERE case_id='input'")
    assert db.query_one("SELECT COUNT(*) AS n FROM benchmark_runs")["n"] == 0
    assert any(item["id"] == "shared-trace" for item in result["retained"])


def test_stop_unconfirmed_keeps_all_history_and_files(database):
    from app.benchmark import retirement
    trace("active")
    old = batch("active", "running")
    path = payload("active", {"text": "keep on stop failure"})
    result = retirement.apply_manifest(retirement.build_manifest(), confirm_stopped=lambda rows: (False, "executor stop unconfirmed"))
    assert result["status"] == "partial"
    assert result["failed"]
    assert path.exists()
    assert db.query_one("SELECT 1 FROM benchmark_runs WHERE batch_id=?", (old,))
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='active'")


def test_file_failure_is_reported_and_retry_is_idempotent(database):
    from app.benchmark import retirement
    trace("old")
    batch("old")
    path = payload("old", {"text": "retry"})
    document = retirement.build_manifest()
    with patch.object(Path, "unlink", side_effect=OSError("disk blocked")):
        first = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert first["status"] == "partial"
    assert any(item["kind"] == "payload" for item in first["failed"])
    assert path.exists()
    second = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert second["status"] == "complete"
    assert not path.exists()
    assert retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))["status"] == "complete"


def test_late_new_reference_is_rechecked_and_preserved(database):
    from app.benchmark import retirement
    trace("old")
    batch("old")
    path = payload("old", {"text": "new reference"})
    document = retirement.build_manifest()
    db.execute("CREATE TABLE IF NOT EXISTS self_evolution_runs(id TEXT PRIMARY KEY,payload TEXT,created_at TEXT)")
    db.execute("INSERT INTO self_evolution_runs VALUES ('new',?,'2026-01-01')", (json.dumps({"samples": [{"trace_ids": ["old"]}]}),))
    result = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert path.exists()
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'")


def test_manifest_cannot_expand_scope_or_cross_database(database):
    from app.benchmark import retirement
    trace("user", "user_generation")
    document = retirement.build_manifest()
    document["rows"].append({"id": 999, "batch_id": "invented", "trace_id": "user", "status": "done"})
    with pytest.raises(ValueError, match="manifest"):
        retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='user'")


def test_real_stop_confirmation_rejects_unmapped_running_task(database):
    from app.benchmark import retirement
    batch(None, "running")
    document = retirement.build_manifest()
    confirmed, reason = retirement.confirm_stopped(document["rows"])
    assert not confirmed
    assert "无法确认停止" in reason


def test_real_stop_confirmation_checks_executor_and_preserves_on_outage(database):
    from app.benchmark import retirement
    import httpx
    trace("old")
    batch("old")
    document = retirement.build_manifest()
    with patch.object(retirement.httpx, "get", side_effect=httpx.ConnectError("unavailable")):
        result = retirement.apply_manifest(document)
    assert result["status"] == "partial"
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'")
    assert db.query_one("SELECT 1 FROM benchmark_runs")


def test_real_stop_confirmation_stops_only_proven_task_and_requires_terminal(database):
    from app.benchmark import retirement
    import httpx
    trace("old", refs={"task_id": "old-task"})
    batch("old", "evaluating")
    request = httpx.Request("GET", "http://executor/status")
    responses = [httpx.Response(200, json={"status": "running"}, request=request),
                 httpx.Response(200, json={"status": "cancelled"}, request=request),
                 httpx.Response(200, json=[], request=request)]
    with patch.object(retirement.httpx, "get", side_effect=responses), \
         patch.object(retirement.httpx, "post", return_value=httpx.Response(202, request=request)) as stop:
        result = retirement.apply_manifest(retirement.build_manifest())
    assert result["status"] == "complete"
    assert stop.call_count == 1
    assert stop.call_args.args[0].endswith("/ab/stop/old-task")


def test_late_shared_artifact_parent_is_preserved(database):
    from app.benchmark import retirement
    trace("old")
    trace("other")
    batch("old")
    path = payload("old", {"text": "revision"})
    db.execute("INSERT INTO artifacts VALUES ('artifact','file','work','/file.md','2026-01-01')")
    db.execute("INSERT INTO artifact_revisions(artifact_revision_id,artifact_id,payload_id,content_hash,producer_trace_id,created_at) VALUES ('parent','artifact',?,?,'old','2026-01-01')", (path.stem, path.stem))
    document = retirement.build_manifest()
    db.execute("INSERT INTO artifact_revisions(artifact_revision_id,artifact_id,parent_revision_id,payload_id,content_hash,producer_trace_id,created_at) VALUES ('child','artifact','parent',?,?,'other','2026-01-02')", (path.stem, path.stem))
    result = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert db.query_one("SELECT 1 FROM artifact_revisions WHERE artifact_revision_id='parent'")
    assert path.exists()


def test_session_kept_but_deleted_weakness_report_reference_removed(database):
    from app.benchmark import retirement
    old = batch()
    db.execute("INSERT INTO evolve_sessions(session_id,case_id,status,benchmark_batch_id,created_at) VALUES ('chat','case','conversing',?,'2026-01-01')", (old,))
    result = retirement.apply_manifest(retirement.build_manifest(), confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    session = db.query_one("SELECT * FROM evolve_sessions WHERE session_id='chat'")
    assert session["status"] == "conversing"
    assert session["benchmark_batch_id"] is None


@pytest.mark.parametrize("external_consumer", [False, True])
def test_real_produces_lineage_distinguishes_owned_revision_from_shared_revision(database, external_consumer):
    from app.benchmark import retirement
    trace("old")
    trace("other")
    batch("old")
    path = payload("old", {"text": "own revision"})
    db.execute("INSERT INTO artifacts VALUES ('artifact','workspace_file','work','/chapter.md','2026-01-01')")
    db.execute("INSERT INTO artifact_revisions(artifact_revision_id,artifact_id,payload_id,content_hash,producer_trace_id,created_at) VALUES ('revision','artifact',?,?,'old','2026-01-01')", (path.stem, path.stem))
    db.execute("INSERT INTO lineage_edges(from_type,from_id,relation,to_type,to_id,created_at) VALUES ('trace','old','produces','artifact_revision','revision','2026-01-01')")
    if external_consumer:
        db.execute("INSERT INTO lineage_edges(from_type,from_id,relation,to_type,to_id,created_at) VALUES ('trace','other','consumes','artifact_revision','revision','2026-01-01')")
    result = retirement.apply_manifest(retirement.build_manifest(), confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert path.exists() == external_consumer
    assert bool(db.query_one("SELECT 1 FROM runs WHERE trace_id='old'")) == external_consumer
    assert bool(db.query_one("SELECT 1 FROM artifact_revisions WHERE artifact_revision_id='revision'")) == external_consumer


def test_manifest_reports_all_legacy_evidence_copies_and_unknown_purposes(database):
    from app.benchmark import retirement
    trace("old")
    batch("old")
    for purpose in ("evolution_eval", "evolution_evolve", "evolution_test"):
        trace(purpose, purpose)
    document = retirement.build_manifest()
    retained = {(item["kind"], item["id"]) for item in document["retained"]}
    assert ("executor_trace_evidence", "old") in retained
    assert ("executor_workspace", "old") in retained
    for purpose in ("evolution_eval", "evolution_evolve", "evolution_test"):
        assert ("trace", purpose) in retained


def test_removing_reference_after_dry_run_does_not_expand_approved_deletions(database):
    from app.benchmark import retirement
    trace("old")
    batch("old")
    path = payload("old", {"text": "initially retained"})
    db.execute("INSERT INTO manual_tests(test_id,case_id,version_type,status,trace_id,created_at) VALUES ('test','case','working','done','old','2026-01-01')")
    document = retirement.build_manifest()
    db.execute("DELETE FROM manual_tests WHERE test_id='test'")
    result = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert path.exists()
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'")


def test_sealed_body_survives_old_reference_removal(database):
    from app.benchmark import retirement
    trace("old")
    batch("old")
    path = payload("old", {"text": "sealed"})
    db.execute("UPDATE payload_objects SET sealed=1 WHERE payload_id=?", (path.stem,))
    result = retirement.apply_manifest(retirement.build_manifest(), confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert path.exists()
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'") is None


def test_stop_acceptance_without_terminal_confirmation_does_not_delete(database):
    from app.benchmark import retirement
    import httpx
    trace("old", refs={"task_id": "task"})
    batch("old", "evaluating")
    request = httpx.Request("GET", "http://executor/status")
    with patch.object(retirement.httpx, "get", return_value=httpx.Response(200, json={"status": "running"}, request=request)), \
         patch.object(retirement.httpx, "post", return_value=httpx.Response(202, request=request)), \
         patch.object(retirement.time, "monotonic", side_effect=[0, 26]):
        result = retirement.apply_manifest(retirement.build_manifest())
    assert result["status"] == "partial"
    assert db.query_one("SELECT 1 FROM benchmark_runs")
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'")


@pytest.mark.parametrize("relationship", ["parent", "source"])
def test_protected_child_preserves_parent_and_source_revision_dependencies(database, relationship):
    from app.benchmark import retirement
    for identifier in ("parent-run", "child-run"):
        trace(identifier)
        batch(identifier)
    parent_body = payload("parent-run", {"text": "parent"})
    child_body = payload("child-run", {"text": "child"})
    db.execute("INSERT INTO artifacts VALUES ('artifact','file','work','/draft.md','2026-01-01')")
    db.execute("INSERT INTO artifact_revisions(artifact_revision_id,artifact_id,payload_id,content_hash,producer_trace_id,created_at) VALUES ('parent','artifact',?,?,'parent-run','2026-01-01')", (parent_body.stem, parent_body.stem))
    db.execute("INSERT INTO artifact_revisions(artifact_revision_id,artifact_id,parent_revision_id,payload_id,content_hash,producer_trace_id,source_trace_id,created_at) VALUES ('child','artifact',?,?,?,'child-run',?,'2026-01-01')", ("parent" if relationship == "parent" else None, child_body.stem, child_body.stem, "parent-run" if relationship == "source" else None))
    document = retirement.build_manifest()
    db.execute("INSERT INTO self_evolution_runs(id,payload,created_at) VALUES ('new-experiment',?,'2026-01-01')", (json.dumps({"samples": [{"trace_ids": ["child-run"]}]}),))
    result = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert parent_body.exists()
    assert db.query_one("SELECT 1 FROM artifact_revisions WHERE artifact_revision_id='parent'")
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='parent-run'")
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='child-run'")


@pytest.mark.parametrize("reference", ["outcome", "score", "baseline", "candidate"])
def test_late_immutable_and_experiment_revision_targets_are_preserved(database, reference):
    from app.benchmark import retirement
    trace("old")
    batch("old")
    path = payload("old", {"text": "governed revision"})
    db.execute("INSERT INTO artifacts VALUES ('artifact','file','work','/draft.md','2026-01-01')")
    db.execute("INSERT INTO artifact_revisions(artifact_revision_id,artifact_id,payload_id,content_hash,producer_trace_id,created_at) VALUES ('revision','artifact',?,?,'old','2026-01-01')", (path.stem, path.stem))
    document = retirement.build_manifest()
    if reference == "outcome":
        db.execute("INSERT INTO outcome_records(outcome_id,target_type,target_id,outcome_type,created_at) VALUES ('fact','artifact_revision','revision','adopt','2026-01-01')")
    elif reference == "score":
        db.execute("INSERT INTO score_records(score_id,target_type,target_id,rubric_id,rubric_version,score_json,actor_user_id,created_at) VALUES ('score','artifact_revision','revision','rubric','1','{}','user','2026-01-01')")
    else:
        db.execute("INSERT INTO experiment_runs_v2(experiment_id,source_evaluation_dossier_id,baseline_revision_id,candidate_revision_id,status,formula_version,created_at) VALUES ('experiment','dossier',?,?,'done','1','2026-01-01')", ("revision" if reference == "baseline" else None, "revision" if reference == "candidate" else "other"))
    result = retirement.apply_manifest(document, confirm_stopped=lambda rows: (True, "confirmed"))
    assert result["status"] == "complete"
    assert path.exists()
    assert db.query_one("SELECT 1 FROM artifact_revisions WHERE artifact_revision_id='revision'")
    assert db.query_one("SELECT 1 FROM runs WHERE trace_id='old'")
