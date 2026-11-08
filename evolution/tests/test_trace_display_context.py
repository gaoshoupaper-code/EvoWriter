from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.core.db as db
from app.core.settings import settings
from app.view.display_context import display_contexts, trace_run_views


class TraceDisplayContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = settings.evolution_db
        settings.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        db._conn = None
        db.init_db()
        db.execute("CREATE TABLE IF NOT EXISTS self_evolution_runs (id TEXT PRIMARY KEY, payload TEXT, created_at TEXT)")

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        settings.evolution_db = self.old_db
        self.tmp.cleanup()

    def _experiment(self) -> None:
        value = {"name": "正文实验", "task_type": "chapter_text", "private": {"input": "不得读取的正文"},
            "samples": [{"id": "sample-a", "package_title": "样本A", "task_type": "chapter_text",
                "target": {"task_type": "chapter_text", "chapter": 3, "prompt": "不得显示"},
                "phase": "validation", "round": 2, "arm": "candidate", "repeat": 3, "attempt": 99,
                "trace_ids": ["generation-a", "generation-b"], "evaluation_trace_ids": ["evaluation-a"]}],
            "rounds": [{"round": 1, "trace_ids": ["other-improvement"]},
                       {"round": 2, "trace_ids": ["improvement-a"]}]}
        db.execute("INSERT INTO self_evolution_runs VALUES ('experiment-a', ?, 'now')", (json.dumps(value),))

    def test_snapshot_wins_and_only_display_fields_leave_the_boundary(self) -> None:
        db.execute("INSERT INTO workspace_cache VALUES ('ws', 'owner', '改名后', 0, 'now', 'now')")
        run = {"trace_id": "new", "workspace_id": "ws", "run_snapshot": {"display_context": {
            "workspace_title": "原书名", "task_phase": "chapter_text", "user_goal": "正文", "attempt": 5,
            "target": {"chapter": 3, "prompt": "正文", "volume_ids": [1]}}}}
        self.assertEqual(display_contexts([run])["new"], {"workspace_title": "原书名", "task_phase": "chapter_text",
            "target": {"chapter": 3, "volume_ids": [1]}})

    def test_historical_experiment_context_uses_actual_role_and_not_attempt(self) -> None:
        self._experiment()
        rows = [{"trace_id": tid} for tid in ("generation-a", "generation-b", "evaluation-a", "improvement-a")]
        rows[2]["workload"] = "creation"  # Historical migration defaults cannot override an actual scoring link.
        rows[2]["run_purpose"] = "user_generation"
        rows[3]["external_refs"] = {"experiment_id": "experiment-a"}
        with patch.object(db, "query_all", wraps=db.query_all) as query:
            contexts = display_contexts(rows)
        self.assertEqual(contexts["generation-a"]["operation"], "generation")
        self.assertEqual(contexts["evaluation-a"]["operation"], "evaluation")
        self.assertEqual(contexts["evaluation-a"]["sample_name"], "样本A")
        self.assertEqual(contexts["evaluation-a"]["repeat"], 3)
        self.assertEqual(contexts["improvement-a"]["operation"], "improvement")
        self.assertEqual(contexts["improvement-a"]["round"], 2)
        self.assertNotIn("sample_name", contexts["improvement-a"])
        self.assertTrue(contexts["generation-a"]["legacy"])
        self.assertNotIn("attempt", contexts["generation-a"])
        self.assertNotIn("prompt", contexts["generation-a"]["target"])
        self.assertLessEqual(query.call_count, 4)
        self.assertFalse(any("SELECT id,payload" in call.args[0] for call in query.call_args_list))

    def test_new_evaluation_resolves_context_before_trace_is_in_sample(self) -> None:
        self._experiment()
        result = display_contexts([{"trace_id": "not-yet-linked", "run_purpose": "evolution_eval",
            "external_refs": {"experiment_id": "experiment-a", "sample_id": "sample-a"}}], legacy=False)
        self.assertEqual(result["not-yet-linked"]["experiment_name"], "正文实验")
        self.assertEqual(result["not-yet-linked"]["sample_name"], "样本A")
        self.assertEqual(result["not-yet-linked"]["operation"], "evaluation")
        self.assertNotIn("legacy", result["not-yet-linked"])

    def test_partial_snapshot_keeps_saved_fields_and_adds_frozen_experiment_context(self) -> None:
        self._experiment()
        result = display_contexts([{"trace_id": "generation-a", "run_purpose": "optimization",
            "external_refs": {"experiment_id": "experiment-a", "sample_id": "sample-a"},
            "run_snapshot": {"display_context": {"task_type": "chapter_text", "target": {"chapter": 1}}}}])
        context = result["generation-a"]
        self.assertEqual(context["experiment_name"], "正文实验")
        self.assertEqual(context["sample_name"], "样本A")
        self.assertEqual(context["target"], {"chapter": 1})

    def test_lightweight_views_keep_snapshot_time_and_do_not_return_body(self) -> None:
        db.execute("INSERT INTO runs (trace_id,workspace_id,status,started_at,ingested_at,run_snapshot_json) "
                   "VALUES ('known','ws','completed','2026-11-08T12:00:00Z','now',?)",
                   (json.dumps({"display_context": {"workspace_title": "原名"}, "private_prompt": "正文"}),))
        values = trace_run_views(["known", "missing"])
        self.assertEqual(list(values), ["known"])
        self.assertEqual(values["known"]["started_at"], "2026-11-08T12:00:00Z")
        self.assertEqual(values["known"]["display_context"], {"workspace_title": "原名"})
        self.assertNotIn("run_snapshot", values["known"])
        self.assertNotIn("private_prompt", json.dumps(values))

    def test_experiment_response_adds_references_for_content_restricted_viewers_only_in_detail(self) -> None:
        from app.experiments.api import public_run

        self._experiment()
        db.execute("INSERT INTO runs (trace_id,workspace_id,status,started_at,ingested_at,run_purpose) "
                   "VALUES ('generation-a','ws','completed','2026-11-08T12:00:00Z','now','optimization')")
        run = json.loads(db.query_one("SELECT payload FROM self_evolution_runs")["payload"])
        run.update(status="completed", phase="validation", current_round=2, report={})
        detailed = public_run(run, can_read_content=False)
        self.assertEqual(detailed["trace_runs"]["generation-a"]["display_context"]["sample_name"], "样本A")
        self.assertNotIn("private", detailed)
        self.assertNotIn("不得读取的正文", json.dumps(detailed, ensure_ascii=False))
        self.assertNotIn("trace_runs", public_run(run, can_read_content=False, summary=True))

    def test_list_direct_detail_and_active_share_context_without_reading_body(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.view import active, traces

        self._experiment()
        db.execute("INSERT INTO runs (trace_id,workspace_id,status,started_at,ingested_at,run_purpose) "
                   "VALUES ('evaluation-a','evolution','running','2026-11-08T12:00:00Z','now','evolution_eval')")
        app = FastAPI()
        app.include_router(traces.router)
        app.include_router(active.router)
        with TestClient(app) as client, patch.object(traces, "read_payload", side_effect=AssertionError("body read")), \
                patch.object(active, "get_active_runs", return_value=[]), \
                patch.object(active, "_get_evolution_active_runs", return_value=[{"trace_id": "evaluation-a", "run_purpose": "evolution_eval"}]):
            listed = client.get("/traces?observation_scope=experiment").json()["items"][0]
            direct = client.get("/traces/evaluation-a").json()["run"]
            current = client.get("/active-runs?observation_scope=experiment").json()[0]
        self.assertEqual(listed["display_context"], direct["run_snapshot"]["display_context"])
        self.assertEqual(current["display_context"], listed["display_context"])
        self.assertEqual(current["started_at"], "2026-11-08T12:00:00Z")
        self.assertNotIn("run_snapshot", current)

    def test_evolution_start_resume_finish_preserve_snapshot_and_event_metadata(self) -> None:
        from app.trace.recorder import EvolutionTraceRecorder
        from app.view.events import EventBus

        self._experiment()
        bus = EventBus()
        with patch("app.trace.recorder._WAL_ROOT", Path(self.tmp.name) / "wal"), \
                patch.object(settings, "trace_payload_dir", str(Path(self.tmp.name) / "payloads")), \
                patch("app.view.events.get_event_bus", return_value=bus), \
                patch("app.trace.otlp.schedule_otlp_export"):
            recorder = EvolutionTraceRecorder()
            handle = recorder.create_run("sample-a", "evolution_eval", external_refs={
                "experiment_id": "experiment-a", "sample_id": "sample-a"})
            before = recorder.list_active_runs()[0]
            self.assertEqual(before["display_context"]["sample_name"], "样本A")
            db.execute("UPDATE self_evolution_runs SET payload=json_set(payload,'$.name','变化后的名字')")
            resumed = EvolutionTraceRecorder()
            self.assertTrue(resumed.resume_run(handle.trace_id))
            self.assertEqual(resumed.list_active_runs()[0]["display_context"], before["display_context"])
            recorder.complete_run(handle.trace_id)
        frames = [json.loads(frame.split("data: ", 1)[1]) for frame in bus.replay(0)]
        self.assertEqual([frame["type"] for frame in frames], ["run_started", "run_finished"])
        for frame in frames:
            self.assertEqual(frame["data"]["run"]["display_context"], before["display_context"])
            self.assertNotIn("run_snapshot", frame["data"]["run"])


if __name__ == "__main__":
    unittest.main()
