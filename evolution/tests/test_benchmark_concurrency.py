"""评测批次并发调度测试（REQ-20260920-104714 / FR-001 / AC-001、AC-002）。

覆盖：
- 原子 claim：多线程并发抢占不重复取行（风险「进度统计竞态」的处置验证）
- 并发上限：同时 running 行数 ≤ 所选并发度（AC-001）
- 并发度持久化：行内 concurrency 与批次聚合返回一致（AC-001）
- 单行失败不传染：一行重试用尽转 failed，其余行照常完成（AC-002）
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ConcurrencyTestBase(unittest.TestCase):
    """临时 DB fixture（与 test_benchmark_v3 同模式）。"""

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


def _make_batch(db, *, case_ids, seeds=2, concurrency=3):
    from app.benchmark import repo as bench_repo

    return bench_repo.create_batch(
        case_ids=case_ids,
        versions=[1],
        golden_revision="rev-test",
        seeds=seeds,
        rubric_version="v3-test",
        judge_fp="fp-test",
        concurrency=concurrency,
    )


class ClaimAtomicityTest(ConcurrencyTestBase):
    def test_claim_no_duplicate_under_threads(self):
        """8 线程并发抢占 10 行，无重复、无遗漏。"""
        from app.benchmark import repo as bench_repo

        batch_id = _make_batch(self.db, case_ids=[f"case-{i:03d}" for i in range(1, 6)], seeds=2)

        claimed: list[int] = []
        lock = threading.Lock()

        def claim_loop():
            while True:
                row = bench_repo.claim_next_pending(batch_id)
                if row is None:
                    return
                with lock:
                    claimed.append(row["id"])

        threads = [threading.Thread(target=claim_loop) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(claimed), 10, "应恰好抢占全部 10 行")
        self.assertEqual(len(set(claimed)), 10, "不允许任何行被两个 worker 抢到")
        statuses = [r["status"] for r in self.db.query_all("SELECT status FROM benchmark_runs")]
        self.assertEqual(set(statuses), {"running"}, "全部行应处于 running")


class ConcurrencyCapTest(ConcurrencyTestBase):
    def test_running_never_exceeds_concurrency(self):
        """worker 池执行期间，同时 active 的行数 ≤ 并发度，且全部完成。"""
        from unittest.mock import patch

        from app.benchmark import runner

        batch_id = _make_batch(self.db, case_ids=[f"case-{i:03d}" for i in range(1, 4)], seeds=2)

        # Barrier 强制 3 行真同时到达：调度不并发则 barrier 超时直接报错，
        # 消除 sleep 计时断言的 flaky（review testing 意见）
        barrier = threading.Barrier(3, timeout=15)
        active = 0
        max_active = 0
        guard = threading.Lock()

        def fake_execute(row, judge_config_id=None, **_):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            barrier.wait()  # 3 行到齐才放行 → 真实并发发生
            with guard:
                active -= 1
            self.db.execute(
                "UPDATE benchmark_runs SET status='done', scores_json='{}', finished_at=? WHERE id=?",
                ("2026-09-20T00:00:00Z", row["id"]),
            )

        with patch.object(runner, "_execute_one", fake_execute):
            runner._run_batch_sync(batch_id, concurrency=3)

        self.assertEqual(max_active, 3, "3 个 worker 必须同时执行（Barrier 保证真实并发）")
        self.assertLessEqual(max_active, 3, "同时运行数不得超过并发度 3")

        rows = self.db.query_all("SELECT status FROM benchmark_runs WHERE batch_id=?", (batch_id,))
        self.assertEqual({r["status"] for r in rows}, {"done"}, "全部行应完成")

    def test_concurrency_one_is_serial(self):
        """并发度 1 时同一时刻只有一行在执行（与旧串行行为一致）。"""
        from unittest.mock import patch

        from app.benchmark import runner

        batch_id = _make_batch(self.db, case_ids=["case-a", "case-b", "case-c"], seeds=1, concurrency=1)

        active = 0
        max_active = 0
        guard = threading.Lock()

        def fake_execute(row, judge_config_id=None, **_):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            self.db.execute(
                "UPDATE benchmark_runs SET status='done', scores_json='{}', finished_at=? WHERE id=?",
                ("2026-09-20T00:00:00Z", row["id"]),
            )
            with guard:
                active -= 1

        with patch.object(runner, "_execute_one", fake_execute):
            runner._run_batch_sync(batch_id, concurrency=1)

        self.assertEqual(max_active, 1, "并发度 1 必须严格串行")


class FailureIsolationTest(ConcurrencyTestBase):
    def test_single_row_failure_does_not_block_others(self):
        """case-b 每次执行都失败（重试用尽转 failed），其余行照常完成（AC-002）。

        单 case 系统性失败不触发止损（REQ-20260920-192126：连续失败需涉及
        ≥2 个不同行），与「单行失败不传染」语义兼容。
        """
        from unittest.mock import patch

        from app.benchmark import runner

        batch_id = _make_batch(self.db, case_ids=["case-a", "case-b", "case-c"], seeds=1)

        def fake_execute(row, judge_config_id=None, **_):
            if row["case_id"] == "case-b":
                raise RuntimeError("评分重试用尽")
            self.db.execute(
                "UPDATE benchmark_runs SET status='done', scores_json='{}', finished_at=? WHERE id=?",
                ("2026-09-20T00:00:00Z", row["id"]),
            )

        with patch.object(runner, "_execute_one", fake_execute), \
             patch.object(runner, "_RETRY_BACKOFF_S", 0.0):
            runner._run_batch_sync(batch_id, concurrency=3)

        rows = self.db.query_all(
            "SELECT case_id, status FROM benchmark_runs WHERE batch_id=?", (batch_id,)
        )
        by_case = {r["case_id"]: r["status"] for r in rows}
        self.assertEqual(by_case["case-a"], "done")
        self.assertEqual(by_case["case-c"], "done")
        self.assertEqual(by_case["case-b"], "failed", "重试用尽（3 次）后应转 failed")
        self.assertNotIn("cancelled", by_case.values(), "单行失败不得触发整批止损")

        from app.benchmark import repo as bench_repo
        batch = bench_repo.get_batch(batch_id)
        self.assertEqual(batch["status"], "partial", "含失败行的批次终态为 partial")
        self.assertEqual(batch["progress"]["failed"], 1)


class ConcurrencyMigrationTest(ConcurrencyTestBase):
    def test_alter_path_restores_column(self):
        """存量部署走 ALTER 迁移路径：drop 列后 init_db 幂等补回，存量行默认 1。"""
        from app.benchmark import repo as bench_repo

        batch_id = _make_batch(self.db, case_ids=["case-a"], seeds=1)
        self.db.execute("ALTER TABLE benchmark_runs DROP COLUMN concurrency")
        cols = {r["name"] for r in self.db.query_all("PRAGMA table_info(benchmark_runs)")}
        self.assertNotIn("concurrency", cols, "前置：列已删除")

        self.db.init_db()  # 幂等迁移应补回列

        cols = {r["name"] for r in self.db.query_all("PRAGMA table_info(benchmark_runs)")}
        self.assertIn("concurrency", cols)
        rows = self.db.query_all(
            "SELECT concurrency FROM benchmark_runs WHERE batch_id=?", (batch_id,)
        )
        self.assertEqual({r["concurrency"] for r in rows}, {1}, "存量行回填默认 1（历史批次均为串行）")

        # 幂等：再跑一次不炸
        self.db.init_db()


class ConcurrencyPersistenceTest(ConcurrencyTestBase):
    def test_concurrency_persisted_in_rows_and_batch(self):
        """并发度写入每行，批次聚合正确返回（AC-001）。"""
        from app.benchmark import repo as bench_repo

        batch_id = _make_batch(self.db, case_ids=["case-a"], seeds=1, concurrency=5)
        rows = self.db.query_all(
            "SELECT concurrency FROM benchmark_runs WHERE batch_id=?", (batch_id,)
        )
        self.assertEqual({r["concurrency"] for r in rows}, {5})

        batch = bench_repo.get_batch(batch_id)
        self.assertEqual(batch["concurrency"], 5)

        recent = bench_repo.get_recent_batches(5)
        target = [b for b in recent if b["batch_id"] == batch_id]
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["concurrency"], 5)


if __name__ == "__main__":
    unittest.main()
