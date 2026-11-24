"""Candidate identity, production CAS, activation acknowledgement and credentials."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_experiments import DatabaseTest, run_record


class ExperimentReleaseTest(DatabaseTest):
    def test_legacy_mutation_of_automatic_session_is_blocked_even_for_superadmin(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.evolve import api, db as sessions
        session_id = "experiment-controlled-r1"
        sessions.create_session(session_id, case_id="experiment:controlled", status="running")
        app = FastAPI()
        @app.middleware("http")
        async def identity(request, call_next):
            request.state.is_super_admin = True
            return await call_next(request)
        app.include_router(api.router, prefix="/api")
        with patch("app.evolve.api.ev_db.update_session") as mutate:
            response = TestClient(app).post(f"/api/evolve/sessions/{session_id}/stop")
        self.assertEqual(response.status_code, 409)
        mutate.assert_not_called()
        self.assertEqual(sessions.get_session(session_id)["status"], "running")

    def test_automatic_session_legacy_detail_cannot_bypass_content_permission(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.evolve import api, db as sessions
        session_id = "experiment-protected-r1"
        sessions.create_session(session_id, case_id="experiment:protected", status="done")
        privileged = [False]
        app = FastAPI()
        @app.middleware("http")
        async def identity(request, call_next):
            request.state.is_super_admin = privileged[0]
            return await call_next(request)
        app.include_router(api.router, prefix="/api")
        client = TestClient(app)
        with patch("app.evolve.api._try_read_doc", return_value={"meta": {}, "body": "PRIVATE_REPORT_QUOTE"}):
            response = client.get(f"/api/evolve/sessions/{session_id}")
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("PRIVATE_REPORT_QUOTE", response.text)
            privileged[0] = True
            response = client.get(f"/api/evolve/sessions/{session_id}")
            self.assertEqual(response.status_code, 200)
            self.assertIn("PRIVATE_REPORT_QUOTE", response.text)

    def test_automatic_session_legacy_messages_cannot_bypass_content_permission(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.evolve import api, db as sessions
        session_id = "experiment-protected-r2"
        sessions.create_session(session_id, case_id="experiment:protected", status="done")
        app = FastAPI()
        @app.middleware("http")
        async def identity(request, call_next):
            request.state.is_super_admin = False
            return await call_next(request)
        app.include_router(api.router, prefix="/api")
        with patch("app.evolve.evolve_repo.EvolveMessagesRepo.list_by_session", return_value=[{"role": "assistant", "content": "PRIVATE_DIALOGUE_QUOTE"}]):
            response = TestClient(app).get(f"/api/evolve/sessions/{session_id}/messages")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("PRIVATE_DIALOGUE_QUOTE", response.text)

    def test_cas_race_marks_candidate_stale(self):
        from app.experiments import repo, release
        run = run_record()
        repo.put_run(run)
        with patch("app.experiments.clients.production", side_effect=[run["baseline"], {"version": 8, "commit": "c" * 40}]), \
             patch("app.experiments.clients.promote", side_effect=ValueError("生产版本已变化")):
            result = release.publish_run(run["id"], "developer")
        self.assertEqual(result["status"], "stale")
        self.assertNotIn("published", result)

    def test_production_version_change_marks_stale_without_promoting(self):
        from app.experiments import repo, release
        run = run_record()
        repo.put_run(run)
        # Same commit, different release number must still invalidate acceptance.
        with patch("app.experiments.clients.production", return_value={"commit": run["baseline"]["commit"], "version": 8}), patch("app.experiments.clients.promote") as promote:
            with self.assertRaisesRegex(ValueError, "过期|变化"):
                release.publish_run(run["id"], "developer")
        promote.assert_not_called()
        self.assertEqual(repo.get_run(run["id"])["status"], "stale")

    def test_promotion_uses_verified_commit_and_cas(self):
        from app.experiments import repo, release
        run = run_record()
        repo.put_run(run)
        promoted = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64,
                    "reload_notified": True, "activation_status": "activated", "observed_commit": "b" * 40}
        with patch("app.experiments.clients.production", return_value=run["baseline"]), patch("app.experiments.clients.promote", return_value=promoted) as promote, patch("app.experiments.clients.runtime", return_value={"commit": "b" * 40, "artifact_digest": "2" * 64}):
            result = release.publish_run(run["id"], "developer")
        sent = promote.call_args.args[0]
        self.assertEqual(sent["source_commit"], "b" * 40)
        self.assertEqual(sent["expected_production_commit"], "a" * 40)
        self.assertEqual(sent["expected_production_version"], 7)
        self.assertEqual(result["status"], "activated")

    def _automatic_run(self, candidate_score=4.1):
        run = run_record()
        candidate = run["best_candidate"]
        run.update(auto_publish=True, task_type="chapter_text", flow_version=3, judge_repeats=1,
                   status="running", phase="exploration", best_candidate=None, report={},
                   private={"actor": "initiator"})
        run["rounds"] = [{"round": 1, "status": "exploration", "source_commit": candidate["commit"],
                          "digest": candidate["digest"]}]
        run["samples"] = [{"arm": arm, "phase": phase, "round": round_, "package_id": "opt", "repeat": 1,
                           "completed": True, "status": "completed", "scores": {"overall": score}}
                          for arm, phase, round_, score in (("baseline", "baseline", 0, 4),
                                                             ("candidate", "exploration", 1, candidate_score))]
        return run

    @patch.dict("os.environ", {"WRITING_AGENT_ID": "text"})
    def test_passing_improvement_queues_and_automatically_publishes_the_fixed_candidate(self):
        from app.experiments import repo, runner
        run = self._automatic_run()
        repo.put_run(run)
        promoted = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64}
        with patch("app.experiments.clients.production", return_value=run["baseline"]), \
             patch("app.experiments.clients.promote", return_value=promoted) as promote, \
             patch("app.experiments.clients.runtime", return_value=promoted):
            runner.advance_one(run["id"])
            queued = repo.get_run(run["id"])
            self.assertEqual((queued["status"], queued["phase"]), ("running", "publish"))
            self.assertTrue(queued["report"]["passed"])
            promote.assert_not_called()
            runner.advance_one(run["id"])
            runner.advance_one(run["id"])
        result = repo.get_run(run["id"])
        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["published"], promoted)
        self.assertEqual(result["approved_by"], "initiator")
        self.assertTrue(result["approved_automatically"])
        self.assertEqual([event["kind"] for event in result["events"]], ["publish_ready", "auto_approved", "activated"])
        self.assertEqual([sample["phase"] for sample in result["samples"]], ["baseline", "exploration"])
        promote.assert_called_once_with({"source_commit": promoted["commit"],
            "version_note": f"实验 {run['id']} 改进题复测达标，自动发布", "expected_production_commit": run["baseline"]["commit"],
            "expected_production_version": run["baseline"]["version"]})

    @patch.dict("os.environ", {"WRITING_AGENT_ID": "text"})
    def test_auto_publish_skips_below_threshold_and_stops_before_promotion(self):
        from app.experiments import repo, runner
        below = self._automatic_run(candidate_score=4.09)
        below["id"] = "below-threshold"
        stopped = self._automatic_run()
        stopped["id"] = "stop-before-publish"
        repo.put_run(below)
        repo.put_run(stopped)
        with patch("app.experiments.clients.promote") as promote, patch.object(runner, "start_worker"):
            runner.advance_one(below["id"])
            runner.advance_one(stopped["id"])
            runner.request_stop(stopped["id"])
            try:
                runner.advance_one(stopped["id"])
            finally:
                runner._stops.pop(stopped["id"], None)
            promote.assert_not_called()
        self.assertEqual(repo.get_run(below["id"])["phase"], "evolving")
        outcome = repo.get_run(stopped["id"])
        self.assertEqual(outcome["status"], "stopped")
        self.assertNotIn("approved_automatically", outcome)

    @patch.dict("os.environ", {"WRITING_AGENT_ID": "text"})
    def test_partial_gain_becomes_next_round_base_without_moving_release_baseline(self):
        from app.experiments import repo, runner

        db_path = Path(self.enterContext(TemporaryDirectory())) / "evolution.db"
        self.enterContext(patch.object(self.db.settings, "evolution_db", str(db_path)))
        manuscript_path = "正文/第001章.md"
        for scenario, second_score in (("lower", 4.06), ("tied", 4.09)):
            with self.subTest(second_round=scenario):
                run = self._automatic_run(candidate_score=4.09)
                run.update(id=f"partial-gain-{scenario}", assessment_version=2, max_rounds=3)
                baseline, first, second, third = (letter * 40 for letter in "abcd")
                run["rounds"][0]["base_commit"] = baseline
                for sample, commit in zip(run["samples"], (baseline, first), strict=True):
                    number = sample["round"]
                    sample.update(id=f"sample-{number}", source_commit=commit, task_type="chapter_text",
                                  package_title="同一改进题", input_revision="same-input", target={"chapter": 1},
                                  artifacts={manuscript_path: f"第 {number} 轮正文"})
                    sample["scores"]["dimensions"] = {"场景空间": 4 - number}
                repo.put_run(run)

                scores = {2: second_score, 3: 4.1}

                def add_scored_sample(active_run, package_ids, *, arm, phase, round_, repeats, commit, digest):
                    self.assertEqual((package_ids, arm, phase, repeats), (["opt"], "candidate", "exploration", 1))
                    active_run["samples"].append({**run["samples"][1], "id": f"sample-{round_}",
                                                  "round": round_, "source_commit": commit, "artifact_digest": digest,
                                                  "artifacts": {manuscript_path: f"第 {round_} 轮正文"},
                                                  "scores": {"overall": scores[round_],
                                                             "dimensions": {"场景空间": round_}}})

                candidates = [{"commit": commit, "digest": str(index) * 64, "summary": "继续改版", "diff": "+修订"}
                              for index, commit in enumerate((second, third), 3)]

                def improve_candidate(active_run, record, _evidence):
                    previous = record["round"] - 1
                    feedback = record["failure_feedback"]
                    persisted = repo.get_run(active_run["id"])["rounds"][-1]["failure_feedback"]
                    self.assertEqual(persisted, feedback)
                    self.assertEqual(feedback["round"], previous)
                    self.assertEqual([(case["baseline_id"], case["candidate_id"]) for case in feedback["cases"]],
                                     [("sample-0", f"sample-{previous}")])
                    return candidates[previous - 1]

                with patch("app.experiments.agent.improve_candidate", side_effect=improve_candidate) as improve, \
                     patch.object(runner, "_add_samples", side_effect=add_scored_sample), \
                     patch("app.experiments.clients.promote") as promote:
                    for phase in ("evolving", "exploration", "evolving", "exploration", "publish"):
                        runner.advance_one(run["id"])
                        saved = repo.get_run(run["id"])
                        self.assertEqual(saved["phase"], phase)
                    promote.assert_not_called()

                self.assertEqual(improve.call_count, 2)
                self.assertEqual(saved["baseline"], run["baseline"])
                self.assertEqual([record["base_commit"] for record in saved["rounds"]], [baseline, first, first])
                self.assertEqual([record["report"]["baseline"]["quality"] for record in saved["rounds"]], [4, 4, 4])
                self.assertEqual([record["report"]["passed"] for record in saved["rounds"]], [False, False, True])
                self.assertEqual(saved["best_candidate"]["commit"], third)
                self.assertTrue(saved["report"]["passed"])

    @patch.dict("os.environ", {"WRITING_AGENT_ID": "text"})
    def test_missing_comparable_manuscript_preserves_round_and_stops(self):
        from app.experiments import repo, runner

        db_path = Path(self.enterContext(TemporaryDirectory())) / "evolution.db"
        self.enterContext(patch.object(self.db.settings, "evolution_db", str(db_path)))
        run = self._automatic_run(candidate_score=4.09)
        run["assessment_version"] = 2
        first_round = run["rounds"][0]
        first_round["base_commit"] = run["baseline"]["commit"]
        for sample, commit in zip(run["samples"], (first_round["base_commit"], first_round["source_commit"]), strict=True):
            sample.update(id=f"sample-{sample['round']}", source_commit=commit, task_type="chapter_text",
                          package_title="同一改进题", input_revision="same-input", target={"chapter": 1})
        run["samples"][0]["artifacts"] = {"正文/第001章.md": "原稿仍可读取"}
        repo.put_run(run)

        with patch("app.experiments.agent.improve_candidate") as improve, patch.object(runner, "_add_samples") as dispatch:
            runner.advance_one(run["id"])
            previous = repo.get_run(run["id"])
            self.assertEqual(previous["rounds"][0]["status"], "not_improved")
            runner.advance_one(run["id"])
        improve.assert_not_called()
        dispatch.assert_not_called()

        saved = repo.get_run(run["id"])
        self.assertEqual((saved["status"], saved["current_round"]), ("failed", 1))
        self.assertIn("第 1 轮缺少完整的同题复盘资料", saved["end_reason"])
        self.assertEqual(saved["rounds"], previous["rounds"])
        self.assertEqual(saved["samples"], previous["samples"])

    def test_auto_publish_recovery_checks_pointer_before_retrying(self):
        from app.experiments import release, repo
        promoted = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64}
        for interrupted_before_promotion in (True, False):
            with self.subTest(interrupted_before_promotion=interrupted_before_promotion):
                run = run_record()
                run.update(auto_publish=True, status="publishing", phase="publish", publish_status="publishing",
                           approved_automatically=True, approved_commit=promoted["commit"], approved_by="initiator")
                repo.event(run, "auto_approved", "改进题复测达标，自动发布。")
                repo.put_run(run)
                current = run["baseline"] if interrupted_before_promotion else promoted
                with patch("app.experiments.clients.production", return_value=current), \
                     patch("app.experiments.clients.promote", return_value=promoted) as promote, \
                     patch("app.experiments.clients.runtime", return_value=promoted):
                    result = release.recover_run(run["id"])
                self.assertEqual(result["status"], "activated")
                self.assertEqual(result["published"], promoted)
                self.assertEqual(promote.call_count, int(interrupted_before_promotion))
                self.assertEqual([event["kind"] for event in result["events"]], ["auto_approved", "activated"])

    def test_pointer_change_without_loaded_commit_is_not_activation(self):
        from app.experiments import repo, release
        run = run_record()
        repo.put_run(run)
        promoted = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64,
                    "reload_notified": False, "activation_status": "unconfirmed", "observed_commit": "a" * 40}
        with patch("app.experiments.clients.production", return_value=run["baseline"]), patch("app.experiments.clients.promote", return_value=promoted), patch("app.experiments.clients.runtime", return_value={"commit": "a" * 40}):
            result = release.publish_run(run["id"], "developer")
        self.assertEqual(result["status"], "publish_failed")
        self.assertNotEqual(result["loaded_revision"], "b" * 40)

    def test_missing_runtime_digest_cannot_confirm_activation(self):
        from app.experiments import repo, release
        run = run_record()
        repo.put_run(run)
        promoted = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64,
                    "reload_notified": True, "activation_status": "activated", "observed_commit": "b" * 40}
        with patch("app.experiments.clients.production", return_value=run["baseline"]), \
             patch("app.experiments.clients.promote", return_value=promoted), \
             patch("app.experiments.clients.runtime", return_value={"commit": "b" * 40, "artifact_digest": None}):
            result = release.publish_run(run["id"], "developer")
        self.assertEqual(result["status"], "publish_failed")
        self.assertIsNone(result["loaded_revision"])

    def test_restore_without_digest_cannot_claim_restored(self):
        from app.experiments import repo, release
        run = run_record()
        run.update(status="publish_failed", published={"version": 8, "commit": "b" * 40})
        repo.put_run(run)
        restored = {"version": 9, "commit": "a" * 40, "artifact_digest": None,
                    "reload_notified": True, "activation_status": "activated", "observed_commit": "a" * 40}
        with patch("app.experiments.clients.restore", return_value=restored), \
             patch("app.experiments.clients.runtime", return_value={"commit": "a" * 40, "artifact_digest": "1" * 64}):
            result = release.restore_run(run["id"], "developer")
        self.assertEqual(result["rollback_status"], "failed")

    def test_restore_passes_failed_release_identity_and_reports_failure(self):
        from app.experiments import repo, release
        run = run_record()
        run.update(status="publish_failed", publish_status="unconfirmed", published={"version": 8, "commit": "b" * 40})
        repo.put_run(run)
        with patch("app.experiments.clients.restore", side_effect=RuntimeError("恢复失败")) as restore:
            result = release.restore_run(run["id"], "developer")
        self.assertEqual(restore.call_args.args[0]["expected_production_commit"], "b" * 40)
        self.assertEqual(result["rollback_status"], "failed")

    def test_public_detail_never_includes_frozen_credentials(self):
        from app.experiments.api import public_run
        run = run_record()
        run["private"] = {"model": {"api_key_enc": "PRIVATE_SECRET"}, "judges": [{"api_key_enc": "PRIVATE_SECRET"}]}
        run["samples"] = [{"artifacts": {"正文/第1章.md": "NOVEL_CONTENT"}, "trace_ids": []}]
        public = public_run(run, can_read_content=False)
        self.assertNotIn("PRIVATE_SECRET", str(public))
        self.assertNotIn("NOVEL_CONTENT", str(public))

    def test_non_superadmin_projection_excludes_all_derived_source_text(self):
        from app.experiments.api import public_run
        run = run_record()
        run["samples"] = [{"status": "completed", "completed": True, "trace_ids": ["trace-one"],
            "artifacts": {"正文/第001章.md": "SECRET_ARTIFACT"},
            "checks": {"complete": True, "missing": [], "text_evidence": {
                "initial_draft": "SECRET_DRAFT", "review_output": "SECRET_REVIEW", "revised": True}},
            "scores": {"overall": 4.0, "dimensions": {"逻辑连续": 4.0}, "reasons": {"逻辑连续": "SECRET_QUOTE"}}}]
        run["rounds"] = [{"round": 1, "status": "passed", "summary": "SECRET_REPORT", "diff": "+SECRET_SOURCE"}]
        public = public_run(run, can_read_content=False)
        for source in ("SECRET_ARTIFACT", "SECRET_DRAFT", "SECRET_REVIEW", "SECRET_QUOTE", "SECRET_REPORT", "SECRET_SOURCE"):
            self.assertNotIn(source, str(public))
        self.assertEqual(public["samples"][0]["scores"]["overall"], 4.0)
        self.assertTrue(public["samples"][0]["checks"]["complete"])
        privileged = public_run(run, can_read_content=True)
        self.assertIn("SECRET_REVIEW", str(privileged))
        self.assertIn("SECRET_SOURCE", str(privileged))

    def test_failure_review_projection_requires_content_access(self):
        from app.experiments.api import public_run

        run = self._automatic_run()
        run["assessment_version"] = 2
        review = {"round": 1, "decision": "change_direction", "reason": "REVIEW_REASON",
                  "manuscript_location": "正文/第001章.md:12", "evidence_refs": ["baseline", "candidate"]}
        run["rounds"][0].update(failure_review=review, failure_feedback={"summary": "RAW_FEEDBACK"},
                                failure_feedback_reads={"baseline": ["manuscript", "scores"]})

        privileged = public_run(run, can_read_content=True)["rounds"][0]
        restricted = public_run(run, can_read_content=False)["rounds"][0]
        self.assertEqual(privileged["failure_review"], review)
        self.assertNotIn("failure_review", restricted)
        for round_ in (privileged, restricted):
            self.assertNotIn("failure_feedback", round_)
            self.assertNotIn("failure_feedback_reads", round_)


class ExperimentKeyAuthTest(unittest.TestCase):
    def test_frozen_key_endpoint_requires_the_real_notify_token(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.notify_auth import NotifyTokenMiddleware
        from app.experiments.api import internal_router

        app = FastAPI()
        app.add_middleware(NotifyTokenMiddleware)
        app.include_router(internal_router, prefix="/api")
        with patch("app.core.notify_auth.settings.notify_token", "unit-secret"), patch("app.experiments.repo.frozen_model_credentials", return_value={"api_key": "frozen-key", "base_url": "https://model.invalid/v1", "model": "gpt-test"}):
            client = TestClient(app)
            endpoint = "/api/ingestion/experiment-key/experiment-test"
            self.assertIn(client.get(endpoint).status_code, (401, 403))
            self.assertIn(client.get(endpoint, headers={"X-Notify-Token": "wrong"}).status_code, (401, 403))
            response = client.get(endpoint, headers={"X-Notify-Token": "unit-secret"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["api_key"], "frozen-key")


if __name__ == "__main__":
    unittest.main()
