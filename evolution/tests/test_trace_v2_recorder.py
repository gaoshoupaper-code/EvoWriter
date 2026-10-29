from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.core.db as db
from app.core.settings import settings
from app.ingestion.projector import TraceProjector
from app.trace.recorder import EvolutionTraceRecorder
from app.trace_payloads import read_payload
from contracts.trace import TraceLogEvent, TraceRunSummary, TraceSpanLink
from contracts.trace.usage import TraceMetricsAccumulator, metrics_from_nodes


class EvolutionTraceV2RecorderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = settings.evolution_db
        self.old_payload_dir = settings.trace_payload_dir
        settings.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        settings.trace_payload_dir = str(Path(self.tmp.name) / "payloads")
        self.wal_patch = patch("app.trace.recorder._WAL_ROOT", Path(self.tmp.name) / "wal")
        self.wal_patch.start()
        db._conn = None
        db.init_db()

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        settings.evolution_db = self.old_db
        settings.trace_payload_dir = self.old_payload_dir
        self.wal_patch.stop()
        self.tmp.cleanup()

    def test_records_canonical_v2_payload_link_and_verified_manifest(self) -> None:
        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run(
            "eval-1",
            "evolution_eval",
            endpoint="eval-agent.run",
            workload="evaluation",
            links=[
                TraceSpanLink(
                    target_trace_id="trace-creation",
                    relation="consumes",
                    attributes={"dossier_id": "dossier-1", "dossier_version": 2},
                )
            ],
            external_refs={"evaluation_id": "eval-1", "dossier_id": "dossier-1"},
        )
        recorder.append_event(
            handle.trace_id,
            {
                "type": "run_meta",
                "status": "running",
                "source": "system",
                "input": {"prompt": "full evaluation prompt"},
            },
        )
        recorder.complete_run(handle.trace_id)

        run = db.query_one("SELECT * FROM runs WHERE trace_id=?", (handle.trace_id,))
        self.assertEqual(run["schema_version"], 2)
        self.assertEqual(run["service"], "evolution")
        self.assertEqual(run["workload"], "evaluation")
        self.assertEqual(run["integrity_status"], "verified")
        self.assertEqual(json.loads(run["links_json"])[0]["target_trace_id"], "trace-creation")
        external_refs = json.loads(run["external_refs_json"])
        self.assertEqual(external_refs["evaluation_id"], "eval-1")
        self.assertEqual(len(external_refs["w3c_trace_id"]), 32)
        self.assertEqual(len(external_refs["w3c_span_id"]), 16)
        self.assertTrue(external_refs["traceparent"].startswith("00-"))

        rows = db.query_all(
            "SELECT payload_json FROM event_payloads WHERE trace_id=? ORDER BY sequence",
            (handle.trace_id,),
        )
        event = next(json.loads(row["payload_json"]) for row in rows if json.loads(row["payload_json"])["type"] == "run_meta")
        self.assertIsNone(event.get("input"))
        payload_id = event["payload_refs"]["input"]["payload_id"]
        self.assertEqual(read_payload(payload_id), {"prompt": "full evaluation prompt"})
        receipt = db.query_one("SELECT * FROM trace_receipts WHERE trace_id=?", (handle.trace_id,))
        self.assertEqual(receipt["manifest_status"], "verified")

    def test_active_metrics_survive_resume_and_match_terminal_projection(self) -> None:
        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run("metrics-eval", "evolution_eval", workload="evaluation")
        for values in [
            {"type": "llm_end", "status": "completed", "duration_ms": 10000,
             "usage": {"input_tokens": 12000, "output_tokens": 3000}},
            {"type": "llm_end", "status": "completed", "duration_ms": 30000,
             "usage": {"input_tokens": 8000, "output_tokens": 2000}},
            {"type": "llm_error", "status": "failed", "duration_ms": 60000,
             "usage": {"input_tokens": 1000, "output_tokens": 0}},
            {"type": "llm_error", "status": "failed", "duration_ms": 2000},
            {"type": "tool_end", "status": "completed", "duration_ms": 90000,
             "tool_name": "read_file"},
        ]:
            recorder.append_event(handle.trace_id, {"source": "middleware", "model_name": "model", **values})
        expected = recorder.list_active_runs()[0]["metrics"]
        self.assertEqual(expected["total_tokens"], 26000)
        self.assertEqual(expected["average_duration_ms"], 20000)
        self.assertTrue(expected["total_partial"])

        recorder.flush_sync(handle.trace_id)
        restarted = EvolutionTraceRecorder()
        self.assertTrue(restarted.resume_run(handle.trace_id, "metrics-eval"))
        self.assertTrue(restarted.resume_run(handle.trace_id, "metrics-eval"))
        self.assertEqual(restarted.list_active_runs()[0]["metrics"], expected)
        restarted.fail_run(handle.trace_id, "terminal failure")
        nodes = db.query_all("SELECT * FROM nodes WHERE trace_id=?", (handle.trace_id,))
        self.assertEqual(metrics_from_nodes(nodes).model_dump(), expected)
        self.assertEqual(restarted.list_active_runs(), [])
        self.assertNotIn(handle.trace_id, restarted._trace_metrics)

    def test_self_observation_keeps_zero_and_partial_usage_independent(self) -> None:
        from app.trace.trace_middleware import _normalize_usage

        self.assertEqual(_normalize_usage({"input_tokens": 0, "output_tokens": 0})["total_tokens"], 0)
        self.assertIsNone(_normalize_usage({"output_tokens": 3000})["total_tokens"])
        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run("metrics-evolve", "evolution_evolve", workload="evolution")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware",
            "usage": {"input_tokens": 0, "output_tokens": 0}, "duration_ms": 0,
        })
        recorder.append_event(handle.trace_id, {"type": "llm_start", "status": "running", "source": "middleware"})
        metrics = recorder.list_active_runs()[0]["metrics"]
        self.assertEqual(metrics["input_tokens"], 0)
        self.assertEqual(metrics["total_tokens"], 0)
        self.assertEqual(metrics["average_duration_ms"], 0)
        self.assertFalse(metrics["total_partial"])

    def test_legacy_inline_usage_is_replayed_on_resume(self) -> None:
        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run("legacy-eval", "evolution_eval", workload="evaluation")
        event = recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware",
            "model_name": "model", "duration_ms": 10000,
        })
        recorder.flush_sync(handle.trace_id)
        legacy = event.model_dump(exclude_none=True)
        legacy["output"] = {"messages": [{"usage_metadata": {"input_tokens": 12000, "output_tokens": 3000}}]}
        db.execute("UPDATE event_payloads SET payload_json=? WHERE trace_id=? AND event_id=?",
                   (json.dumps(legacy), handle.trace_id, event.event_id))
        restarted = EvolutionTraceRecorder()

        self.assertTrue(restarted.resume_run(handle.trace_id, "legacy-eval"))

        metrics = restarted.list_active_runs()[0]["metrics"]
        self.assertEqual((metrics["input_tokens"], metrics["output_tokens"], metrics["total_tokens"]), (12000, 3000, 15000))
        restarted.complete_run(handle.trace_id)
        nodes = db.query_all("SELECT * FROM nodes WHERE trace_id=?", (handle.trace_id,))
        self.assertEqual(metrics_from_nodes(nodes).model_dump(), metrics)

    def test_finished_event_carries_final_metrics_without_an_active_poll(self) -> None:
        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run("finished-metrics", "evolution_eval", workload="evaluation")
        for duration, input_tokens, output_tokens in [(10000, 12000, 3000), (30000, 8000, 2000)]:
            recorder.append_event(handle.trace_id, {
                "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": duration,
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            })
        recorder.append_event(handle.trace_id, {"type": "llm_start", "status": "running", "source": "middleware"})

        with patch("app.view.events.get_event_bus") as get_bus:
            recorder.cancel_run(handle.trace_id)

        finished = [call.args[1] for call in get_bus.return_value.publish.call_args_list if call.args[0] == "run_finished"]
        self.assertEqual(len(finished), 1)
        metrics = finished[0]["run"].get("metrics")
        self.assertIsNotNone(metrics)
        self.assertEqual((metrics["input_tokens"], metrics["output_tokens"], metrics["total_tokens"]), (20000, 5000, 25000))
        self.assertEqual(metrics["average_duration_ms"], 20000)
        self.assertTrue(metrics["total_partial"])
        nodes = db.query_all("SELECT * FROM nodes WHERE trace_id=?", (handle.trace_id,))
        self.assertEqual(metrics_from_nodes(nodes).model_dump(), metrics)

    def test_finished_event_keeps_known_metrics_when_projection_fails(self) -> None:
        recorder = EvolutionTraceRecorder()
        handle = recorder.create_run("metrics-fallback", "evolution_eval", workload="evaluation")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end", "status": "completed", "source": "middleware", "duration_ms": 10000,
            "usage": {"input_tokens": 12000, "output_tokens": 3000},
        })

        with patch("app.ingestion.projector.TraceProjector.project", side_effect=RuntimeError("projection failed")):
            with patch("app.view.events.get_event_bus") as get_bus:
                recorder.complete_run(handle.trace_id)

        finished = [call.args[1] for call in get_bus.return_value.publish.call_args_list if call.args[0] == "run_finished"]
        self.assertEqual(finished[0]["run"]["metrics"]["total_tokens"], 15000)
        self.assertEqual(finished[0]["run"]["status"], "completed")
        self.assertEqual(recorder.list_active_runs(), [])


class TraceMetricsPairingTest(unittest.TestCase):
    def _metrics(self, start_fields: dict, end_fields: dict):
        run = TraceRunSummary(
            trace_id="pairing", workspace_id="ws", thread_id="thread", session_name="session",
            workspace_path="", endpoint="eval", status="cancelled", started_at="2026-10-29T00:00:00+00:00", path="",
        )
        base = {"trace_id": run.trace_id, "source": "middleware", "timestamp": "2026-10-29T00:00:01+00:00"}
        events = [
            TraceLogEvent(**base, event_id="start", sequence=1, type="llm_start", status="running", **start_fields),
            TraceLogEvent(**base, event_id="end", sequence=2, type="llm_end", status="completed", duration_ms=10000,
                          usage={"input_tokens": 12000, "output_tokens": 3000}, **end_fields),
        ]
        accumulator = TraceMetricsAccumulator()
        for event in events:
            accumulator.add_event(event)
        projected = metrics_from_nodes(TraceProjector().project(run, events).nodes)
        return accumulator.snapshot(terminal=True), projected

    def test_run_id_pairs_even_when_end_has_no_agent_name(self) -> None:
        actual, projected = self._metrics(
            {"run_id": "call", "agent_name": "writer", "model_name": "model"},
            {"run_id": "call", "agent_name": None, "model_name": "model"},
        )
        self.assertFalse(projected.total_partial)
        self.assertEqual(actual, projected)

    def test_different_models_without_run_id_do_not_close_the_same_start(self) -> None:
        actual, projected = self._metrics(
            {"agent_name": "writer", "model_name": "model-a"},
            {"agent_name": "writer", "model_name": "model-b"},
        )
        self.assertTrue(projected.total_partial)
        self.assertEqual(actual, projected)


if __name__ == "__main__":
    unittest.main()
