from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request

import app.core.db as db
from app.core.settings import settings
from app.view.traces import refresh_executor_trace
from contracts.trace import TraceLogEvent, TraceRunSummary


TRACE_ID = "trace-live-test"


def _event(sequence: int, event_type: str) -> TraceLogEvent:
    return TraceLogEvent(
        trace_id=TRACE_ID,
        event_id=f"event-{sequence}",
        sequence=sequence,
        type=event_type,
        status="running",
        timestamp=f"2026-07-29T05:32:4{sequence}+00:00",
        source="runtime",
        schema_version=2,
        run_id="llm-1" if event_type.startswith("llm_") else None,
        model_name="test-model" if event_type.startswith("llm_") else None,
    )


def _run(event_count: int) -> TraceRunSummary:
    return TraceRunSummary(
        trace_id=TRACE_ID,
        workspace_id="ab-workspace",
        thread_id="ab-thread",
        session_name="evolve-ab",
        workspace_path="",
        endpoint="screenplay.ab_run",
        status="running",
        started_at="2026-07-29T05:32:41+00:00",
        event_count=event_count,
        path="",
        schema_version=2,
        service="executor",
        workload="creation",
        purpose="evolution",
        integrity_status="incomplete",
    )


class TraceLiveRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = settings.evolution_db
        self.old_payload_dir = settings.trace_payload_dir
        settings.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        settings.trace_payload_dir = str(Path(self.tmp.name) / "payloads")
        db._conn = None
        db.init_db()
        self.request = Request({
            "type": "http",
            "headers": [(b"traceparent", b"00-live-refresh")],
        })

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        settings.evolution_db = self.old_db
        settings.trace_payload_dir = self.old_payload_dir
        self.tmp.cleanup()

    @patch("app.ingestion.ingestion._fetch_trace_content")
    def test_refresh_ingests_first_snapshot_then_only_new_events(self, fetch) -> None:
        first_events = [_event(1, "run_start"), _event(2, "llm_start")]
        fetch.return_value = (first_events, _run(2), {})

        first = refresh_executor_trace(TRACE_ID, self.request)

        self.assertEqual(first.run.status, "running")
        self.assertEqual(first.run.event_count, 2)
        fetch.assert_called_once_with(TRACE_ID, 0, "00-live-refresh")

        fetch.reset_mock()
        fetch.return_value = ([_event(3, "llm_end")], _run(3), {})

        second = refresh_executor_trace(TRACE_ID, self.request)

        self.assertEqual(second.run.event_count, 3)
        self.assertGreaterEqual(len(second.nodes), 1)
        fetch.assert_called_once_with(TRACE_ID, 2, "00-live-refresh")
        rows = db.query_all(
            "SELECT sequence FROM event_payloads WHERE trace_id=? ORDER BY sequence",
            (TRACE_ID,),
        )
        self.assertEqual([row["sequence"] for row in rows], [1, 2, 3])

    @patch("app.ingestion.ingestion._fetch_trace_content")
    def test_ac001_live_metrics_follow_incremental_model_calls_without_recounting(self, fetch) -> None:
        end = TraceLogEvent.model_validate(_event(3, "llm_end").model_dump() | {
            "status": "completed", "duration_ms": 10000,
            "usage": {"input_tokens": 12000, "output_tokens": 3000},
        })
        # Validate through ingestion and projection, rather than only a mocked summary.
        fetch.return_value = ([_event(1, "run_start"), _event(2, "llm_start"), end], _run(3), {})
        first = refresh_executor_trace(TRACE_ID, self.request)
        self.assertEqual(first.metrics.input_tokens, 12000)
        self.assertEqual(first.metrics.output_tokens, 3000)
        self.assertEqual(first.metrics.average_duration_ms, 10000)

        second_start = _event(4, "llm_start").model_copy(update={"run_id": "llm-2", "agent_name": "writing"})
        second_end = TraceLogEvent.model_validate(_event(5, "llm_end").model_dump() | {
            "run_id": "llm-2", "agent_name": "writing", "status": "completed",
            "duration_ms": 30000, "usage": {"input_tokens": 8000, "output_tokens": 2000},
        })
        fetch.return_value = ([second_start, second_end], _run(5), {})
        second = refresh_executor_trace(TRACE_ID, self.request)
        self.assertEqual(second.run.status, "running")
        self.assertEqual(second.metrics.input_tokens, 20000)
        self.assertEqual(second.metrics.output_tokens, 5000)
        self.assertEqual(second.metrics.total_tokens, 25000)
        self.assertEqual(second.metrics.average_duration_ms, 20000)

        fetch.return_value = ([], _run(5), {})
        repeated = refresh_executor_trace(TRACE_ID, self.request)
        self.assertEqual(repeated.metrics, second.metrics)


if __name__ == "__main__":
    unittest.main()
