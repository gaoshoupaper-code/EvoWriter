"""leaderboard 五维聚合后端测试（REQ-20260921-135543 FR-003）。

覆盖：
- get_leaderboard 各版本 dimension_means 与行级 scores_json 逐维均值一致
- 损坏/缺失 scores_json 的行不进维度聚合，不影响其他行
- 无 done 行时 versions 为空、不抛错
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class LeaderboardTestBase(unittest.TestCase):
    """临时 DB fixture（与 test_benchmark_run_details 同模式）。"""

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

    def tearDown(self) -> None:
        try:
            self.db.get_conn().close()
        except Exception:
            pass
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_scores(self, dims: dict[str, float], overall: float) -> str:
        return json.dumps(
            {
                "rubric_version": "v3",
                "scores": dims,
                "tags": {},
                "reasons": {},
                "overall": overall,
                "rule_delivery": {"key": "delivery_complete", "passed": True, "problems": []},
            },
            ensure_ascii=False,
        )


class GetLeaderboardDimensionMeansTest(LeaderboardTestBase):
    def _make_done_rows(self, batch_id: str, case_ids: list[str], version: int, per_case_scores: dict[str, str]) -> None:
        from app.benchmark import repo

        rows = self.db.query_all(
            "SELECT * FROM benchmark_runs WHERE batch_id=? AND harness_version=?",
            (batch_id, version),
        )
        for row in rows:
            if row["case_id"] not in per_case_scores:
                continue
            repo.set_trace(row["id"], f"trace-{row['case_id']}-{version}")
            repo.set_result(row["id"], eval_id=None, scores_json=per_case_scores[row["case_id"]])

    def test_dimension_means_match_row_scores(self):
        """v7 两行 done：维度均值 = 两行同维平均。"""
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=["case-001", "case-002"], versions=[7],
            golden_revision="rev-x", seeds=1, rubric_version="v3", judge_fp="jfp",
        )
        self._make_done_rows(
            batch_id, ["case-001", "case-002"], 7,
            {
                "case-001": self._make_scores({"需求兑现": 4, "设定自洽": 3}, 3.5),
                "case-002": self._make_scores({"需求兑现": 2, "设定自洽": 5}, 3.5),
            },
        )

        result = repo.get_leaderboard("rev-x")
        self.assertEqual(len(result["versions"]), 1)
        v7 = result["versions"][0]
        self.assertEqual(v7["version"], 7)
        self.assertAlmostEqual(v7["dimension_means"]["需求兑现"], 3.0)
        self.assertAlmostEqual(v7["dimension_means"]["设定自洽"], 4.0)

    def test_corrupt_scores_json_excluded_from_dimension_means(self):
        """损坏 scores_json 的 done 行不进维度聚合；正常行照常聚合。"""
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=["case-001", "case-002"], versions=[7],
            golden_revision="rev-x", seeds=1, rubric_version="v3", judge_fp="jfp",
        )
        self._make_done_rows(
            batch_id, ["case-001", "case-002"], 7,
            {
                "case-001": self._make_scores({"需求兑现": 5}, 5.0),
                "case-002": "{not-json",
            },
        )

        result = repo.get_leaderboard("rev-x")
        v7 = result["versions"][0]
        self.assertAlmostEqual(v7["dimension_means"]["需求兑现"], 5.0)
        self.assertNotIn("设定自洽", v7["dimension_means"])
        # 总分侧沿用既有 scores_avg 解析（损坏行同样排除），avg_score 只由正常行贡献
        self.assertAlmostEqual(v7["avg_score"], 5.0)

    def test_zero_and_foreign_keys_excluded_from_dimension_means(self):
        """0 分（无法判断）与词表外维度不进均值——与 report/stats 同口径。"""
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=["case-001", "case-002"], versions=[7],
            golden_revision="rev-x", seeds=1, rubric_version="v3", judge_fp="jfp",
        )
        self._make_done_rows(
            batch_id, ["case-001", "case-002"], 7,
            {
                "case-001": self._make_scores({"需求兑现": 4, "设定自洽": 0}, 2.0),
                "case-002": self._make_scores({"需求兑现": 2, "外来维度": 5}, 3.5),
            },
        )

        result = repo.get_leaderboard("rev-x")
        v7 = result["versions"][0]
        # 需求兑现：4 与 2 的均值；设定自洽只有 0 分被排除（无有效样本→不出现）；外来维度被词表过滤
        self.assertAlmostEqual(v7["dimension_means"]["需求兑现"], 3.0)
        self.assertNotIn("设定自洽", v7["dimension_means"])
        self.assertNotIn("外来维度", v7["dimension_means"])

    def test_non_dict_scores_json_skipped(self):
        """scores_json 为合法 JSON 但非 dict（如 null/[]）不抛错，整行跳过。"""
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=["case-001", "case-002"], versions=[7],
            golden_revision="rev-x", seeds=1, rubric_version="v3", judge_fp="jfp",
        )
        self._make_done_rows(
            batch_id, ["case-001", "case-002"], 7,
            {
                "case-001": self._make_scores({"需求兑现": 4}, 4.0),
                "case-002": "null",
            },
        )

        result = repo.get_leaderboard("rev-x")
        v7 = result["versions"][0]
        self.assertAlmostEqual(v7["dimension_means"]["需求兑现"], 4.0)

    def test_no_done_rows_returns_empty_versions(self):
        from app.benchmark import repo

        repo.create_batch(
            case_ids=["case-001"], versions=[7],
            golden_revision="rev-y", seeds=1, rubric_version="v3", judge_fp="jfp",
        )
        result = repo.get_leaderboard("rev-y")
        self.assertEqual(result["versions"], [])


if __name__ == "__main__":
    unittest.main()
