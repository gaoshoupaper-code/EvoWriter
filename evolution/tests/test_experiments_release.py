"""Candidate identity, production CAS, activation acknowledgement and credentials."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
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
