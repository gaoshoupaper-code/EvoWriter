"""活跃 trace 的 token 实时透传（REQ-20261027-012631 FR-005 / DEC-010）。

list_active_runs 必须携带 owner_user_id 与 usage_total：evolution 用户层/作品层
的 token 累计需要实时计入运行中 trace（数字随创作增长），而运行中 trace 尚未
摄入 evolution 库——executor 内存是唯一数据源。

跑法（在 executor 目录）：
    .venv/Scripts/python.exe -m pytest tests/test_active_runs_usage.py -v
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.platform.agent.middleware.trace_middleware import TraceMiddleware
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary
from contracts.trace.usage import metrics_from_nodes


def _make_thread(workspace_path: str, user_id: str = "user-42") -> ThreadSummary:
    now = "2026-10-27T00:00:00+00:00"
    return ThreadSummary(
        thread_id="usage-thread",
        workspace_id="usage-ws",
        session_name="观测用量测试",
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
        user_id=user_id,
    )


class ActiveRunsUsageTest(unittest.TestCase):
    """运行中 trace 的 usage 契约：owner 与 token 累计随活跃行透传。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="active_usage_")
        self.workspace = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_active_run_carries_owner_and_usage(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"input_tokens": 700, "output_tokens": 300, "total_tokens": 1000},
        })
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"input_tokens": 2000, "output_tokens": 500, "total_tokens": 2500},
        })

        runs = recorder.list_active_runs()

        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["owner_user_id"], "user-42")
        self.assertEqual(run["usage_total"], 3500)

    def test_usage_accumulates_without_explicit_total(self) -> None:
        """usage 缺 total_tokens 时按 input+output 累加；无 usage 事件不计。"""
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        recorder.append_event(handle.trace_id, {
            "type": "tool_end",
            "status": "completed",
            "source": "runtime",
        })

        runs = recorder.list_active_runs()

        self.assertEqual(runs[0]["usage_total"], 150)

    def test_active_metrics_track_partial_usage_and_successful_calls(self) -> None:
        recorder = TraceRecorder()
        handle = recorder.create_run(_make_thread(self.workspace), "screenplay.generate.stream")
        for values in [
            {"type": "llm_end", "status": "completed", "duration_ms": 10000,
             "usage": {"input_tokens": 12000, "output_tokens": 3000}},
            {"type": "llm_start", "status": "running"},
            {"type": "llm_end", "status": "completed", "duration_ms": 30000,
             "usage": {"input_tokens": 8000, "output_tokens": 2000}},
            {"type": "llm_error", "status": "failed", "duration_ms": 60000},
        ]:
            recorder.append_event(handle.trace_id, {"source": "runtime", **values})

        metrics = recorder.list_active_runs()[0]["metrics"]
        self.assertEqual(metrics["input_tokens"], 20000)
        self.assertEqual(metrics["output_tokens"], 5000)
        self.assertEqual(metrics["total_tokens"], 25000)
        self.assertEqual(metrics["average_duration_ms"], 20000)
        self.assertTrue(metrics["total_partial"])

    def test_usage_survives_index_loss_and_cleared_on_finalize(self) -> None:
        """累加器不依赖 index（内存直读）；终态清出活跃列表后不再残留。"""
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"total_tokens": 800},
        })
        # 模拟 index 丢失：usage/owner 降级仍可用（累加器在内存，owner 退 None）。
        recorder._trace_workspace.pop(handle.trace_id)
        recorder._disk_miss_until[handle.trace_id] = float("inf")

        degraded = recorder.list_active_runs()
        self.assertEqual(degraded[0]["usage_total"], 800)
        self.assertIsNone(degraded[0]["owner_user_id"])

        # 终态收尾：清出活跃列表 + 累加器无泄漏。
        recorder._finalize_run(thread, handle.trace_id, "completed", 1000, None)
        self.assertEqual(recorder.list_active_runs(), [])
        self.assertNotIn(handle.trace_id, recorder._usage_totals)
        self.assertNotIn(handle.trace_id, recorder._trace_metrics)

    def test_metrics_replay_after_restart_and_duplicate_delivery(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        values = {"event_id": "llm-known", "type": "llm_end", "status": "completed", "source": "runtime",
                  "duration_ms": 10000, "usage": {"input_tokens": 12000, "output_tokens": 3000}}
        recorder.append_event(handle.trace_id, values)
        recorder.append_event(handle.trace_id, values)
        expected = recorder.list_active_runs()[0]["metrics"]
        self.assertEqual(expected["total_tokens"], 15000)
        run = recorder.find_run_by_trace_id(handle.trace_id)
        self.assertIsNotNone(run)

        restarted = TraceRecorder()
        restarted._rebuild_active_state(thread, run)
        self.assertEqual(restarted.list_active_runs()[0]["metrics"], expected)
        restarted.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "runtime",
            "duration_ms": 30000, "usage": {"input_tokens": 8000, "output_tokens": 2000},
        })
        metrics = restarted.list_active_runs()[0]["metrics"]
        self.assertEqual(metrics["total_tokens"], 25000)
        self.assertEqual(metrics["average_duration_ms"], 20000)

    def test_model_error_keeps_known_usage_without_a_success_sample(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        error = RuntimeError("failed after reporting usage")
        error.usage = {"input_tokens": 100, "output_tokens": 0}
        request = SimpleNamespace(model=SimpleNamespace(model_name="model"))

        TraceMiddleware(recorder, handle.trace_id, "writer")._record_model_error(request, time.perf_counter(), error)

        metrics = recorder.list_active_runs()[0]["metrics"]
        self.assertEqual(metrics["total_tokens"], 100)
        self.assertEqual(metrics["output_tokens"], 0)
        self.assertIsNone(metrics["average_duration_ms"])
        detail = recorder.read_run(thread, handle.trace_id)
        self.assertIsNotNone(detail)
        self.assertEqual(metrics_from_nodes(detail.nodes).model_dump(), metrics)

    def test_missing_history_does_not_make_metrics_block_resume(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        run = recorder.find_run_by_trace_id(handle.trace_id)
        (Path(thread.workspace_path) / run.path).unlink()
        restarted = TraceRecorder()

        restarted._rebuild_active_state(thread, run)

        self.assertIsNone(restarted.list_active_runs()[0]["metrics"]["total_tokens"])
        self.assertIn("metrics_replay_failed", restarted._capture_degraded[handle.trace_id])

    def test_legacy_inline_usage_is_replayed_on_restart(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        run = recorder.find_run_by_trace_id(handle.trace_id)
        # Older traces stored provider usage inside the inline model output.
        event = {
            "trace_id": handle.trace_id, "event_id": "legacy-llm-end", "sequence": 2,
            "type": "llm_end", "status": "completed", "source": "middleware",
            "timestamp": "2026-10-27T00:00:01+00:00", "model_name": "model", "duration_ms": 10000,
            "output": {"messages": [{"usage_metadata": {"input_tokens": 12000, "output_tokens": 3000}}]},
        }
        with (Path(thread.workspace_path) / run.path).open("a", encoding="utf-8") as file:
            file.write(json.dumps(event) + "\n")
        run.event_count = 2
        restarted = TraceRecorder()

        restarted._rebuild_active_state(thread, run)

        metrics = restarted.list_active_runs()[0]["metrics"]
        self.assertEqual((metrics["input_tokens"], metrics["output_tokens"], metrics["total_tokens"]), (12000, 3000, 15000))
        self.assertEqual(restarted.list_active_runs()[0]["usage_total"], 15000)
        detail = restarted.read_run(thread, handle.trace_id)
        self.assertEqual(metrics_from_nodes(detail.nodes).model_dump(), metrics)

    def test_terminal_index_keeps_last_call_without_an_active_poll(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": 10000,
            "usage": {"input_tokens": 12000, "output_tokens": 3000},
        })
        self.assertEqual(recorder.list_active_runs()[0]["metrics"]["total_tokens"], 15000)
        recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": 30000,
            "usage": {"input_tokens": 8000, "output_tokens": 2000},
        })

        with patch("app.platform.trace.recorder._notify_evolution") as notify:
            recorder.complete_run(thread, handle.trace_id)

        run = recorder.find_run_by_trace_id(handle.trace_id)
        metrics = getattr(run, "metrics", None)
        self.assertIsNotNone(metrics)
        self.assertEqual((metrics.input_tokens, metrics.output_tokens, metrics.total_tokens), (20000, 5000, 25000))
        self.assertEqual(metrics.average_duration_ms, 20000)
        self.assertFalse(metrics.total_partial)
        self.assertEqual(notify.call_args.args[3]["metrics"], metrics.model_dump())
        self.assertNotIn("metrics", run.run_snapshot)

    def test_terminal_cancelled_summary_marks_unfinished_call_usage_missing(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": 10000,
            "usage": {"input_tokens": 12000, "output_tokens": 3000},
        })
        recorder.append_event(handle.trace_id, {"type": "llm_start", "status": "running", "source": "middleware"})

        with patch("app.platform.trace.recorder._notify_evolution"):
            recorder.cancel_run(thread, handle.trace_id)

        run = recorder.find_run_by_trace_id(handle.trace_id)
        metrics = getattr(run, "metrics", None)
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics.total_tokens, 15000)
        self.assertTrue(metrics.total_partial)
        self.assertEqual(metrics.average_duration_ms, 10000)
        self.assertEqual(run.status, "cancelled")

    def test_terminal_metrics_do_not_depend_on_extra_projection(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": 10000,
            "usage": {"input_tokens": 12000, "output_tokens": 3000},
        })

        with patch.object(recorder, "_write_manifest", return_value=None), patch.object(recorder, "_export_summary"):
            with patch.object(recorder, "_read_events") as read_events, patch.object(recorder._projector, "project") as project:
                with patch("app.platform.trace.recorder._notify_evolution") as notify:
                    recorder.complete_run(thread, handle.trace_id)

        run = recorder.find_run_by_trace_id(handle.trace_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.metrics.total_tokens, 15000)
        read_events.assert_not_called()
        project.assert_not_called()
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[3]["metrics"]["total_tokens"], 15000)

    def test_damaged_event_file_keeps_known_metrics_and_completion_notification(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": 10000,
            "usage": {"input_tokens": 12000, "output_tokens": 3000},
        })
        with recorder._run_paths[handle.trace_id].open("a", encoding="utf-8") as file:
            file.write("{invalid-json}\n")

        with patch("app.platform.trace.recorder._notify_evolution") as notify:
            recorder.complete_run(thread, handle.trace_id)

        run = recorder.find_run_by_trace_id(handle.trace_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.metrics.total_tokens, 15000)
        self.assertEqual(run.integrity_status, "incomplete")
        notify.assert_called_once()

    def test_parent_cancel_reuses_already_read_events_for_final_metrics(self) -> None:
        from contracts.trace import CancelAudit

        child = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = child.create_run(thread, "screenplay.ab_run")
        child.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": 10000,
            "usage": {"input_tokens": 12000, "output_tokens": 3000},
        })
        child.append_event(handle.trace_id, {"type": "llm_start", "status": "running", "source": "middleware"})
        parent = TraceRecorder()
        parent.register_external_run(handle.trace_id, self.workspace)

        with patch("app.platform.trace.recorder._notify_evolution"):
            self.assertTrue(parent.seal_external_cancel(
                handle.trace_id, cancel_audit=CancelAudit(cancel_id="cancel-metrics", requested_by="user", reason="user_stop"),
            ))

        metrics = parent.find_run_by_trace_id(handle.trace_id).metrics
        self.assertEqual(metrics.total_tokens, 15000)
        self.assertTrue(metrics.total_partial)
        self.assertEqual(metrics.average_duration_ms, 10000)


if __name__ == "__main__":
    unittest.main()
