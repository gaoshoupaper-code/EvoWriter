"""Bounded experiment responses preserve historical usage and access boundaries."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_experiments import DatabaseTest, run_record
from app.experiments import api, repo


def sample_record(identifier="sample-one", attempts=None):
    return {"id": identifier, "phase": "baseline", "round": 0, "arm": "baseline",
            "status": "completed", "completed": True, "request_saved": True, "trace_ids": [],
            "artifacts": {"正文.md": "PRIVATE_ARTIFACT"},
            "usage": {"generation": {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
                      "judge_calls": len(attempts or []), "judge_attempts": attempts or []}}


def client_for(*, can_read=False):
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.is_super_admin = can_read
        return await call_next(request)

    app.include_router(api.router, prefix="/api")
    return TestClient(app)


class ExperimentLoadingTest(DatabaseTest):
    def test_summary_is_bounded_and_keeps_display_and_operation_fields(self):
        run = run_record()
        run["samples"] = [sample_record(attempts=[{"judge_id": 2, "dimension": "quality", "status": "failed", "usage": None}] * 56_396)]
        run["private"] = {"model": {"api_key_enc": "PRIVATE_CREDENTIAL"}}
        repo.put_run(run)
        response = client_for(can_read=True).get("/api/experiments/runs")
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.content), 100_000)
        item = response.json()["items"][0]
        for key in ("id", "name", "task_type", "status", "phase", "progress", "baseline", "report", "can_publish", "can_restore", "models"):
            self.assertIn(key, item)
        for key in ("samples", "rounds", "events", "private"):
            self.assertNotIn(key, item)
        self.assertNotIn("judge_attempts", response.text)
        self.assertNotIn("PRIVATE_CREDENTIAL", response.text)
        self.assertEqual(len(repo.get_run(run["id"])["samples"][0]["usage"]["judge_attempts"]), 56_396)

    def test_summary_does_not_copy_discarded_history(self):
        class History(list):
            def __deepcopy__(self, memo):
                raise AssertionError("discarded history was copied")

        run = run_record()
        run["events"] = History([{"message": "large history"}])
        run["samples"] = [sample_record(attempts=History([{"status": "failed", "usage": None}]))]
        self.assertEqual(api.public_run(run, can_read_content=True, summary=True)["id"], run["id"])

    def test_active_filter_and_default_history_preserve_order(self):
        statuses = ("publishing", "pending", "running", "recovering", "stopping", "stopped", "activated", "failed")
        runs = []
        for index, status in enumerate(statuses):
            run = run_record()
            run.update(id=f"experiment-{index}", status=status)
            runs.append(run)
        client = client_for()
        with patch.object(repo, "list_runs", return_value=runs):
            self.assertEqual([item["status"] for item in client.get("/api/experiments/runs").json()["items"]], list(statuses))
            self.assertEqual([item["status"] for item in client.get("/api/experiments/runs?active=true").json()["items"]], list(statuses[:5]))
            self.assertEqual(len(client.get("/api/experiments/runs?active=false").json()["items"]), len(statuses))
        with patch.object(repo, "list_runs", return_value=[]):
            self.assertEqual(client.get("/api/experiments/runs?active=true").json(), {"items": []})

    def test_detail_aggregates_partial_usage_and_keeps_source_unchanged(self):
        run = run_record()
        run["samples"] = [sample_record(attempts=[
            {"judge_id": 2, "dimension": "quality", "status": "completed", "usage": {"prompt_tokens": 4, "completion_tokens": 0}},
            {"judge_id": 2, "dimension": "quality", "status": "failed", "usage": None},
            {"judge_id": 3, "dimension": "quality", "status": "failed", "usage": {"input_tokens": 2, "output_tokens": None, "total_tokens": 3}},
        ])]
        run["rounds"] = [{"round": 1, "status": "failed", "usage": {"known": False}}]
        original = copy.deepcopy(run)
        for can_read in (True, False):
            public = api.public_run(run, can_read_content=can_read)
            evaluation = public["usage"]["evaluation"]
            self.assertEqual(evaluation, {"calls": 3, "failed_calls": 2, "input_tokens": 6,
                "output_tokens": 0, "total_tokens": 7, "known": False, "partial": True})
            self.assertEqual(public["usage"]["generation"], {"calls": 1, "failed_calls": 0,
                "input_tokens": 10, "output_tokens": 0, "total_tokens": 10, "known": True, "partial": False})
            self.assertEqual(public["usage"]["evolution"]["failed_calls"], 1)
            self.assertIsNone(public["usage"]["evolution"]["total_tokens"])
            self.assertFalse(public["usage"]["known"])
            self.assertNotIn("judge_attempts", json.dumps(public))
            self.assertEqual(public["samples"][0]["usage"]["judge_calls"], 3)
        self.assertEqual(run, original)

    def test_unknown_and_partial_flags_do_not_become_known_zero(self):
        run = run_record()
        run["samples"] = [sample_record(attempts=[{"status": "failed", "usage": None}])]
        run["samples"][0]["usage"]["generation"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "total_partial": True}
        usage = api.public_run(run, can_read_content=True)["usage"]
        self.assertEqual(usage["evaluation"]["calls"], 1)
        self.assertIsNone(usage["evaluation"]["total_tokens"])
        self.assertFalse(usage["evaluation"]["known"])
        self.assertFalse(usage["evaluation"]["partial"])
        self.assertEqual(usage["generation"]["total_tokens"], 0)
        self.assertFalse(usage["generation"]["known"])
        self.assertTrue(usage["generation"]["partial"])
        self.assertEqual(usage["evolution"]["calls"], 0)
        self.assertIsNone(usage["evolution"]["total_tokens"])

    def test_evaluation_pages_are_complete_bounded_and_metadata_only(self):
        run = run_record()
        run.update(status="stopped", private={"model": {"api_key_enc": "PRIVATE_CREDENTIAL"}})
        run["samples"] = [sample_record("sample-a", [
            {"judge_id": index, "dimension": "quality", "status": "failed", "usage": None, "output": "PRIVATE_OUTPUT"}
            for index in range(103)]), sample_record("sample-b", [
                {"judge_id": 103, "dimension": "quality", "status": "completed", "usage": {"total_tokens": 0}}])]
        repo.put_run(run)
        before = repo.get_run(run["id"])
        client = client_for(can_read=False)
        endpoint = f"/api/experiments/runs/{run['id']}/usage/evaluation"
        first = client.get(endpoint)
        self.assertEqual(first.status_code, 200)
        self.assertEqual((first.json()["total"], first.json()["offset"], first.json()["limit"]), (104, 0, 50))
        items = []
        for offset in (0, 50, 100):
            response = client.get(endpoint, params={"offset": offset, "limit": 50})
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(response.json()["items"]), 50)
            self.assertNotIn("PRIVATE_", response.text)
            items.extend(response.json()["items"])
        self.assertEqual([item["judge_id"] for item in items], list(range(104)))
        self.assertEqual(set(items[0]), {"sample_id", "judge_id", "dimension", "status", "usage"})
        self.assertEqual((items[0]["sample_id"], items[-1]["sample_id"]), ("sample-a", "sample-b"))
        self.assertIsNone(items[0]["usage"])
        self.assertEqual(items[-1]["usage"], {"total_tokens": 0})
        self.assertEqual(client.get(endpoint, params={"offset": 999}).json()["items"], [])
        self.assertEqual(repo.get_run(run["id"]), before)

    def test_evaluation_page_errors_and_empty_history(self):
        client = client_for()
        endpoint = "/api/experiments/runs/experiment-test/usage/evaluation"
        self.assertEqual(client.get(endpoint).status_code, 404)
        for params in ({"offset": -1}, {"limit": 0}, {"limit": 101}, {"limit": "bad"}):
            self.assertEqual(client.get(endpoint, params=params).status_code, 422)
        repo.put_run(run_record())
        self.assertEqual(client.get(endpoint).json(), {"items": [], "total": 0, "offset": 0, "limit": 50})

    def test_evaluation_pages_keep_sso_and_non_superadmin_metadata_access(self):
        from app.core.sso_auth import SSOAuthMiddleware

        run = run_record()
        run["samples"] = [sample_record(attempts=[{"judge_id": 2, "dimension": "quality", "status": "failed", "usage": None}])]
        repo.put_run(run)
        app = FastAPI()
        app.include_router(api.router, prefix="/api")
        app.add_middleware(SSOAuthMiddleware)
        endpoint = f"/api/experiments/runs/{run['id']}/usage/evaluation"
        with patch("app.core.sso_auth.settings.allowed_user_ids", "allowed-user"), \
             patch.object(SSOAuthMiddleware, "_verify_with_executor", new_callable=AsyncMock, return_value=("allowed-user", False)):
            client = TestClient(app)
            self.assertEqual(client.get(endpoint).status_code, 401)
            client.cookies.set("session", "unit-test-session")
            response = client.get(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertNotIn("PRIVATE_ARTIFACT", response.text)
