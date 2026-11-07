"""Stage trial defaults, round-isolated regression and durable release recovery."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_experiments as fixtures


class FlowV2Test(fixtures.DatabaseTest):
    create_experiment = fixtures.ExperimentLifecycleTest.create_experiment
    completed_task = staticmethod(fixtures.ExperimentLifecycleTest.completed_task)

    def test_optimization_failure_retries_with_fixed_baseline_until_round_limit(self):
        from app.experiments import repo, runner
        run = self.create_experiment()
        baseline = copy.deepcopy(run["baseline"])
        improvements = []

        def improve(active_run, record, _evidence):
            improvements.append((record["round"], record["base_commit"], copy.deepcopy(active_run["baseline"])))
            return {"commit": ("b" if record["round"] == 1 else "c") * 40,
                    "digest": str(record["round"] + 1) * 64, "summary": "尝试改版", "diff": "+修复"}

        def score(_run, sample):
            # Each candidate improves, but neither reaches the frozen 0.5 threshold.
            value = 3.0 if sample["arm"] == "baseline" else 3.0 + sample["round"] / 10
            return {"overall": value, "dimensions": {}, "reasons": {}}

        with patch("app.experiments.clients.start_task", side_effect=self.completed_task), \
             patch("app.experiments.evaluation.score_sample", side_effect=score), \
             patch("app.experiments.agent.improve_candidate", side_effect=improve) as improve_candidate:
            for _ in range(40):
                runner.advance_one(run["id"])

        actual = repo.get_run(run["id"])
        self.assertEqual(actual["status"], "exhausted")
        self.assertEqual(actual["current_round"], 2)
        self.assertEqual(improve_candidate.call_count, 2)
        self.assertEqual(improvements, [(1, baseline["commit"], baseline), (2, baseline["commit"], baseline)])
        self.assertEqual(actual["baseline"], baseline)
        self.assertIsNone(actual["best_candidate"])
        self.assertEqual([record["status"] for record in actual["rounds"]], ["not_improved", "not_improved"])
        self.assertTrue(all(record["report"]["baseline"]["quality"] == 3.0 for record in actual["rounds"]))
        self.assertEqual(len([sample for sample in actual["samples"] if sample["phase"] == "baseline"]), 1)
        self.assertEqual([sample["round"] for sample in actual["samples"] if sample["phase"] == "exploration"], [1, 2])
        self.assertFalse(any(sample["phase"] in {"regression", "validation"} for sample in actual["samples"]))

    def test_regression_failure_retries_before_independent_validation(self):
        from app.experiments import repo, runner
        run = self.create_experiment()
        def score(_run, sample):
            value = 3.0 if sample["arm"] == "baseline" else 4.0
            if sample["phase"] == "regression" and sample["arm"] == "candidate" and sample["round"] == 1:
                value = 2.0
            return {"overall": value, "dimensions": {}, "reasons": {}}
        candidates = [{"commit": letter * 40, "digest": str(index) * 64, "summary": "改动", "diff": "+修复"}
                      for index, letter in ((2, "b"), (3, "c"))]
        with patch("app.experiments.clients.start_task", side_effect=self.completed_task), \
             patch("app.experiments.evaluation.score_sample", side_effect=score), \
             patch("app.experiments.agent.improve_candidate", side_effect=candidates) as improve:
            for _ in range(90):
                runner.advance_one(run["id"])
        actual = repo.get_run(run["id"])
        self.assertEqual(actual["flow_version"], 2)
        self.assertEqual(actual["status"], "awaiting_review")
        self.assertEqual(improve.call_count, 2)
        self.assertFalse(any(sample["phase"] == "validation" and sample["round"] == 1 for sample in actual["samples"]))
        self.assertTrue(any(item["phase"] == "regression" for item in improve.call_args_list[1].args[2]))
        self.assertTrue(all(item["phase"] != "validation" for item in improve.call_args_list[1].args[2]))
        for report in actual["report"]["regressions"].values():
            self.assertEqual(report["baseline"]["total"], 3)
            self.assertEqual(report["candidate"]["total"], 3)

    def test_legacy_final_regression_failure_does_not_start_another_round(self):
        from app.experiments import repo, runner
        run = self.create_experiment()
        run.pop("flow_version", None)
        repo.put_run(run)
        def score(_run, sample):
            value = 3.0 if sample["arm"] == "baseline" else (2.0 if sample["phase"] == "regression" else 4.0)
            return {"overall": value, "dimensions": {}, "reasons": {}}
        with patch("app.experiments.clients.start_task", side_effect=self.completed_task), \
             patch("app.experiments.evaluation.score_sample", side_effect=score), \
             patch("app.experiments.agent.improve_candidate", return_value={"commit": "b" * 40, "digest": "2" * 64, "summary": "改动", "diff": "+修复"}) as improve:
            for _ in range(40):
                runner.advance_one(run["id"])
        self.assertEqual(repo.get_run(run["id"])["status"], "validation_failed")
        self.assertEqual(improve.call_count, 1)

    def test_public_models_keep_frozen_names_after_configuration_changes(self):
        from app.experiments.api import public_run
        run = self.create_experiment()
        self.db.execute("UPDATE llm_configs SET name='后改名称',model='changed-model' WHERE id IN (1,2,3)")
        actual = public_run(run, can_read_content=True, summary=True)
        self.assertEqual(actual["models"]["executor"], {"id": 1, "name": "gpt-test", "model": "gpt-test"})
        self.assertEqual(actual["models"]["judges"], [{"id": 2, "name": "claude-test", "model": "claude-test"}])
        self.assertEqual(actual["models"]["evolver"]["model"], "gpt-test")
        self.assertNotIn("base_url", str(actual["models"]))
        self.assertNotIn("opaque-encrypted-secret", str(actual))
        legacy = copy.deepcopy(run)
        legacy.pop("models", None)
        for snapshot in [legacy["private"]["model"], legacy["private"]["evolver"], *legacy["private"]["judges"]]:
            snapshot.pop("name", None)
        self.assertEqual(public_run(legacy, can_read_content=True)["models"]["executor"]["model"], "gpt-test")

    def test_phase_progress_excludes_previous_rounds(self):
        from app.experiments.api import public_run
        run = fixtures.run_record()
        run.update(flow_version=2, status="running", phase="regression", current_round=2)
        run["samples"] = [
            {"phase": "regression", "round": 1, "status": "completed", "completed": True},
            {"phase": "regression", "round": 2, "status": "completed", "completed": True},
            {"phase": "regression", "round": 2, "status": "running", "completed": False},
        ]
        progress = public_run(run, can_read_content=True)["progress"]["phase"]
        self.assertEqual((progress["total"], progress["done"], progress["active"]), (2, 1, 1))

    def test_auto_names_are_readable_and_do_not_replace_custom_names(self):
        from app.experiments import runner
        from app.experiments.schemas import RunCreate
        original = self.create_experiment()
        self.assertEqual(original["name"], "完整实验")
        request = RunCreate.model_validate({**original["settings"], "name": None})
        with patch("app.experiments.clients.versions", return_value={"items": [{"version": 7, "commit": "a" * 40}]}), \
             patch("app.experiments.clients.production", return_value={"version": 7, "commit": "a" * 40}), \
             patch("app.experiments.clients.prepare_artifact", return_value={"digest": "1" * 64}):
            first = runner.create_run(request, "developer")
            second = runner.create_run(request, "developer")
        self.assertRegex(first["name"], r"整本卷纲.*\d{4}-\d{2}-\d{2}.*001$")
        self.assertRegex(second["name"], r"002$")

    def test_no_change_is_not_a_new_quality_candidate(self):
        from app.core.settings import settings
        from app.experiments import agent
        run = fixtures.run_record()
        run["flow_version"] = 2
        record = {"round": 1, "base_commit": "a" * 40}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(settings, "evolution_db", str(Path(directory) / "evolution.db")), \
             patch("app.experiments.agent._improve", new_callable=AsyncMock, return_value={"validated": True, "summary": "没有改动"}), \
             patch("app.experiments.agent._git", side_effect=lambda _root, *args: "a" * 40 if args == ("rev-parse", "HEAD") else ""), \
             patch("app.experiments.repo.put_run"), patch("app.experiments.clients.prepare_artifact") as prepare:
            root = Path(directory) / "experiments" / run["id"] / "round-1"
            (root / ".git").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "未产生.*改动"):
                agent.improve_candidate(run, record, [])
        prepare.assert_not_called()


class ReleaseRecoveryV2Test(fixtures.DatabaseTest):
    def test_startup_resumes_observation_of_interrupted_release_operations(self):
        from app.experiments import repo, runner
        publication = fixtures.run_record()
        publication.update(id="publish-interrupted", status="publishing", publish_status="publishing",
                           approved_commit="b" * 40, approved_by="developer")
        restoration = fixtures.run_record()
        restoration.update(id="restore-interrupted", status="publish_failed", rollback_status="restoring",
                           published={"version": 8, "commit": "b" * 40}, restore_requested_by="developer")
        repo.put_run(publication)
        repo.put_run(restoration)
        with patch("app.experiments.runner.start_worker") as start:
            runner.recover_runs()
        self.assertEqual({call.args[0] for call in start.call_args_list}, {publication["id"], restoration["id"]})
        self.assertEqual(repo.get_run(publication["id"])["status"], "publishing")
        self.assertEqual(repo.get_run(restoration["id"])["rollback_status"], "restoring")

    def test_recover_promotion_that_happened_before_response_was_saved(self):
        from app.experiments import repo, release
        run = fixtures.run_record()
        run.update(status="publishing", phase="publish", publish_status="publishing", approved_commit="b" * 40, approved_by="developer")
        repo.put_run(run)
        promoted = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64}
        with patch("app.experiments.clients.production", return_value=promoted), \
             patch("app.experiments.clients.runtime", return_value=promoted), \
             patch("app.experiments.clients.promote") as promote:
            actual = release.recover_run(run["id"])
        promote.assert_not_called()
        self.assertEqual(actual["status"], "activated")
        self.assertEqual(actual["published"]["version"], 8)

    def test_late_promotion_after_failed_recovery_is_confirmed_without_republishing(self):
        from app.experiments import repo, release
        run = fixtures.run_record()
        run.update(status="publishing", phase="publish", publish_status="publishing",
                   approved_commit="b" * 40, approved_by="developer")
        repo.put_run(run)
        with patch("app.experiments.clients.production", return_value=run["baseline"]):
            interrupted = release.recover_run(run["id"])
        self.assertEqual(interrupted["status"], "publish_failed")
        self.assertNotIn("published", interrupted)
        promoted = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64}
        with patch("app.experiments.clients.production", return_value=promoted), \
             patch("app.experiments.clients.runtime", return_value=promoted), \
             patch("app.experiments.clients.promote") as promote:
            actual = release.publish_run(run["id"], "developer")
        promote.assert_not_called()
        self.assertEqual(actual["status"], "activated")
        self.assertEqual(actual["published"]["version"], 8)
        self.assertFalse(actual.get("stale_reason"))

    def test_failed_recovery_still_rejects_a_later_external_release(self):
        from app.experiments import repo, release
        run = fixtures.run_record()
        run.update(status="publishing", phase="publish", publish_status="publishing",
                   approved_commit="b" * 40, approved_by="developer")
        repo.put_run(run)
        with patch("app.experiments.clients.production", return_value=run["baseline"]):
            release.recover_run(run["id"])
        external = {"version": 8, "commit": "c" * 40, "artifact_digest": "3" * 64}
        with patch("app.experiments.clients.production", return_value=external), \
             patch("app.experiments.clients.runtime") as runtime, \
             patch("app.experiments.clients.promote") as promote:
            actual = release.publish_run(run["id"], "developer")
        promote.assert_not_called()
        runtime.assert_not_called()
        self.assertEqual(actual["status"], "stale")

    def test_recovery_does_not_authorize_unstarted_promotion(self):
        from app.experiments import repo, release
        run = fixtures.run_record()
        run.update(status="publishing", publish_status="publishing")
        repo.put_run(run)
        with patch("app.experiments.clients.production", return_value=run["baseline"]), \
             patch("app.experiments.clients.promote") as promote:
            actual = release.recover_run(run["id"])
        promote.assert_not_called()
        self.assertNotEqual(actual["status"], "activated")

    def test_recover_completed_restore_without_a_second_promotion(self):
        from app.experiments import repo, release
        run = fixtures.run_record()
        run.update(status="publish_failed", publish_status="unconfirmed", rollback_status="restoring",
                   published={"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64}, restore_requested_by="developer")
        repo.put_run(run)
        restored = {"version": 9, "commit": "a" * 40, "artifact_digest": "1" * 64}
        with patch("app.experiments.clients.production", return_value=restored), \
             patch("app.experiments.clients.runtime", return_value=restored), \
             patch("app.experiments.clients.restore") as restore:
            actual = release.recover_run(run["id"])
        restore.assert_not_called()
        self.assertEqual(actual["rollback_status"], "restored")

    def test_late_restore_after_failed_recovery_is_confirmed_without_restoring_again(self):
        from app.experiments import repo, release
        published = {"version": 8, "commit": "b" * 40, "artifact_digest": "2" * 64}
        run = fixtures.run_record()
        run.update(status="publish_failed", publish_status="unconfirmed", rollback_status="restoring",
                   published=published, restore_requested_by="developer")
        repo.put_run(run)
        with patch("app.experiments.clients.production", return_value=published):
            interrupted = release.recover_run(run["id"])
        self.assertEqual(interrupted["rollback_status"], "failed")
        restored = {"version": 9, "commit": "a" * 40, "artifact_digest": "1" * 64}
        with patch("app.experiments.clients.production", return_value=restored), \
             patch("app.experiments.clients.runtime", return_value=restored), \
             patch("app.experiments.clients.restore") as restore:
            actual = release.restore_run(run["id"], "developer")
        restore.assert_not_called()
        self.assertEqual(actual["status"], "restored")
        self.assertEqual(actual["rollback_status"], "restored")


class DefaultsAndRetryTest(fixtures.DatabaseTest):
    def test_options_only_default_to_current_rules_and_safe_models(self):
        from app.experiments import api, repo
        with patch("app.experiments.clients.versions", return_value={"items": [], "production_version": None}), \
             patch("app.benchmark.api.list_judge_groups", return_value={"groups": []}), \
             patch("app.core.db.LlmConfigsRepository.list_all", return_value=[
                 {"id": 1, "name": "写作", "model": "gpt-test", "is_active": True, "has_key": True,
                  "base_url": "https://private.invalid", "key_hint": "secret"}]):
            options = api.experiment_options()
        self.assertEqual(options["builtin_suite"], {"id": "stage-smoke-v1", "total": 12})
        self.assertEqual(options["defaults"]["thresholds"]["quality_gain"], .1)
        self.assertEqual(options["defaults"]["max_rounds"], 3)
        for identifier in options["defaults"]["rule_versions"].values():
            self.assertEqual(repo.get_rule(identifier)["profile"], "stage-eval-v2")
        self.assertNotIn("base_url", str(options["models"]))
        self.assertNotIn("secret", str(options["models"]))

    def test_schema_supplies_trial_thresholds_when_omitted(self):
        from app.experiments.schemas import RunCreate
        request = RunCreate.model_validate({
            "task_type": "chapter_text", "baseline_version": 7, "model_config_id": 1, "judge_group_id": 1,
            "rule_versions": {kind: kind for kind in ("volume_outlines", "chapter_outlines", "chapter_text")},
            "optimization_ids": ["opt"], "validation_ids": ["hidden"],
            "regression_ids": {"volume_outlines": ["reg-vol"], "chapter_outlines": ["reg-outline"]},
        })
        self.assertIsNone(request.name)
        self.assertEqual(request.max_rounds, 3)
        self.assertEqual(request.thresholds.completion_rate, 1)
        self.assertEqual(request.thresholds.quality_gain, .1)
        self.assertEqual(request.thresholds.regression_tolerance, {"volume_outlines": 0, "chapter_outlines": 0})

    def test_judge_outage_waits_before_reusing_generated_sample(self):
        from app.experiments import repo, runner
        run = fixtures.run_record()
        run.update(status="running", phase="baseline")
        sample = {"id": "scoring-retry", "package_id": "opt", "phase": "baseline", "round": 0,
                  "status": "scoring", "completed": True, "generation_status": "completed", "scores": None,
                  "artifacts": {"正文/第001章.md": "已生成"}}
        run["samples"] = [sample]
        repo.put_run(run)
        with patch("app.experiments.evaluation.score_sample", side_effect=RuntimeError("HTTP 429 评委限流")) as score, \
             patch("app.experiments.clients.start_task") as generate:
            runner.advance_one(run["id"])
            runner.advance_one(run["id"])
        self.assertEqual(score.call_count, 1)
        generate.assert_not_called()
        actual = repo.get_run(run["id"])
        self.assertIn("429", actual["samples"][0]["error"])
        self.assertEqual(actual["phase"], "baseline")


if __name__ == "__main__":
    unittest.main()
