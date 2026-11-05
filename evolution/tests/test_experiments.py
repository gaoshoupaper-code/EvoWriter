"""Independent experiments: immutable inputs, comparisons and restart evidence."""
from __future__ import annotations

import copy
import asyncio
import json
import sqlite3
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def package_payload(task_type="volume_outlines", purpose="optimization"):
    files = {}
    target = {"task_type": task_type, "volume_ids": [1, 2]}
    if task_type == "chapter_outlines":
        target["volume_ids"] = [1, 2, 3, 4]
        files = {"主题.md": "主题", "总纲.md": "总纲", "文风.md": "简洁具体"}
        files.update({f"卷纲/第{i:02d}卷.md": f"本卷章号范围：第{(i - 1) * 5 + 1}-{i * 5}章" for i in target["volume_ids"]})
    elif task_type == "chapter_text":
        target = {"task_type": task_type, "volume_ids": [1], "chapter": 1}
        files = {"主题.md": "主题", "总纲.md": "总纲", "文风.md": "简洁具体", "卷纲/第01卷.md": "本卷章号范围：第1-5章", "章纲/第001章.md": "章纲"}
    return {"title": "固定输入", "purpose": purpose, "target": target,
            "input": {"demand_md": f"完整任务：两卷小说，独立{purpose}题目", "files": files,
                      "skills": [], "confirmation_rules": "普通取舍按既定规则自行决定"}}


def run_record():
    return {
        "id": "experiment-test", "name": "测试", "task_type": "volume_outlines",
        "status": "awaiting_review", "phase": "review", "current_round": 1, "max_rounds": 2,
        "settings": {"optimization_ids": ["opt"], "validation_ids": ["hidden"],
                     "max_rounds": 2, "thresholds": {"completion_rate": 1.0, "quality_gain": .1, "regression_tolerance": {}}},
        "baseline": {"version": 7, "commit": "a" * 40, "artifact_digest": "1" * 64},
        "best_candidate": {"commit": "b" * 40, "digest": "2" * 64, "round": 1, "quality": 4.0, "completion_rate": 1.0},
        "rounds": [], "samples": [], "report": {"passed": True}, "events": [],
        "publish_status": "not_started", "loaded_revision": None, "load_error": None,
        "rollback_status": None, "end_reason": None,
        "created_at": "2026-11-06T00:00:00Z", "updated_at": "2026-11-06T00:00:00Z",
    }


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        import app.core.db as db
        from app.experiments import repo
        self.db = db
        self.original_connection = db._conn
        db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        db._conn.row_factory = sqlite3.Row
        db.init_db()
        repo.init_schema()

    def tearDown(self):
        self.db._conn.close()
        self.db._conn = self.original_connection


class PackageAndRuleTest(DatabaseTest):
    def test_three_types_have_fixed_content_revisions(self):
        from app.experiments import packages, repo
        from app.experiments.schemas import PackageCreate
        for kind in ("volume_outlines", "chapter_outlines", "chapter_text"):
            imported = packages.import_package(PackageCreate.model_validate(package_payload(kind)))
            before = repo.get_package(imported["id"])
            self.assertEqual(imported["task_type"], kind)
            self.assertEqual(len(before["input_revision"]), 64)
            before["input"]["files"]["主题.md"] = "运行中修改"
            self.assertNotEqual(repo.get_package(imported["id"])["input"]["files"].get("主题.md"), "运行中修改")

    def test_missing_upstream_and_nonfirst_chapter_context_are_rejected(self):
        from app.experiments import packages
        from app.experiments.schemas import PackageCreate
        payload = package_payload("chapter_outlines")
        del payload["input"]["files"]["卷纲/第04卷.md"]
        with self.assertRaisesRegex(ValueError, "第04卷"):
            packages.import_package(PackageCreate.model_validate(payload))
        payload = package_payload("chapter_text")
        payload["target"]["chapter"] = 2
        payload["input"]["files"]["章纲/第002章.md"] = "第二章"
        with self.assertRaisesRegex(ValueError, "正文/第001章"):
            packages.import_package(PackageCreate.model_validate(payload))

    def test_public_templates_pass_the_real_executor_input_validator(self):
        from app.experiments import packages
        from app.experiments.schemas import PackageCreate
        examples = packages.templates()
        for payload in examples:
            packages.validate_package(PackageCreate.model_validate(payload))
        repository = Path(__file__).resolve().parents[2]
        # The services deliberately share the package name app. A fresh interpreter
        # exercises executor's actual validator without importing the evolution app.
        script = """
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path[:0] = [str(root / 'executor'), str(root)]
from contracts.target_tasks import TargetSpec, TaskInput
from app.domains.writing.target_tasks import validate_input
for package in json.load(sys.stdin):
    validate_input(TargetSpec.model_validate(package['target']), TaskInput.model_validate(package['input']))
print('three real executor validations passed')
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, str(repository)], input=json.dumps(examples),
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("three real executor validations passed", result.stdout)

    def test_rule_versions_do_not_mutate_old_rules_or_legacy_tables(self):
        from app.experiments import rubrics
        from app.experiments.schemas import RuleCreate
        before = self.db.query_one("SELECT COUNT(*) n FROM benchmark_runs")["n"]
        initial = rubrics.list_rules("volume_outlines")[0]
        replacement = copy.deepcopy(initial)
        replacement["name"] = "更严格"
        replacement["dimensions"][0]["question"] = "逐条给原文证据"
        saved = rubrics.save_rule(RuleCreate.model_validate({key: replacement[key] for key in
            ("task_type", "name", "dimensions", "discipline_rules")}))
        self.assertGreater(saved["version"], initial["version"])
        self.assertEqual(rubrics.get_rule(initial["id"])["dimensions"], initial["dimensions"])
        self.assertEqual(self.db.query_one("SELECT COUNT(*) n FROM benchmark_runs")["n"], before)
        self.db.init_db()
        self.assertIsNotNone(rubrics.get_rule(saved["id"]))

    def test_same_family_is_rejected_against_frozen_writer(self):
        from app.experiments.evaluation import require_heterogeneous_judges
        with self.assertRaisesRegex(ValueError, "家族|异源"):
            require_heterogeneous_judges("openai:gpt-4o", [{"model": "gpt-4.1"}])
        require_heterogeneous_judges("gpt-4o", [{"model": "claude-sonnet"}])


class ComparisonTest(unittest.TestCase):
    def test_new_failure_cannot_disappear_behind_high_quality(self):
        from app.experiments.evaluation import compare_results
        baseline = [{"package_id": key, "repeat": 1, "completed": True, "scores": {"overall": 3.0}} for key in ("one", "two")]
        candidate = [{"package_id": "one", "repeat": 1, "completed": True, "scores": {"overall": 5.0}},
                     {"package_id": "two", "repeat": 1, "completed": False, "scores": None, "status": "failed"}]
        report = compare_results(baseline, candidate, completion_rate=1.0, quality_gain=.1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["candidate"]["completion_rate"], .5)
        self.assertEqual(report["paired_count"], 1)
        self.assertIn("two", report["new_failures"])

    def test_judge_error_is_neither_zero_nor_valid_acceptance(self):
        from app.experiments.evaluation import compare_results
        baseline = [{"package_id": "one", "repeat": 1, "completed": True, "scores": {"overall": 3.0}}]
        candidate = [{"package_id": "one", "repeat": 1, "completed": True, "scores": None, "status": "evaluation_error"}]
        report = compare_results(baseline, candidate, completion_rate=1.0, quality_gain=.1)
        self.assertEqual(report["candidate"]["completion_rate"], 1.0)
        self.assertIsNone(report["candidate"]["quality"])
        self.assertFalse(report["passed"])


class RecoveryAndIsolationTest(DatabaseTest):
    def test_stop_missing_task_finishes_but_service_outage_keeps_waiting(self):
        from app.experiments import clients, repo, runner
        run = run_record()
        run.update(status="stopping", phase="baseline")
        run["samples"] = [{"id": "not-dispatched", "task_id": "missing-task", "status": "interrupted",
                           "request_saved": True, "artifacts": {"主题.md": "保留现场"}}]
        repo.put_run(run)
        with patch("app.experiments.clients.stop_task", side_effect=clients.ServiceUnavailable("暂时不可用")):
            runner.advance_one(run["id"])
        self.assertEqual(repo.get_run(run["id"])["status"], "stopping")
        with patch("app.experiments.clients.stop_task", side_effect=clients.TaskNotFound("missing-task")):
            runner.advance_one(run["id"])
        stopped = repo.get_run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["samples"][0]["status"], "stopped")
        self.assertIn("未找到", stopped["samples"][0]["error"])
        self.assertEqual(stopped["samples"][0]["artifacts"], {"主题.md": "保留现场"})

    def test_stop_waits_for_executor_stopping_to_reach_stopped(self):
        from app.experiments import repo, runner
        run = run_record()
        run.update(status="stopping", phase="baseline")
        run["samples"] = [{"id": "active-sample", "task_id": "active-task", "status": "running", "request_saved": True,
                           "usage": {"judge_calls": 1}}]
        repo.put_run(run)
        with patch("app.experiments.clients.stop_task", return_value={"status": "stopping", "usage": {"total_tokens": 9}}):
            runner.advance_one(run["id"])
        self.assertEqual(repo.get_run(run["id"])["status"], "stopping")
        self.assertEqual(repo.get_run(run["id"])["samples"][0]["usage"], {"judge_calls": 1, "generation": {"total_tokens": 9}})
        with patch("app.experiments.clients.stop_task", return_value={"status": "stopped", "usage": {"total_tokens": 12}}):
            runner.advance_one(run["id"])
        self.assertEqual(repo.get_run(run["id"])["status"], "stopped")
        self.assertEqual(repo.get_run(run["id"])["samples"][0]["usage"], {"judge_calls": 1, "generation": {"total_tokens": 12}})

    def test_agent_input_excludes_hidden_inputs_and_derived_evidence(self):
        from app.experiments.runner import optimization_evidence
        run = run_record()
        run["samples"] = [
            {"package_id": "opt", "phase": "exploration", "artifacts": {"卷纲/第1卷.md": "优化证据"}, "scores": {"overall": 2}},
            {"package_id": "hidden", "phase": "validation", "artifacts": {"正文/第1章.md": "HIDDEN_SECRET"}, "scores": {"reason": "HIDDEN_SECRET"}, "trace_ids": ["hidden-trace"]},
        ]
        run["report"] = {"hidden": "HIDDEN_SECRET"}
        evidence = str(optimization_evidence(run))
        self.assertIn("优化证据", evidence)
        self.assertNotIn("HIDDEN_SECRET", evidence)
        self.assertNotIn("hidden-trace", evidence)

    def test_scoring_recovery_reuses_saved_generation(self):
        from app.experiments import repo, runner
        run = run_record()
        sample = {"id": "sample-1", "status": "evaluation_error", "package_id": "opt", "phase": "exploration",
                  "completed": True, "trace_ids": ["existing-trace"], "artifacts": {"卷纲/第1卷.md": "已生成"},
                  "generation_status": "completed", "scores": None}
        run["samples"] = [sample]
        repo.put_run(run)
        with patch("app.experiments.clients.start_task") as start, patch("app.experiments.evaluation.score_sample", return_value={"overall": 4.0, "dimensions": {}, "reasons": {}}):
            runner.process_sample(run, sample)
        start.assert_not_called()
        self.assertEqual(sample["status"], "completed")
        self.assertEqual(sample["trace_ids"], ["existing-trace"])

    def test_stopped_and_finished_runs_are_not_recovered(self):
        from app.experiments import repo, runner
        for state in ("stopped", "exhausted", "validation_failed", "activated"):
            run = run_record()
            run["id"] = state
            run["status"] = state
            repo.put_run(run)
        with patch("app.experiments.runner.start_worker") as start:
            runner.recover_runs()
        start.assert_not_called()

    def test_iteration_exhaustion_cannot_start_an_extra_round(self):
        from app.experiments import repo, runner
        run = run_record()
        run.update(status="running", phase="evolving", current_round=2)
        run["report"] = {}
        repo.put_run(run)
        with patch("app.experiments.agent.improve_candidate") as improve:
            runner.advance_one(run["id"])
        improve.assert_not_called()
        self.assertEqual(repo.get_run(run["id"])["status"], "exhausted")

    def test_late_worker_save_does_not_undo_stop(self):
        from app.experiments import repo
        old = run_record()
        old["status"] = "running"
        repo.put_run(old)
        stopped = copy.deepcopy(old)
        stopped["status"] = "stopping"
        repo.event(stopped, "stop_requested", "停止")
        repo.put_run(stopped)
        old["phase"] = "exploration"
        repo.put_run(old)
        saved = repo.get_run(old["id"])
        self.assertEqual(saved["status"], "stopping")
        self.assertTrue(any(event["kind"] == "stop_requested" for event in saved["events"]))


class ExperimentLifecycleTest(DatabaseTest):
    def create_experiment(self, second_optimization=False):
        from app.experiments import packages, rubrics, runner
        from app.experiments.schemas import PackageCreate, RunCreate
        for config_id, scope, model in ((1, "executor", "gpt-test"), (2, "eval", "claude-test"), (3, "evolution", "gpt-test")):
            self.db.execute("INSERT INTO llm_configs(id,name,api_key_enc,base_url,model,is_active,scope,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (config_id, model, "opaque-encrypted-secret", "https://model.invalid/v1", model, 1, scope, "now", "now"))
        self.db.execute("INSERT INTO judge_groups(id,name,created_at,updated_at) VALUES (1,'异源组','now','now')")
        self.db.execute("INSERT INTO judge_group_members(group_id,config_id,position) VALUES (1,2,0)")
        imported = {}
        for kind, purpose in (("volume_outlines", "optimization"), ("volume_outlines", "validation"),
                              ("chapter_outlines", "regression"), ("chapter_text", "regression")):
            imported[(kind, purpose)] = packages.import_package(PackageCreate.model_validate(package_payload(kind, purpose)))["id"]
        optimization_ids = [imported[("volume_outlines", "optimization")]]
        if second_optimization:
            second = package_payload()
            second["title"] = "另一条优化样本"
            second["input"]["demand_md"] += "；另一部独立小说"
            optimization_ids.append(packages.import_package(PackageCreate.model_validate(second))["id"])
        request = RunCreate.model_validate({"name": "完整实验", "task_type": "volume_outlines", "baseline_version": 7,
            "model_config_id": 1, "judge_group_id": 1,
            "rule_versions": {kind: rubrics.list_rules(kind)[0]["id"] for kind in ("volume_outlines", "chapter_outlines", "chapter_text")},
            "optimization_ids": optimization_ids,
            "validation_ids": [imported[("volume_outlines", "validation")]],
            "regression_ids": {kind: [imported[(kind, "regression")]] for kind in ("chapter_outlines", "chapter_text")},
            "thresholds": {"completion_rate": 1.0, "quality_gain": .5,
                           "regression_tolerance": {"chapter_outlines": .1, "chapter_text": .1}}, "max_rounds": 2})
        with patch("app.experiments.clients.versions", return_value={"items": [{"version": 7, "commit": "a" * 40}]}), \
             patch("app.experiments.clients.production", return_value={"version": 7, "commit": "a" * 40}), \
             patch("app.experiments.clients.prepare_artifact", return_value={"digest": "1" * 64}):
            return runner.create_run(request, "developer")

    @staticmethod
    def completed_task(payload):
        return {"task_id": payload["task_id"], "status": "completed", "completed": True,
            "trace_ids": ["trace-" + payload["task_id"]], "artifacts": {"卷纲/第1卷.md": "真实落盘产物"},
            "checks": {"passed": True}, "usage": {"known": True}, "input_revision": payload["input"]["input_revision"],
            "source_commit": payload["source_commit"], "artifact_digest": payload["artifact_digest"]}

    def test_real_state_machine_runs_independent_three_repeat_final_checks(self):
        from app.experiments import repo, runner
        run = self.create_experiment()
        def score(_run, sample):
            return {"overall": 4.0 if sample["arm"] == "candidate" else 3.0, "dimensions": {}, "reasons": {}}
        with patch("app.experiments.clients.start_task", side_effect=self.completed_task), \
             patch("app.experiments.evaluation.score_sample", side_effect=score), \
             patch("app.experiments.agent.improve_candidate", return_value={"commit": "b" * 40, "digest": "2" * 64, "summary": "改动", "diff": "+修复"}) as improve:
            for _ in range(40):
                runner.advance_one(run["id"])
        actual = repo.get_run(run["id"])
        self.assertEqual(actual["status"], "awaiting_review")
        self.assertEqual(actual["current_round"], 1)
        self.assertEqual(len(actual["samples"]), 20)
        self.assertTrue(actual["report"]["passed"])
        for kind in ("volume_outlines", "chapter_outlines", "chapter_text"):
            for arm in ("baseline", "candidate"):
                final = [sample for sample in actual["samples"] if sample["phase"] in ("validation", "regression") and sample["task_type"] == kind and sample["arm"] == arm]
                self.assertEqual({sample["repeat"] for sample in final}, {1, 2, 3})
        self.assertEqual(improve.call_count, 1)
        self.assertTrue(all(item["phase"] in ("baseline", "exploration") for item in improve.call_args.args[2]))

    def test_final_failure_ends_experiment_without_another_evolution_round(self):
        from app.experiments import repo, runner
        run = self.create_experiment()
        def score(_run, sample):
            value = 3.0 if sample["arm"] == "baseline" else (4.0 if sample["phase"] == "exploration" else 2.0)
            return {"overall": value, "dimensions": {}, "reasons": {}}
        with patch("app.experiments.clients.start_task", side_effect=self.completed_task), \
             patch("app.experiments.evaluation.score_sample", side_effect=score), \
             patch("app.experiments.agent.improve_candidate", return_value={"commit": "b" * 40, "digest": "2" * 64, "summary": "改动", "diff": "+修复"}) as improve:
            for _ in range(40):
                runner.advance_one(run["id"])
        self.assertEqual(repo.get_run(run["id"])["status"], "validation_failed")
        self.assertEqual(improve.call_count, 1)

    def test_real_runner_retains_failed_sample_then_runs_the_next_one(self):
        from app.experiments import repo, runner
        run = self.create_experiment(second_optimization=True)
        first_id = run["settings"]["optimization_ids"][0]
        first_demand = run["private"]["packages"][first_id]["input"]["demand_md"]
        def task(payload):
            result = self.completed_task(payload)
            if payload["source_commit"] == "b" * 40 and payload["input"]["demand_md"] == first_demand:
                result.update(status="failed", completed=False, error="样本业务失败",
                              artifacts={"主题.md": "失败现场仍在"})
            return result
        def score(_run, sample):
            return {"overall": 4.0 if sample["arm"] == "candidate" else 3.0, "dimensions": {}, "reasons": {}}
        with patch("app.experiments.clients.start_task", side_effect=task), \
             patch("app.experiments.evaluation.score_sample", side_effect=score), \
             patch("app.experiments.agent.improve_candidate", return_value={"commit": "b" * 40, "digest": "2" * 64, "summary": "改动", "diff": "+修复"}):
            for _ in range(15):
                runner.advance_one(run["id"])
                actual = repo.get_run(run["id"])
                if actual["report"].get("exploration"):
                    break
        candidates = [sample for sample in actual["samples"] if sample["phase"] == "exploration"]
        self.assertEqual([sample["status"] for sample in candidates], ["failed", "completed"])
        self.assertEqual(candidates[0]["artifacts"], {"主题.md": "失败现场仍在"})
        self.assertTrue(candidates[0]["trace_ids"])
        report = actual["report"]["exploration"]
        self.assertEqual(report["candidate"]["total"], 2)
        self.assertEqual(report["candidate"]["completion_rate"], .5)
        self.assertEqual(report["new_failures"], [first_id])
        self.assertFalse(report["passed"])

    def test_ambiguous_dispatch_reconciles_task_before_retrying(self):
        from app.experiments import clients, repo, runner
        run = self.create_experiment()
        sample = run["samples"][0]
        payload = {"task_id": sample["task_id"], "input": run["private"]["packages"][sample["package_id"]]["input"],
                   "source_commit": sample["source_commit"], "artifact_digest": sample["artifact_digest"]}
        with patch("app.experiments.clients.start_task", side_effect=clients.ServiceUnavailable("响应丢失")):
            runner.advance_one(run["id"])
        persisted = repo.get_run(run["id"])
        self.assertTrue(persisted["samples"][0]["request_saved"])
        with patch("app.experiments.clients.get_task", return_value=self.completed_task(payload)) as lookup, \
             patch("app.experiments.clients.start_task") as duplicate, \
             patch("app.experiments.evaluation.score_sample", return_value={"overall": 3.0, "dimensions": {}, "reasons": {}}):
            runner.advance_one(run["id"])
        lookup.assert_called_once_with(sample["task_id"])
        duplicate.assert_not_called()
        self.assertEqual(repo.get_run(run["id"])["samples"][0]["status"], "completed")

    def test_live_config_edits_do_not_change_frozen_model_or_judges(self):
        from app.experiments import repo
        run = self.create_experiment()
        self.db.execute("UPDATE llm_configs SET model='changed-model',base_url='https://changed.invalid' WHERE id IN (1,2)")
        saved = repo.get_run(run["id"])
        self.assertEqual(saved["private"]["model"]["model"], "gpt-test")
        self.assertEqual(saved["private"]["judges"][0]["model"], "claude-test")

    def test_real_judging_emits_valid_shared_trace_events_and_known_usage(self):
        from contracts.trace import TraceLogEvent
        from app.experiments import evaluation
        run = self.create_experiment()
        sample = run["samples"][0]
        sample.update(completed=True, artifacts={"卷纲/第1卷.md": "事件因果清楚"})
        events = []
        class StrictRecorder:
            def create_run(self, *args, **kwargs):
                return SimpleNamespace(trace_id="trace-evaluation")
            def append_event(self, trace_id, payload):
                events.append(TraceLogEvent.model_validate({"trace_id": trace_id, "event_id": f"event-{len(events)}",
                    "sequence": len(events) + 1, "timestamp": "2026-11-06T00:00:00Z", **payload}))
            def complete_run(self, trace_id):
                pass
            def fail_run(self, *args):
                pass
        response = SimpleNamespace(is_success=True, json=lambda: {"choices": [{"message": {"content": json.dumps(
            {"score": 4, "达标": ["卷纲事件因果清楚"], "不足": ["局部节奏略快"]}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})
        with patch("app.experiments.runner._recorder", StrictRecorder()), \
             patch("app.experiments.repo.decrypt_credentials", return_value={"api_key": "test", "base_url": "https://model.invalid/v1", "model": "claude-test"}), \
             patch("app.experiments.evaluation.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            score = evaluation.score_sample(run, sample)
        self.assertEqual(score["overall"], 4.0)
        self.assertEqual(len(events), 8)
        self.assertEqual(events[-1].usage.input_tokens, 100)
        self.assertEqual(len(sample["usage"]["judge_attempts"]), 4)
        self.assertEqual(sample["usage"]["judge_calls"], 4)

    def test_judging_stop_preserves_first_result_and_prevents_next_call(self):
        from app.experiments import evaluation
        run = self.create_experiment()
        sample = run["samples"][0]
        sample.update(completed=True, artifacts={"卷纲/第01卷.md": "已生成内容"})
        stopped = [False]
        def first_response(*args, **kwargs):
            stopped[0] = True
            return SimpleNamespace(is_success=True, json=lambda: {"choices": [{"message": {"content": json.dumps(
                {"score": 4, "达标": ["存在原文证据"], "不足": ["局部不足"]}, ensure_ascii=False)}}]})
        with patch("app.experiments.runner._recorder", None), \
             patch("app.experiments.runner.is_stopped", side_effect=lambda run_id: stopped[0]), \
             patch("app.experiments.repo.decrypt_credentials", return_value={"api_key": "test", "base_url": "https://model.invalid/v1", "model": "claude-test"}), \
             patch("app.experiments.evaluation.httpx.Client") as client:
            post = client.return_value.__enter__.return_value.post
            post.side_effect = first_response
            with self.assertRaises(asyncio.CancelledError):
                evaluation.score_sample(run, sample)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(sample["judge_results"]), 1)


class HarnessToolBoundaryTest(unittest.TestCase):
    def test_actual_controlled_tools_edit_full_package_but_cannot_access_holdout(self):
        from deepagents.backends.filesystem import FilesystemBackend
        from app.evolve import ctx as context
        from app.evolve.agent.tools.inspect import make_inspect_tools
        from app.evolve.agent.tools.writers import make_writer_tools
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "harness"
            root.mkdir()
            (root / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
            holdout = base / "holdout.md"
            holdout.write_text("HIDDEN_QUESTION", encoding="utf-8")
            current = context.EvolveContext("controlled-harness")
            current.harness_root = root
            token = context._current_ctx.set(current)
            try:
                tools = {tool.name: tool for tool in make_writer_tools(FilesystemBackend(root_dir=str(root), virtual_mode=True))}
                tools["edit_source"].invoke({"file_path": "__init__.py", "old_string": "VALUE = 1", "new_string": "VALUE = 2"})
                tools["write_prompt"].invoke({"name": "example", "content": "固定提示词"})
                tools["write_middleware"].invoke({"name": "example", "code": "def build(ctx):\n    return None\n"})
                tools["write_tool"].invoke({"name": "example", "code": "def build(ctx):\n    return []\n"})
                tools["write_skill"].invoke({"path": "example/SKILL.md", "content": "# 固定技能\n"})
                self.assertEqual((root / "__init__.py").read_text(encoding="utf-8"), "VALUE = 2\n")
                for name in ("prompts/example.md", "middleware/example.py", "tools/example.py", "skills/example/SKILL.md"):
                    self.assertTrue((root / name).is_file(), name)
                denied = tools["edit_source"].invoke({"file_path": "../holdout.md", "old_string": "HIDDEN_QUESTION", "new_string": "tampered"})
                self.assertIn("错误", denied)
                read = next(tool for tool in make_inspect_tools() if tool.name == "read_source")
                result = read.invoke({"file_path": "../holdout.md"})
                self.assertIn("越界", result)
                self.assertNotIn("HIDDEN_QUESTION", result)
                self.assertEqual(holdout.read_text(encoding="utf-8"), "HIDDEN_QUESTION")
            finally:
                context._current_ctx.reset(token)


if __name__ == "__main__":
    unittest.main()
