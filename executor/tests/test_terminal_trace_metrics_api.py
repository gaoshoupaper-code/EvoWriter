from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.routers.internal import get_trace_content
from contracts.trace import TraceRunSummary


def _run(status: str = "completed", metrics: dict | None = None) -> TraceRunSummary:
    return TraceRunSummary.model_validate({
        "trace_id": "terminal", "workspace_id": "ws", "thread_id": "thread", "session_name": "session",
        "workspace_path": "", "endpoint": "generate", "status": status,
        "started_at": "2026-10-29T00:00:00+00:00", "path": "trace.jsonl", "event_count": 10,
        "metrics": metrics,
    })


class TerminalTraceMetricsAPITest(unittest.TestCase):
    def test_terminal_summary_skips_event_file_when_since_already_covers_run(self) -> None:
        recorder = Mock()
        recorder.find_run_by_trace_id.return_value = _run(metrics={"total_tokens": 25000})
        recorder.read_trace_events.return_value = []
        with patch("app.routers.internal.get_trace_recorder", return_value=recorder):
            response = get_trace_content("terminal", since_seq=2147483647)

        recorder.read_trace_events.assert_not_called()
        self.assertEqual(response.events, [])
        self.assertEqual(response.run.metrics.total_tokens, 25000)

    def test_legacy_running_and_unread_events_keep_the_existing_read_path(self) -> None:
        for status, metrics, since in [
            ("completed", None, 2147483647),
            ("running", {"total_tokens": 25000}, 2147483647),
            ("completed", {"total_tokens": 25000}, 9),
        ]:
            with self.subTest(status=status, metrics=metrics, since=since):
                recorder = Mock()
                recorder.find_run_by_trace_id.return_value = _run(status, metrics)
                recorder.read_trace_events.return_value = []
                with patch("app.routers.internal.get_trace_recorder", return_value=recorder):
                    get_trace_content("terminal", since_seq=since)
                recorder.read_trace_events.assert_called_once_with("terminal", since_seq=since)


if __name__ == "__main__":
    unittest.main()
