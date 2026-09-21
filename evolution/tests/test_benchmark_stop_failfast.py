"""评测批次可控性测试（REQ-20260920-192126 / FR-001/FR-002/FR-004）。

覆盖：
- AC-001 手动停止：非终态行转 cancelled、worker 叫停 executor 任务、重复调用幂等
- AC-002 僵尸批次：无存活 worker 时停止端点同样清干净（running/evaluating 不残留）
- AC-003 系统性失败自动止损：连续 3 次失败（涉及 ≥2 个不同 case）停批，无第 4 次尝试
- AC-004 偶发失败不误伤：失败-成功交错序列不触发止损
- AC-006 轮询 4xx 快速失败：404 秒级判败；503 维持退避重试
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class StopFailFastTestBase(unittest.TestCase):
    """临时 DB fixture（与 test_benchmark_concurrency 同模式）。"""

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

    def _make_batch(self, *, case_ids, seeds=1, concurrency=3):
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

    def _row_statuses(self, batch_id):
        return {
            r["id"]: r["status"]
            for r in self.db.query_all(
                "SELECT id, status FROM benchmark_runs WHERE batch_id=?", (batch_id,)
            )
        }


class ManualStopTest(StopFailFastTestBase):
    """AC-001：手动停止生效且幂等。"""

    def test_worker_cancel_path_marks_cancelled_and_stops_executor(self):
        """worker 手中行因批次停止转 cancelled，且叫停 executor 任务（worker 收尾路径）。"""
        from app.benchmark import runner

        batch_id = self._make_batch(case_ids=["case-a"], seeds=1, concurrency=1)
        stop_calls: list[str] = []

        def fake_execute(row, judge_config_id=None, **_):
            # 模拟真实时序：worker 执行中用户点停止（写 meta + 清行 + set 事件），
            # 随后 worker 在下一个取消检查点感知上抛（携带 task_id）
            runner.request_stop(batch_id)
            raise runner._BatchCancelled(task_id="task-exec-1")

        with patch.object(runner, "_execute_one", fake_execute), \
             patch.object(runner, "_stop_executor_task",
                          lambda task_id: stop_calls.append(task_id)):
            runner._run_batch_sync(batch_id, concurrency=1)

        statuses = self._row_statuses(batch_id)
        self.assertEqual(set(statuses.values()), {"cancelled"}, "被取消的行应转 cancelled")
        self.assertEqual(stop_calls, ["task-exec-1"], "应尽力叫停 executor 任务")

        from app.benchmark import repo as bench_repo
        batch = bench_repo.get_batch(batch_id)
        self.assertEqual(batch["status"], "cancelled")
        self.assertEqual(batch["stop_reason"], "user_stop")

    def test_request_stop_clears_all_active_rows_and_idempotent(self):
        """停止端点清全部非终态行（含 evaluating），重复调用幂等。"""
        from app.benchmark import runner
        from app.benchmark import repo as bench_repo

        batch_id = self._make_batch(case_ids=["case-a", "case-b", "case-c"], seeds=2)

        # 构造混合状态：2 pending + 2 running + 2 evaluating
        rows = sorted(self._row_statuses(batch_id).keys())
        self.db.execute(
            "UPDATE benchmark_runs SET status='evaluating' WHERE id IN (?, ?)", (rows[0], rows[1])
        )
        self.db.execute(
            "UPDATE benchmark_runs SET status='running' WHERE id IN (?, ?)", (rows[2], rows[3])
        )

        result = runner.request_stop(batch_id)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["stop_reason"], "user_stop")
        statuses = self._row_statuses(batch_id)
        self.assertEqual(set(statuses.values()), {"cancelled"}, "非终态行应全部转 cancelled")
        self.assertEqual(result["progress"]["cancelled"], 6)

        # 幂等：重复调用不报错、状态不变
        again = runner.request_stop(batch_id)
        self.assertEqual(again["status"], "cancelled")
        self.assertEqual(self._row_statuses(batch_id), statuses, "重复停止不得改变行状态")

    def test_completed_rows_survive_stop(self):
        """停止不得覆盖已完成行的分数（终态守卫）。"""
        from app.benchmark import runner

        batch_id = self._make_batch(case_ids=["case-a", "case-b"], seeds=1)

        rows = sorted(self._row_statuses(batch_id).keys())
        self.db.execute(
            "UPDATE benchmark_runs SET status='done', scores_json='{\"overall\": 4.5}' WHERE id=?",
            (rows[0],),
        )

        runner.request_stop(batch_id)

        statuses = self._row_statuses(batch_id)
        self.assertEqual(statuses[rows[0]], "done", "已完成行不得被停止覆盖")
        row = self.db.query_one("SELECT scores_json FROM benchmark_runs WHERE id=?", (rows[0],))
        self.assertIsNotNone(row["scores_json"], "分数不得丢失")


class ZombieBatchCleanupTest(StopFailFastTestBase):
    """AC-002：僵尸批次（无存活 worker）停止清理。"""

    def test_stop_without_worker_clears_everything(self):
        """服务重启残留的 running 批次，点停止后无非终态行残留。"""
        from app.benchmark import runner
        from app.benchmark import repo as bench_repo

        batch_id = self._make_batch(case_ids=["case-a", "case-b", "case-c"], seeds=1)

        # 模拟重启残留：不启动任何 worker，直接把行改成混合执行中状态
        rows = sorted(self._row_statuses(batch_id).keys())
        self.db.execute("UPDATE benchmark_runs SET status='running' WHERE id=?", (rows[0],))
        self.db.execute("UPDATE benchmark_runs SET status='evaluating' WHERE id=?", (rows[1],))

        result = runner.request_stop(batch_id)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["stop_reason"], "user_stop")
        statuses = self._row_statuses(batch_id)
        self.assertEqual(
            set(statuses.values()), {"cancelled"},
            "僵尸批次停止后不得残留 pending/running/evaluating",
        )
        batch = bench_repo.get_batch(batch_id)
        self.assertEqual(batch["progress"]["active"], 0)


class AutoFailFastTest(StopFailFastTestBase):
    """AC-003 / AC-004：连续失败自动止损与偶发失败不误伤。"""

    def test_systematic_failure_halts_after_three_attempts(self):
        """executor 全挂：3 个不同 case 并发失败即停批，无第 4 次尝试（AC-003）。"""
        from app.benchmark import runner
        from app.benchmark import repo as bench_repo

        batch_id = self._make_batch(
            case_ids=["case-a", "case-b", "case-c"], seeds=1, concurrency=3
        )
        attempts: list[int] = []
        lock = threading.Lock()

        def fake_execute(row, judge_config_id=None, **_):
            with lock:
                attempts.append(row["id"])
            raise RuntimeError("executor 不可达")

        # 退避保留真实节奏（不为 0）：第三个 worker 触发止损（set 事件 + 清行）
        # 必须先于其余 worker 退避结束，才能严格保证无第 4 次尝试
        with patch.object(runner, "_execute_one", fake_execute), \
             patch.object(runner, "_RETRY_BACKOFF_S", 0.5):
            runner._run_batch_sync(batch_id, concurrency=3)

        self.assertEqual(len(attempts), 3, "跨 case 连续 3 次失败即停批，不得有第 4 次尝试")

        batch = bench_repo.get_batch(batch_id)
        self.assertEqual(batch["status"], "failed", "止损批次终态为 failed")
        self.assertEqual(batch["stop_reason"], "auto_fail")
        self.assertEqual(batch["progress"]["failed"], 3)
        self.assertEqual(batch["progress"]["cancelled"], 0, "全部行已失败，无残留待取消")

    def test_same_case_seed_failures_do_not_halt_batch(self):
        """同 case 系统性失败：3 个 seed 全灭只计 1 个 case，不触发止损（AC-003b）。

        行级隔离重试吃满（3 行 × 3 次尝试 = 9 次）后整批自然结束，
        无 cancelled 行、无 auto_fail。
        """
        from app.benchmark import runner
        from app.benchmark import repo as bench_repo

        batch_id = self._make_batch(case_ids=["case-a"], seeds=3, concurrency=3)
        attempts: list[int] = []
        lock = threading.Lock()

        def fake_execute(row, judge_config_id=None, **_):
            with lock:
                attempts.append(row["id"])
            raise RuntimeError("case-a 系统性超时")

        with patch.object(runner, "_execute_one", fake_execute), \
             patch.object(runner, "_RETRY_BACKOFF_S", 0.0):
            runner._run_batch_sync(batch_id, concurrency=3)

        self.assertEqual(len(attempts), 9, "3 行各重试吃满 3 次，共 9 次尝试")
        statuses = self._row_statuses(batch_id)
        self.assertEqual(set(statuses.values()), {"failed"}, "各行耗尽重试后转 failed")
        self.assertNotIn("cancelled", statuses.values(), "单 case 失败不得停批/取消他行")

        batch = bench_repo.get_batch(batch_id)
        self.assertIsNone(batch["stop_reason"], "同 case 连败不触发 auto_fail")

    def test_serial_systematic_failure_halts_after_row_exhaustion(self):
        """串行（并发 1）系统性失败：首行重试耗尽 + 次行首败后止损（4 次尝试）。"""
        from app.benchmark import runner
        from app.benchmark import repo as bench_repo

        batch_id = self._make_batch(
            case_ids=["case-a", "case-b", "case-c"], seeds=1, concurrency=1
        )
        attempts: list[int] = []

        def fake_execute(row, judge_config_id=None, **_):
            attempts.append(row["id"])
            raise RuntimeError("executor 不可达")

        with patch.object(runner, "_execute_one", fake_execute), \
             patch.object(runner, "_RETRY_BACKOFF_S", 0.0):
            runner._run_batch_sync(batch_id, concurrency=1)

        # 行1 三次（重试耗尽，单行不触发）+ 行2 首败（涉及 2 行 → 触发）= 4 次
        self.assertEqual(len(attempts), 4, "串行止损点：首行耗尽 + 次行首败")
        batch = bench_repo.get_batch(batch_id)
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(batch["stop_reason"], "auto_fail")

    def test_intermittent_failure_does_not_trigger_failfast(self):
        """偶发失败序列：个别行失败一次后成功，计数清零不触发止损（AC-004）。"""
        from app.benchmark import runner
        from app.benchmark import repo as bench_repo

        batch_id = self._make_batch(
            case_ids=["case-a", "case-b", "case-c", "case-d"], seeds=1, concurrency=2
        )
        flaky = set()  # 偶发行：首跑失败一次，重试即成功

        def fake_execute(row, judge_config_id=None, **_):
            run_id = row["id"]
            if run_id not in flaky and row["case_id"] in ("case-a", "case-b"):
                flaky.add(run_id)
                raise RuntimeError("偶发网络抖动")
            self.db.execute(
                "UPDATE benchmark_runs SET status='done', scores_json='{}', finished_at=? WHERE id=?",
                ("2026-09-20T00:00:00Z", run_id),
            )

        with patch.object(runner, "_execute_one", fake_execute), \
             patch.object(runner, "_RETRY_BACKOFF_S", 0.0):
            runner._run_batch_sync(batch_id, concurrency=2)

        statuses = self._row_statuses(batch_id)
        self.assertNotIn("cancelled", statuses.values(), "偶发失败不得触发止损")
        self.assertEqual(set(statuses.values()), {"done"}, "全部行最终完成")

        batch = bench_repo.get_batch(batch_id)
        self.assertEqual(batch["status"], "done")
        self.assertIsNone(batch["stop_reason"])


class Poll4xxFastFailTest(StopFailFastTestBase):
    """AC-006：轮询 4xx 快速失败。"""

    def test_404_fails_fast_without_waiting_timeout(self):
        """executor 返回 404：秒级判败，不等 10 分钟超时。"""
        from app.benchmark import runner

        class FakeResp:
            status_code = 404

        with patch.object(runner, "_POLL_INTERVAL", 0.05), \
             patch.object(runner.httpx, "get", return_value=FakeResp()):
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError) as ctx:
                runner._poll_until_done("task-x", 1)
            elapsed = time.monotonic() - t0

        self.assertIn("404", str(ctx.exception))
        self.assertLess(elapsed, 15, "4xx 必须秒级快速失败")

    def test_503_keeps_retrying_then_succeeds(self):
        """executor 返回 503：维持退避重试，不立即失败。"""
        from app.benchmark import runner

        responses = [
            type("R", (), {"status_code": 503})(),
            type("R", (), {"status_code": 503})(),
            type("R", (), {
                "status_code": 200,
                "json": lambda self=None: {"status": "done", "trace_ids": ["trace-1"]},
            })(),
        ]

        with patch.object(runner, "_POLL_INTERVAL", 0.05), \
             patch.object(runner.httpx, "get", side_effect=responses):
            trace_id = runner._poll_until_done("task-y", 1)

        self.assertEqual(trace_id, "trace-1", "503 退避后应成功拿到 trace")

    def test_cancel_event_interrupts_polling(self):
        """轮询等待点感知批次取消，上抛携带 task_id（FR-001 worker 收尾依赖）。"""
        from app.benchmark import runner

        event = threading.Event()
        event.set()

        with patch.object(runner, "_POLL_INTERVAL", 0.05):
            with self.assertRaises(runner._BatchCancelled) as ctx:
                runner._poll_until_done("task-z", 1, cancel_event=event)

        self.assertEqual(ctx.exception.task_id, "task-z", "取消信号须携带 task_id 供 executor 叫停")

    def test_unknown_terminal_status_fails_fast(self):
        """evidence_capture_failed 等未知终态按失败上抛，不得当 running 死等。"""
        from app.benchmark import runner

        resp = type("R", (), {
            "status_code": 200,
            "json": lambda self=None: {
                "status": "evidence_capture_failed",
                "trace_ids": [],
                "error": "capture conflict",
            },
        })()

        with patch.object(runner, "_POLL_INTERVAL", 0.05), \
             patch.object(runner.httpx, "get", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                runner._poll_until_done("task-e", 1)

        self.assertIn("evidence_capture_failed", str(ctx.exception))


class PollFailureStopsExecutorTest(StopFailFastTestBase):
    """行失败叫停 executor：轮询失败尽力停 executor 任务，不白烧 API（FR-006）。"""

    def _execute_with_poll_error(self, poll_exc):
        from app.benchmark import runner

        stop_calls: list[str] = []
        with patch.object(runner.evalset, "load_case_demand", return_value="demand"), \
             patch.object(runner, "_get_snapshot", return_value={"commit": "c1"}), \
             patch.object(runner, "_trigger_executor", return_value="task-x"), \
             patch.object(runner, "_poll_until_done", side_effect=poll_exc), \
             patch.object(runner, "_stop_executor_task",
                          lambda tid: stop_calls.append(tid)):
            with self.assertRaises(type(poll_exc)):
                runner._execute_one(
                    {"id": 1, "case_id": "case-a", "harness_version": 1, "seed": 1}
                )
        return stop_calls

    def test_poll_failure_stops_executor_task(self):
        """轮询异常（4xx 快败/executor failed）：行失败前叫停 executor 生成任务。"""
        stop_calls = self._execute_with_poll_error(RuntimeError("executor task failed: boom"))
        self.assertEqual(stop_calls, ["task-x"], "失败路径必须尽力叫停 executor 任务")

    def test_batch_cancel_not_double_stopped(self):
        """批次取消（_BatchCancelled）：走 worker 收尾的叫停语义，不重复停。"""
        from app.benchmark import runner

        stop_calls = self._execute_with_poll_error(runner._BatchCancelled(task_id="task-x"))
        self.assertEqual(stop_calls, [], "取消路径由 worker 收尾叫停，轮询层不重复停")


if __name__ == "__main__":
    unittest.main()
