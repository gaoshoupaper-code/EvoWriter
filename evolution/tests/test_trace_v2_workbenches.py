from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import app.core.db as db
from app.core.settings import settings


class TraceV2WorkbenchesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = settings.evolution_db
        self.old_payload_dir = settings.trace_payload_dir
        settings.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        settings.trace_payload_dir = str(Path(self.tmp.name) / "payloads")
        db._conn = None
        db.init_db()
        for trace_id, workload, status, integrity, coverage in (
            ("trace-create", "creation", "completed", "verified",
             {"payload": "known", "token": "known", "cost": "unknown"}),
            ("trace-eval", "evaluation", "failed", "incomplete",
             {"payload": "partial", "token": "unknown", "cost": "unknown"}),
        ):
            db.execute(
                """INSERT INTO runs
                   (trace_id, workspace_id, status, started_at, duration_ms, event_count,
                    ingested_at, schema_version, service, workload, integrity_status,
                    coverage_json)
                   VALUES (?, 'ws', ?, '2026-07-28T00:00:00+00:00', 1000, 3,
                           '2026-07-28T00:01:00+00:00', 2, 'executor', ?, ?, ?)""",
                (trace_id, status, workload, integrity, json.dumps(coverage)),
            )
        db.execute(
            """INSERT INTO nodes
               (node_id, trace_id, kind, label, status, depth, usage_input,
                usage_output, usage_total)
               VALUES ('llm-1', 'trace-create', 'llm', 'model', 'completed', 3, 60, 40, 100)"""
        )
        for sequence, event_type, extra in (
            (1, "skill_activation", {}),
            (2, "middleware_intervention", {"intervention": {"action": "retry"}}),
            (3, "hitl", {}),
        ):
            db.execute(
                """INSERT INTO event_payloads
                   (trace_id, sequence, type, timestamp, payload_json)
                   VALUES ('trace-create', ?, ?, '2026-07-28T00:00:00+00:00', ?)""",
                (sequence, event_type, json.dumps({"type": event_type, **extra})),
            )

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        settings.evolution_db = self.old_db
        settings.trace_payload_dir = self.old_payload_dir
        self.tmp.cleanup()

    def test_profiles_keep_workload_denominators_separate(self) -> None:
        from app.view.workbenches import workload_profiles

        result = workload_profiles(hours=None)
        profiles = {item["workload"]: item for item in result["profiles"]}
        creation = profiles["creation"]
        evaluation = profiles["evaluation"]

        self.assertEqual(result["formula_version"], "writer-trace-v2/profile-1")
        self.assertEqual(creation["sample_size"], 1)
        self.assertEqual(creation["status_denominator"], 1)
        self.assertEqual(creation["success_rate"], 1.0)
        self.assertEqual(creation["integrity"]["verified"], 1)
        self.assertEqual(creation["mechanisms"]["skill_activations"], 1)
        self.assertEqual(creation["mechanisms"]["retries"], 1)
        self.assertIn("linked_outcome", creation["advanced_analysis"]["missing_conditions"])
        self.assertIn("completed_experiment", creation["advanced_analysis"]["missing_conditions"])
        self.assertEqual(evaluation["sample_size"], 1)
        self.assertEqual(evaluation["success_rate"], 0.0)

    def test_trace_list_filters_by_integrity_status(self) -> None:
        from app.view.traces import list_traces

        result = list_traces(
            workspace=None,
            thread_id=None,
            status=None,
            owner=None,
            run_purpose=None,
            workload=None,
            integrity_status="verified",
            since=None,
            until=None,
            limit=50,
            offset=0,
        )

        self.assertEqual(result.total, 1)
        self.assertEqual([item.trace_id for item in result.items], ["trace-create"])
        self.assertEqual(result.items[0].skill_activation_count, 1)
        self.assertEqual(result.items[0].middleware_intervention_count, 1)
        self.assertEqual(result.items[0].hitl_count, 1)

    def test_trace_summary_restores_persisted_span_links(self) -> None:
        from app.view.traces import _run_summary_from_row

        db.execute(
            "UPDATE runs SET links_json=? WHERE trace_id='trace-create'",
            (json.dumps([{
                "target_trace_id": "trace-source",
                "relation": "triggered_by",
                "attributes": {"dossier_id": "dossier-1"},
            }]),),
        )

        summary = _run_summary_from_row(
            db.query_one("SELECT * FROM runs WHERE trace_id='trace-create'")
        )

        self.assertEqual(len(summary.links), 1)
        self.assertEqual(summary.links[0].target_trace_id, "trace-source")
        self.assertEqual(summary.links[0].attributes["dossier_id"], "dossier-1")

    def test_lineage_and_artifact_revision_are_structural_by_default(self) -> None:
        from app.trace.facts import add_lineage
        from app.view.workbenches import (
            get_artifact_revision,
            get_artifact_revision_content,
            get_lineage,
            list_trace_artifact_revisions,
        )
        from contracts.trace.payload import ContentAddressedPayloadStore

        ref = ContentAddressedPayloadStore(settings.trace_payload_path).put("immutable draft")
        db.execute(
            """INSERT INTO payload_objects
               (payload_id, content_hash, kind, size_bytes, sensitivity, expires_at,
                storage_path, created_at)
               VALUES (?, ?, ?, ?, 'restricted', ?, ?, '2026-07-28')""",
            (ref.payload_id, ref.content_hash, ref.kind, ref.size_bytes, ref.expires_at,
             str(settings.trace_payload_path / f"{ref.payload_id}.json")),
        )
        db.execute(
            """INSERT INTO artifacts
               (artifact_id, artifact_type, workspace_id, logical_key, created_at)
               VALUES ('artifact-1', 'draft', 'ws', 'chapter/1', '2026-07-28')"""
        )
        db.execute(
            """INSERT INTO artifact_revisions
               (artifact_revision_id, artifact_id, payload_id, content_hash,
                producer_trace_id, created_at)
               VALUES ('revision-1', 'artifact-1', ?, ?, 'trace-create', '2026-07-28')""",
            (ref.payload_id, ref.content_hash),
        )
        add_lineage("trace", "trace-create", "produces", "artifact_revision", "revision-1")

        graph = get_lineage("trace", "trace-create")
        self.assertEqual(graph["outgoing"][0]["to_id"], "revision-1")
        revision = get_artifact_revision("revision-1")
        self.assertEqual(revision["payload_id"], ref.payload_id)
        self.assertNotIn("content", revision)
        revisions = list_trace_artifact_revisions("trace-create")
        self.assertEqual(revisions["total_groups"], 1)
        self.assertEqual(revisions["total_revisions"], 1)
        group = revisions["groups"][0]
        self.assertEqual(group["logical_key"], "chapter/1")
        self.assertEqual(group["head_revision_id"], "revision-1")
        self.assertEqual(group["revisions"][0]["artifact_revision_id"], "revision-1")
        self.assertNotIn("content", group["revisions"][0])

        request = SimpleNamespace(
            state=SimpleNamespace(is_super_admin=True, user_id="admin")
        )
        content = get_artifact_revision_content("revision-1", request)
        self.assertEqual(content["content"], "immutable draft")
        audit = db.query_one("SELECT action, object_type FROM access_audit")
        self.assertEqual(audit["action"], "view")
        self.assertEqual(audit["object_type"], "artifact_revision")

    def test_artifact_revisions_grouped_by_logical_key(self) -> None:
        """同路径多修订折叠一组：head=最新、组内时间升序（REQ-20260921-114943 FR-003）。"""
        from app.view.workbenches import list_trace_artifact_revisions

        db.execute(
            """INSERT INTO artifacts
               (artifact_id, artifact_type, workspace_id, logical_key, created_at)
               VALUES ('artifact-g', 'draft', 'ws', 'storyline/S01.md', '2026-07-28')"""
        )
        # 3 次修订：t1 草稿 → t2 润色 → t3 润色未改内容（hash 与 t2 相同）
        for rid, hash_v, ts in (
            ("rev-g1", "hash-draft", "2026-07-28T01:00:00+00:00"),
            ("rev-g2", "hash-polish", "2026-07-28T02:00:00+00:00"),
            ("rev-g3", "hash-polish", "2026-07-28T03:00:00+00:00"),
        ):
            db.execute(
                """INSERT INTO payload_objects
                   (payload_id, content_hash, kind, size_bytes, sensitivity, expires_at,
                    storage_path, created_at)
                   VALUES (?, ?, 'semantic_full', 10, 'normal', NULL, '', ?)""",
                (f"pl-{rid}", hash_v, ts),
            )
            db.execute(
                """INSERT INTO artifact_revisions
                   (artifact_revision_id, artifact_id, payload_id, content_hash,
                    producer_trace_id, created_at)
                   VALUES (?, 'artifact-g', ?, ?, 'trace-create', ?)""",
                (rid, f"pl-{rid}", hash_v, ts),
            )

        result = list_trace_artifact_revisions("trace-create")
        by_key = {g["logical_key"]: g for g in result["groups"]}
        self.assertEqual(result["total_groups"], 1)
        story = by_key["storyline/S01.md"]
        self.assertEqual(story["revision_count"], 3)
        self.assertEqual(story["head_revision_id"], "rev-g3")
        self.assertEqual([r["artifact_revision_id"] for r in story["revisions"]],
                         ["rev-g1", "rev-g2", "rev-g3"])  # 时间升序（修订链顺序）
        self.assertEqual(result["total_revisions"], 3)


if __name__ == "__main__":
    unittest.main()
