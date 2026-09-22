"""benchmark 开销统计与报告呈现测试（REQ-20260922-162823 FR-006 / DEC-007）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 本地运行修复（先于本文件 import runner→httpx→zstandard）：PYTHONPATH 含仓库/
# contracts 时 stdlib platform 被 contracts/platform 子包遮蔽。显式从 stdlib
# 加载并注册 sys.modules（缓存优先于 sys.path 搜索），不修改 sys.path——
# tests 目录的 conftest 存在与否会改变 pytest 收集行为（已实测），故内联于此。
import importlib.util

if not hasattr(sys.modules.get("platform"), "python_implementation"):
    _stdlib_platform = Path(sys.base_prefix) / "Lib" / "platform.py"
    if _stdlib_platform.is_file():
        _spec = importlib.util.spec_from_file_location("platform", str(_stdlib_platform))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules["platform"] = _mod
        _spec.loader.exec_module(_mod)


class CostStatsTestBase(unittest.TestCase):
    """临时 DB fixture（与 test_benchmark_v3 同模式）。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["EVOLUTION_DB"] = str(Path(self._tmpdir) / "test.db")
        import sqlite3

        import app.core.db as db

        # 仅注入新连接 + init_db 建表（全走 db._conn）；不 reload settings——
        # settings 是跨模块共享的导入期单例，reload 会把后续模块（如
        # test_fr007_eval_scope 的 module 级 fixture）绑到本类的临时库上，
        # 引发全量运行时的跨模块污染。
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

    # ── 造数辅助 ─────────────────────────────────────────────

    def _insert_run_with_cost(self, trace_id: str, *, duration_ms: int,
                              llm_nodes: list[tuple[int, int]]) -> None:
        self.db.execute(
            """INSERT INTO runs (trace_id, workspace_id, status, duration_ms, ingested_at)
               VALUES (?, ?, 'completed', ?, '2026-09-22T00:00:00Z')""",
            (trace_id, f"ws-{trace_id}", duration_ms),
        )
        for i, (inp, out) in enumerate(llm_nodes):
            self.db.execute(
                """INSERT INTO nodes (trace_id, node_id, kind, usage_input, usage_output)
                   VALUES (?, ?, 'llm', ?, ?)""",
                (trace_id, f"n{i}", inp, out),
            )

    def _insert_bench_row(self, batch_id: str, *, trace_id: str, case_id: str,
                          version: int, scores: dict | None = None,
                          cost: tuple | None = None) -> int:
        cur = self.db.execute(
            """INSERT INTO benchmark_runs
               (batch_id, case_id, harness_version, golden_revision, trace_id,
                status, ran_at, seed, rubric_version, judge_fp, model_fp, manifest_fp,
                harness_commit, concurrency)
               VALUES (?, ?, ?, 'gr-test', ?, 'done', '2026-09-22T00:00:00Z',
                       1, 'v4', 'jfp', 'mfp', 'mfp-test', 'c0', 1)""",
            (batch_id, case_id, version, trace_id),
        )
        run_id = cur.lastrowid
        if scores is not None:
            self.db.execute(
                "UPDATE benchmark_runs SET scores_json=? WHERE id=?",
                (json.dumps(scores, ensure_ascii=False), run_id),
            )
        if cost is not None:
            inp, out, calls, wall = cost
            self.db.execute(
                """UPDATE benchmark_runs SET input_tokens=?, output_tokens=?,
                   llm_calls=?, wall_clock_ms=? WHERE id=?""",
                (inp, out, calls, wall, run_id),
            )
        return run_id


class MigrationAndSetCostTest(CostStatsTestBase):
    def test_migration_columns_exist(self) -> None:
        """init_db 后 benchmark_runs 含 4 个开销列（含旧库 ALTER 路径）。"""
        columns = {
            row[1] for row in self.db.get_conn()
            .execute("PRAGMA table_info(benchmark_runs)").fetchall()
        }
        self.assertTrue(
            {"input_tokens", "output_tokens", "llm_calls", "wall_clock_ms"} <= columns,
        )

    def test_set_cost_and_null_semantics(self) -> None:
        """set_cost 写入；聚合失败路径传全 None 不炸（FR-006 失败语义）。"""
        from app.benchmark import repo as bench_repo

        run_id = self._insert_bench_row("b1", trace_id="t1", case_id="case-001", version=13)
        bench_repo.set_cost(
            run_id, input_tokens=100, output_tokens=200, llm_calls=3, wall_clock_ms=45000,
        )
        row = self.db.query_one("SELECT * FROM benchmark_runs WHERE id=?", (run_id,))
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["llm_calls"], 3)

        bench_repo.set_cost(
            run_id, input_tokens=None, output_tokens=None,
            llm_calls=None, wall_clock_ms=None,
        )
        row = self.db.query_one("SELECT * FROM benchmark_runs WHERE id=?", (run_id,))
        self.assertIsNone(row["input_tokens"])

    def test_aggregate_run_cost(self) -> None:
        """runner._aggregate_run_cost：nodes 聚合 + runs.duration_ms。"""
        from app.benchmark import runner

        self._insert_run_with_cost(
            "t-cost", duration_ms=120000,
            llm_nodes=[(1000, 2000), (3000, 4000), (500, None)],
        )
        result = runner._aggregate_run_cost("t-cost")
        self.assertEqual(result["input_tokens"], 4500)
        self.assertEqual(result["output_tokens"], 6000)
        self.assertEqual(result["llm_calls"], 3)
        self.assertEqual(result["wall_clock_ms"], 120000)

    def test_aggregate_run_cost_missing_trace(self) -> None:
        """trace 不存在：token/时长 None，不抛异常。"""
        from app.benchmark import runner

        result = runner._aggregate_run_cost("t-nonexistent")
        self.assertIsNone(result["wall_clock_ms"])


class ReportCostQuotaLayerTest(CostStatsTestBase):
    def _scores(self, overall: float, quota: dict | None = None) -> dict:
        data = {
            "overall": overall,
            "scores": {"需求兑现": 3, "设定自洽": 4},
            "rule_delivery": {"passed": True, "problems": []},
        }
        if quota is not None:
            data["rule_quota"] = quota
        return data

    def test_report_includes_cost_quota_layers(self) -> None:
        """build_report 含 cost / quota / blank_layers 三块（AC-005）。"""
        from app.benchmark import report as bench_report

        self._insert_bench_row(
            "rb", trace_id="t1", case_id="case-001", version=13,
            scores=self._scores(3.5, {"status": "checked", "passed": True, "summary": "达标"}),
            cost=(1000, 2000, 5, 60000),
        )
        self._insert_bench_row(
            "rb", trace_id="t2", case_id="case-002", version=13,
            scores=self._scores(4.0, {"status": "skipped_minimal"}),
            cost=(1500, 2500, 7, 90000),
        )
        result = bench_report.build_report("rb")
        self.assertEqual(result["status"], "ok")
        # cost：合计与均值
        self.assertEqual(result["cost"]["input_tokens"]["total"], 2500)
        self.assertEqual(result["cost"]["llm_calls"]["mean"], 6.0)
        # quota：1 行核对通过、1 行 minimal 跳过
        self.assertEqual(result["quota"]["checked"], 1)
        self.assertEqual(result["quota"]["passed"], 1)
        self.assertEqual(result["quota"]["skipped_minimal"], 1)
        # blank_layers：case-001=full、case-002=semi（真实 golden 分档）
        layers = {l["blank_level"] for l in result["blank_layers"]}
        self.assertIn("full", layers)
        self.assertIn("semi", layers)

    def test_compare_batches_includes_cost_and_layers(self) -> None:
        """compare_batches 输出含开销并排与分层均分差（DEC-007 辅判读）。"""
        from app.benchmark import stats as bench_stats

        for batch, version, inp in (("ba", 14, 5000), ("bb", 13, 2000)):
            self._insert_bench_row(
                batch, trace_id=f"{batch}-t1", case_id="case-001", version=version,
                scores=self._scores(3.5 if version == 14 else 3.0),
                cost=(inp, inp * 2, 9, 300000),
            )
            self._insert_bench_row(
                batch, trace_id=f"{batch}-t2", case_id="case-002", version=version,
                scores=self._scores(4.0 if version == 14 else 3.8),
                cost=(inp, inp, 8, 280000),
            )
        result = bench_stats.compare_batches("ba", "bb")
        self.assertTrue(result["comparable"], msg=str(result.get("problems")))
        self.assertEqual(result["cost"]["input_tokens"]["candidate_mean"], 5000.0)
        self.assertEqual(result["cost"]["input_tokens"]["production_mean"], 2000.0)
        self.assertTrue(result["blank_layers"])
        full_layer = next(l for l in result["blank_layers"] if l["blank_level"] == "full")
        self.assertEqual(full_layer["n_candidate"], 1)
        self.assertEqual(full_layer["diff"], 0.5)


if __name__ == "__main__":
    unittest.main()
