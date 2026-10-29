from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.db as db
import app.view.active as active
from app.core.settings import settings
from app.view.traces import _to_trace_detail_lite, router
from contracts.trace import TraceDetail, TraceNode, TraceRunSummary


def _metrics(total: int = 15000) -> dict:
    return {
        "input_tokens": total * 4 // 5,
        "output_tokens": total // 5,
        "total_tokens": total,
        "input_partial": False,
        "output_partial": False,
        "total_partial": False,
        "average_duration_ms": 10000,
    }


class TraceLiveMetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = settings.evolution_db
        settings.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        db._conn = None
        db.init_db()
        active._reset_diff_state_for_test()
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.include_router(active.router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        active._reset_diff_state_for_test()
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        settings.evolution_db = self.old_db
        self.tmp.cleanup()

    def _run(self, tid: str, workload: str = "creation") -> None:
        db.execute(
            """INSERT INTO runs
               (trace_id, workspace_id, status, started_at, ingested_at, workload)
               VALUES (?, 'ws', 'completed', '2026-10-29', '2026-10-29', ?)""",
            (tid, workload),
        )

    def _node(self, tid: str, name: str, **values) -> None:
        row = {
            "kind": "llm", "status": "completed", "duration_ms": 10000,
            "usage_input": 12000, "usage_output": 3000, "usage_total": None,
            **values,
        }
        columns = ["node_id", "trace_id", *row]
        db.execute(
            f"INSERT INTO nodes ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            (name, tid, *row.values()),
        )

    def test_ac001_detail_exposes_live_model_only_metrics(self) -> None:
        run = TraceRunSummary(
            trace_id="live", workspace_id="ws", thread_id="t", session_name="s",
            workspace_path="", endpoint="test", status="running",
            started_at="2026-10-29", path="",
        )
        detail = TraceDetail(run=run, nodes=[
            TraceNode(node_id="llm:main", kind="llm", label="main", status="completed",
                      duration_ms=10000, usage={"input_tokens": 12000, "output_tokens": 3000}),
            TraceNode(node_id="llm:sub", kind="llm", label="sub", status="completed",
                      duration_ms=30000, usage={"input_tokens": 8000, "output_tokens": 2000}),
            TraceNode(node_id="tool", kind="tool", label="tool", status="completed", duration_ms=90000),
            TraceNode(node_id="llm:pending", kind="llm", label="pending", status="running"),
        ])
        result = _to_trace_detail_lite(detail).model_dump()
        self.assertEqual(result["metrics"]["input_tokens"], 20000)
        self.assertEqual(result["metrics"]["output_tokens"], 5000)
        self.assertEqual(result["metrics"]["total_tokens"], 25000)
        self.assertEqual(result["metrics"]["average_duration_ms"], 20000)
        self.assertFalse(result["metrics"]["total_partial"])
        self.assertEqual(result["run"]["status"], "running")

    def test_ac002_history_uses_lean_nodes_and_keeps_partial_fields(self) -> None:
        for workload in ("creation", "evaluation", "evolution"):
            self._run(workload, workload)
            self._node(workload, "llm:a", usage_input=None, usage_output=3000)
            self._node(workload, "llm:b", usage_input=8000, usage_output=2000, duration_ms=30000)
            self._node(workload, "tool", kind="tool", duration_ms=90000, usage_total=999999)
        with patch.object(db, "query_all", wraps=db.query_all) as query:
            result = self.client.get("/api/traces").json()
        self.assertEqual(result["total"], 3)
        for row in result["items"]:
            self.assertEqual(row["token_total"], 10000)
            self.assertEqual(row["metrics"]["input_tokens"], 8000)
            self.assertTrue(row["metrics"]["input_partial"])
            self.assertEqual(row["metrics"]["output_tokens"], 5000)
            self.assertFalse(row["metrics"]["output_partial"])
            self.assertTrue(row["metrics"]["total_partial"])
            self.assertEqual(row["metrics"]["average_duration_ms"], 20000)
        self.assertFalse(any("payload_json" in call.args[0] for call in query.call_args_list))

    def test_ac005_history_unknown_and_explicit_zero_differ(self) -> None:
        self._run("unknown")
        self._node("unknown", "llm:x", usage_input=None, usage_output=None)
        self._run("zero")
        self._node("zero", "llm:z", usage_input=0, usage_output=0)
        rows = {r["trace_id"]: r for r in self.client.get("/api/traces").json()["items"]}
        self.assertIsNone(rows["unknown"]["token_total"])
        self.assertEqual(rows["zero"]["token_total"], 0)

    def test_ac008_legacy_inline_usage_survives_structural_detail(self) -> None:
        self._run("legacy")
        self._node("legacy", "llm:legacy-end")
        event = {
            "trace_id": "legacy", "event_id": "legacy-end", "sequence": 1,
            "type": "llm_end", "status": "completed", "timestamp": "2026-10-29T00:00:00Z",
            "source": "runtime", "duration_ms": 10000,
            "output": {"messages": [{"usage_metadata": {"input_tokens": 12000, "output_tokens": 3000}}]},
        }
        db.execute(
            "INSERT INTO event_payloads (trace_id, sequence, type, timestamp, payload_json) VALUES (?, 1, 'llm_end', ?, ?)",
            ("legacy", event["timestamp"], json.dumps(event)),
        )
        listed = self.client.get("/api/traces").json()["items"][0]
        with patch("app.view.traces.read_payload", side_effect=AssertionError("must not hydrate body")):
            detail = self.client.get("/api/traces/legacy").json()
        self.assertEqual(listed["metrics"]["total_tokens"], 15000)
        self.assertEqual(detail["metrics"], listed["metrics"])
        self.assertNotIn("events", detail)

    def test_ac002_active_metrics_cover_both_sources_before_ingestion(self) -> None:
        active._active_cache = [{"trace_id": "creation", "metrics": _metrics(), "service": "executor"}]
        with patch.object(active, "_get_evolution_active_runs", return_value=[
            {"trace_id": kind, "metrics": _metrics(25000), "workload": kind}
            for kind in ("evaluation", "evolution")
        ]):
            rows = {r["trace_id"]: r for r in self.client.get("/api/active-runs").json()}
        self.assertEqual(set(rows), {"creation", "evaluation", "evolution"})
        self.assertEqual(rows["creation"]["metrics"]["total_tokens"], 15000)
        self.assertEqual(rows["evaluation"]["metrics"]["total_tokens"], 25000)
        self.assertFalse(rows["creation"]["ingested"])

    def test_ac009_source_failure_preserves_stale_metrics_and_recovery_replaces(self) -> None:
        first = {"trace_id": "live", "metrics": _metrics()}
        active._active_cache = [first]
        active._last_good_runs = [first]
        with patch("httpx.get", side_effect=ConnectionError("offline")):
            active._poll_once("http://executor")
        with patch.object(active, "_get_evolution_active_runs", return_value=[]):
            stale = self.client.get("/api/active-runs").json()
        self.assertEqual(stale[0]["metrics"]["total_tokens"], 15000)
        self.assertTrue(stale[0]["stats_stale"])
        response = unittest.mock.Mock()
        response.json.return_value = [{"trace_id": "live", "metrics": _metrics(25000)}]
        with patch("httpx.get", return_value=response):
            active._poll_once("http://executor")
        with patch.object(active, "_get_evolution_active_runs", return_value=[]):
            fresh = self.client.get("/api/active-runs").json()
        self.assertEqual(fresh[0]["metrics"]["total_tokens"], 25000)
        self.assertFalse(fresh[0]["stats_stale"])


if __name__ == "__main__":
    unittest.main()
