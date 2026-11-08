"""A failed scoring round has a finite budget without discarding generated work."""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_experiments import DatabaseTest, run_record
from app.experiments import evaluation, repo, runner


def generated_sample(identifier="sample-one", *, failures=0):
    return {"id": identifier, "package_id": identifier, "package_title": "已生成样本", "phase": "baseline",
            "round": 0, "repeat": 1, "status": "evaluation_error", "completed": True,
            "generation_status": "completed", "request_saved": True, "scores": None,
            "scoring_attempts": failures, "artifacts": {"正文/第001章.md": "生成成功的原文"},
            "judge_results": {"2:quality": {"score": 4, "达标": ["原文证据"], "不足": []}},
            "trace_ids": ["generation-trace"], "evaluation_trace_ids": ["previous-evaluation"],
            "usage": {"generation": {"total_tokens": 100}, "judge_calls": 1,
                      "judge_attempts": [{"judge_id": 2, "status": "completed", "usage": {"total_tokens": 20}}]}}


def running_record(samples):
    run = run_record()
    run.update(status="running", phase="baseline", samples=samples)
    return run


class ExperimentRetryLimitTest(DatabaseTest):
    def test_first_two_failures_back_off_and_third_stops_with_evidence_intact(self):
        sample = generated_sample()
        run = running_record([sample])
        evidence = {key: copy.deepcopy(sample[key]) for key in
                    ("artifacts", "judge_results", "usage", "trace_ids", "evaluation_trace_ids")}
        repo.put_run(run)
        with patch.object(evaluation, "score_sample", side_effect=RuntimeError("评分不可用")) as score, \
             patch.object(runner.time, "time", return_value=100):
            for count in (1, 2):
                runner.process_sample(run, sample)
                self.assertEqual(sample["scoring_attempts"], count)
                self.assertEqual(sample["status"], "evaluation_error")
                self.assertEqual(sample["score_retry_at"], 100 + 5 * count)
            runner.process_sample(run, sample)
            self.assertEqual(sample["status"], "failed")
            self.assertEqual(sample["scoring_attempts"], 3)
            self.assertNotIn("score_retry_at", sample)
            self.assertRegex(sample["error"], r"3\s*轮上限")
            self.assertIn("保留", sample["error"])
            self.assertTrue(sample["completed"])
            self.assertEqual(sample["generation_status"], "completed")
            self.assertIsNone(sample["scores"])
            self.assertFalse(runner._run_pending(run, run["samples"]))
            runner.process_sample(run, sample)
            self.assertEqual(score.call_count, 3)
        for key, value in evidence.items():
            self.assertEqual(sample[key], value)
        self.assertEqual(repo.get_run(run["id"])["samples"][0]["status"], "failed")

    def test_third_round_success_is_normal_completion(self):
        sample = generated_sample()
        run = running_record([sample])
        score_value = {"overall": 4.0, "dimensions": {"quality": 4.0}, "reasons": {}}
        with patch.object(evaluation, "score_sample", side_effect=[RuntimeError("一次"), RuntimeError("二次"), score_value]) as score:
            for _ in range(3):
                runner.process_sample(run, sample)
        self.assertEqual(score.call_count, 3)
        self.assertEqual(sample["status"], "completed")
        self.assertEqual(sample["scores"], score_value)
        self.assertIsNone(sample["error"])
        self.assertNotIn("score_retry_at", sample)
        self.assertFalse(runner._run_pending(run, run["samples"]))

    def test_recovered_exhausted_samples_clear_future_retry_without_scoring(self):
        for failures in (3, 12):
            with self.subTest(failures=failures):
                sample = generated_sample(failures=failures)
                sample["score_retry_at"] = 9999
                run = running_record([sample])
                with patch.object(evaluation, "score_sample", return_value={"overall": 4.0}) as score, patch.object(runner.time, "time", return_value=100):
                    self.assertTrue(runner._run_pending(run, run["samples"]))
                score.assert_not_called()
                self.assertEqual(sample["status"], "failed")
                self.assertEqual(sample["scoring_attempts"], failures)
                self.assertNotIn("score_retry_at", sample)
                self.assertFalse(runner._run_pending(run, run["samples"]))

    def test_repeated_exhaustion_guard_is_idempotent(self):
        sample = generated_sample(failures=5)
        run = running_record([sample])
        with patch.object(evaluation, "score_sample", return_value={"overall": 4.0}) as score:
            runner.process_sample(run, sample)
            persisted = repo.get_run(run["id"])
            events = copy.deepcopy(run["events"])
            runner.process_sample(run, sample)
        score.assert_not_called()
        self.assertEqual(run["events"], events)
        self.assertEqual(repo.get_run(run["id"]), persisted)

    def test_exhausted_sample_does_not_block_sibling_or_pass_quality_gate(self):
        failed = generated_sample(failures=2)
        sibling = generated_sample("sample-two")
        run = running_record([failed, sibling])
        with patch.object(evaluation, "score_sample", side_effect=RuntimeError("第三次失败")):
            runner.process_sample(run, failed)
        with patch.object(evaluation, "score_sample", return_value={"overall": 5.0}) as score:
            self.assertTrue(runner._run_pending(run, run["samples"]))
            self.assertEqual(score.call_args.args[1]["id"], "sample-two")
        self.assertEqual(sibling["status"], "completed")
        self.assertFalse(runner._run_pending(run, run["samples"]))
        baseline = [{"package_id": item["package_id"], "repeat": 1, "completed": True, "scores": {"overall": 3.0}}
                    for item in run["samples"]]
        report = evaluation.compare_results(baseline, run["samples"], completion_rate=1, quality_gain=.1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["candidate"]["completion_rate"], 1)
        self.assertEqual(report["candidate"]["evaluation_errors"], 1)
        self.assertIn("存在缺少有效评分的已完成样本", report["problems"])
        self.assertIsNone(failed["scores"])

    def test_saved_success_precedes_exhaustion_guard(self):
        sample = generated_sample(failures=9)
        sample["scores"] = {"overall": 4.0}
        run = running_record([sample])
        with patch.object(evaluation, "score_sample") as score:
            runner.process_sample(run, sample)
        score.assert_not_called()
        self.assertEqual(sample["status"], "completed")
        self.assertEqual(sample["scores"], {"overall": 4.0})

    def test_cancellation_does_not_consume_a_failed_round(self):
        sample = generated_sample(failures=1)
        run = running_record([sample])
        with patch.object(evaluation, "score_sample", side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError):
                runner.process_sample(run, sample)
        self.assertEqual(sample["scoring_attempts"], 1)
        self.assertTrue(sample["completed"])
        self.assertEqual(sample["generation_status"], "completed")

    def test_recovery_does_not_rewrite_terminal_experiment_history(self):
        run = running_record([generated_sample(failures=12)])
        run["status"] = "stopped"
        repo.put_run(run)
        before = repo.get_run(run["id"])
        with patch.object(runner, "start_worker") as worker:
            runner.recover_runs()
        worker.assert_not_called()
        self.assertEqual(repo.get_run(run["id"]), before)
