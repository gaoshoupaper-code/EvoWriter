from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import app.core.db as db
from app.core.settings import settings
from app.trace.facts import (
    append_outcome,
    append_release_event,
)


class TraceV2FactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = settings.evolution_db
        self.old_payload_dir = settings.trace_payload_dir
        settings.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        settings.trace_payload_dir = str(Path(self.tmp.name) / "payloads")
        db._conn = None
        db.init_db()
        db.execute(
            """INSERT INTO runs
               (trace_id, workspace_id, thread_id, session_name, endpoint, status,
                started_at, event_count, ingested_at, schema_version, service, workload,
                integrity_status)
               VALUES ('trace-source', 'ws', 'thread', 'session', 'create', 'completed',
                       '2026-01-01T00:00:00+00:00', 1, '2026-01-01T00:00:01+00:00',
                       2, 'executor', 'creation', 'incomplete')"""
        )

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        settings.evolution_db = self.old_db
        settings.trace_payload_dir = self.old_payload_dir
        self.tmp.cleanup()

    def test_release_state_records_commit_separately_from_activation(self) -> None:
        append_release_event(
            release_id="release-1", status="committed", candidate_id="candidate-1",
            actor_user_id="user-1",
        )
        with self.assertRaises(ValueError):
            append_release_event(
                release_id="release-1", status="activated", candidate_id="candidate-1",
                actor_user_id="user-1",
            )
        append_release_event(
            release_id="release-1", status="registry_promoted", candidate_id="candidate-1",
            actor_user_id="user-1",
        )
        append_release_event(
            release_id="release-1", status="executor_refresh_ack", candidate_id="candidate-1",
            actor_user_id="user-1",
        )
        append_release_event(
            release_id="release-1", status="activated", candidate_id="candidate-1",
            actor_user_id="user-1",
        )
        rows = db.query_all("SELECT status FROM release_events_v2 ORDER BY created_at, rowid")
        self.assertEqual(
            [row["status"] for row in rows],
            ["committed", "registry_promoted", "executor_refresh_ack", "activated"],
        )
