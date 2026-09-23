"""GET /api/benchmark/batches 均分与总数测试（REQ-20260923-131103 FR-002/AC-002）。

avg_overall 口径：批次内全部 done 行 scores.overall 的算术平均（两位小数）；
失败/取消/未跑行不计入；无 done 行为 None。total 为去重批次总数（加载更多用）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BatchListAvgTest(unittest.TestCase):
    """临时 DB fixture（与 test_benchmark_run_api 同模式）。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["EVOLUTION_DB"] = str(Path(self._tmpdir) / "test.db")
        os.environ["EXECUTOR_WORKSPACE"] = self._tmpdir
        import importlib
        import sqlite3

        import app.core.settings as settings_mod

        importlib.reload(settings_mod)
        import app.core.db as db

        conn = sqlite3.connect(os.environ["EVOLUTION_DB"], check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db._conn = conn
        db.init_db()
        self.db = db
        self.repo = __import__("app.benchmark.repo", fromlist=["repo"])

    def tearDown(self) -> None:
        try:
            self.db.get_conn().close()
        except Exception:
            pass
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_batch(self, bid_seed: str, done_scores: list[float], failed: int = 0) -> str:
        """建一个批次并把指定分数的行推到 done，其余推到 failed。"""
        repo = self.repo
        total = len(done_scores) + failed
        batch_id = repo.create_batch(
            case_ids=[f"case-{i}" for i in range(total)], versions=[7],
            golden_revision="rev-x", seeds=1,
            rubric_version="v4", judge_fp="jfp",
        )
        runs = repo.list_batch_runs(batch_id)["items"]
        done_iter = iter(done_scores)
        for run in runs:
            # 走完整状态流转：pending → running → evaluating → done/failed
            repo.mark_running(run["id"])
            repo.set_trace(run["id"], f"trace-{run['id']}")
            score = next(done_iter, None)
            if score is not None:
                repo.set_result(
                    run["id"], eval_id=None,
                    scores_json=f'{{"overall": {score}}}',
                )
            else:
                repo.mark_failed(run["id"], "boom")
        return batch_id

    def test_avg_overall_averages_done_rows_only(self):
        """done 3.0/4.0 + failed → 均分 3.5，失败行不计入。"""
        self._make_batch("a", done_scores=[3.0, 4.0], failed=1)
        batches = self.repo.get_recent_batches(20)
        self.assertEqual(len(batches), 1)
        self.assertAlmostEqual(batches[0]["avg_overall"], 3.5)

    def test_avg_overall_none_when_no_done_rows(self):
        """全失败批次均分为 None（前端显示「—」）。"""
        self._make_batch("b", done_scores=[], failed=2)
        batches = self.repo.get_recent_batches(20)
        self.assertIsNone(batches[0]["avg_overall"])

    def test_avg_overall_rounded_to_two_decimals(self):
        """3.333... → 3.33（两位小数）。"""
        self._make_batch("c", done_scores=[3.0, 3.1, 3.9])
        batches = self.repo.get_recent_batches(20)
        self.assertEqual(batches[0]["avg_overall"], 3.33)

    def test_count_batches_counts_distinct(self):
        """两个批次 → total 2（加载更多按钮可见性依据）。"""
        self._make_batch("d", done_scores=[4.0])
        self._make_batch("e", done_scores=[], failed=2)
        self.assertEqual(self.repo.count_batches(), 2)


if __name__ == "__main__":
    unittest.main()
