"""生命周期事件直发测试（REQ-20260920-193428 FR-001 evolution 源 / FR-002）。

- evolution recorder create_run → run_started（进程内直发，带 session_name/workload）
- complete_run / fail_run → run_finished（终态摘要从 runs 表取）
- ingestion notify 主路径 → run_finished（executor 轻量摘要）+ poller 兜底去重登记

跑法（在 evolution 目录）：
    python -m pytest tests/test_lifecycle_events.py -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


def _bus_events() -> list[dict]:
    """从总线缓冲提取 envelope 列表（publish 立即入缓冲，无需订阅）。"""
    from app.view.events import get_event_bus

    envelopes: list[dict] = []
    for _, frame in list(get_event_bus()._buffer):
        for line in frame.splitlines():
            if line.startswith("data:"):
                envelopes.append(json.loads(line[len("data:"):]))
    return envelopes


class _TempDbTest(unittest.TestCase):
    """临时 db fixture（与 test_trace_v2_recorder 相同模式）。"""

    def setUp(self) -> None:
        import app.core.db as db
        from app.core.settings import settings

        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = settings.evolution_db
        settings.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        db._conn = None
        db.init_db()

    def tearDown(self) -> None:
        import app.core.db as db
        from app.core.settings import settings

        if db._conn is not None:
            db._conn.close()
        db._conn = None
        settings.evolution_db = self.old_db
        self.tmp.cleanup()


class RecorderLifecycleEventsTest(_TempDbTest):

    def test_create_run_emits_started(self) -> None:
        from app.trace.recorder import EvolutionTraceRecorder

        recorder = EvolutionTraceRecorder()
        before = len(_bus_events())
        recorder.create_run("sess-evt-1", "evolution_eval", workload="evaluation")

        started = [e for e in _bus_events()[before:] if e["type"] == "run_started"]
        self.assertEqual(len(started), 1)
        data = started[0]["data"]
        self.assertEqual(data["trace_id"], started[0]["data"]["run"]["trace_id"])
        self.assertEqual(data["source"], "evolution")
        run = data["run"]
        self.assertEqual(run["session_name"], "sess-evt-1")
        self.assertEqual(run["workload"], "evaluation")
        self.assertEqual(run["status"], "running")
        self.assertTrue(run["started_at"])

    def test_complete_run_emits_finished_with_terminal_summary(self) -> None:
        from app.trace.recorder import EvolutionTraceRecorder

        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run("sess-evt-2", "evolution_evolve", workload="evolution")
        before = len(_bus_events())
        recorder.complete_run(handle.trace_id)

        finished = [e for e in _bus_events()[before:] if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 1)
        data = finished[0]["data"]
        self.assertEqual(data["source"], "evolution")
        run = data["run"]
        self.assertEqual(run["trace_id"], handle.trace_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["session_name"], "sess-evt-2")
        self.assertEqual(run["workload"], "evolution")
        self.assertIsNotNone(run["duration_ms"])

    def test_fail_run_emits_finished_failed(self) -> None:
        from app.trace.recorder import EvolutionTraceRecorder

        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run("sess-evt-3", "evolution_eval", workload="evaluation")
        before = len(_bus_events())
        recorder.fail_run(handle.trace_id, RuntimeError("boom"))

        finished = [e for e in _bus_events()[before:] if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["data"]["run"]["status"], "failed")
        self.assertIn("boom", finished[0]["data"]["run"].get("error") or "")


class NotifyFinishedEventTest(unittest.TestCase):

    def test_notify_emits_finished_with_executor_summary(self) -> None:
        import app.view.active as active_mod
        from app.ingestion import ingestion
        from contracts.trace import TraceRunSummary

        active_mod._reset_diff_state_for_test()
        fake_run = TraceRunSummary(
            trace_id="trace-exec-fin",
            workspace_id="ws", thread_id="th",
            session_name="写一部赛博修仙小说",
            workspace_path="/w", endpoint="screenplay.generate.stream",
            status="completed",
            started_at="2026-09-20T12:00:00+00:00",
            ended_at="2026-09-20T12:01:00+00:00",
            duration_ms=60000, event_count=42, path="p",
            workload="creation",
        )
        original = ingestion._fetch_trace_content
        ingestion._fetch_trace_content = (
            lambda tid, since_seq=0, traceparent=None: ([], fake_run, {})
        )
        try:
            before = len(_bus_events())
            ingestion._emit_finished_event_from_executor("trace-exec-fin")
        finally:
            ingestion._fetch_trace_content = original

        finished = [e for e in _bus_events()[before:] if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 1)
        data = finished[0]["data"]
        self.assertEqual(data["source"], "executor")
        self.assertEqual(data["run"]["status"], "completed")
        self.assertEqual(data["run"]["session_name"], "写一部赛博修仙小说")
        self.assertEqual(data["run"]["duration_ms"], 60000)
        # 去重登记：后续 poller 兜底 diff 不再重复发。
        self.assertIn("trace-exec-fin", active_mod._finished_emitted)

    def test_fetch_failure_publishes_nothing(self) -> None:
        import app.view.active as active_mod
        from app.ingestion import ingestion

        active_mod._reset_diff_state_for_test()
        original = ingestion._fetch_trace_content
        ingestion._fetch_trace_content = lambda *a, **k: None
        try:
            before = len(_bus_events())
            ingestion._emit_finished_event_from_executor("trace-missing")
        finally:
            ingestion._fetch_trace_content = original

        finished = [e for e in _bus_events()[before:] if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 0)

    def test_non_terminal_status_publishes_nothing(self) -> None:
        """awaiting_input（HITL）与 resume（running）的 notify 不得广播 run_finished。

        review R1（P0）：非终态 notify 无条件广播会把仍在运行的活跃行
        踢出大盘且 SSE 模式无自愈。
        """
        import app.view.active as active_mod
        from app.ingestion import ingestion
        from contracts.trace import TraceRunSummary

        for status in ("awaiting_input", "running"):
            with self.subTest(status=status):
                active_mod._reset_diff_state_for_test()
                fake_run = TraceRunSummary(
                    trace_id=f"trace-nonterm-{status}",
                    workspace_id="ws", thread_id="th", session_name="等待输入的任务",
                    workspace_path="/w", endpoint="e",
                    status=status,  # type: ignore[arg-type]
                    started_at="2026-09-20T12:00:00+00:00",
                    event_count=5, path="p", workload="creation",
                )
                original = ingestion._fetch_trace_content
                ingestion._fetch_trace_content = (
                    lambda tid, since_seq=0, traceparent=None, _r=fake_run: ([], _r, {})
                )
                try:
                    before = len(_bus_events())
                    ingestion._emit_finished_event_from_executor(f"trace-nonterm-{status}")
                finally:
                    ingestion._fetch_trace_content = original

                finished = [
                    e for e in _bus_events()[before:] if e["type"] == "run_finished"
                ]
                self.assertEqual(len(finished), 0)
                # 也不得登记去重（否则 poller 兜底语义被误伤）
                self.assertNotIn(f"trace-nonterm-{status}", active_mod._finished_emitted)


if __name__ == "__main__":
    unittest.main()
