"""POST /api/benchmark/run 响应契约测试（review rev1 finding 2 回归锚点）。

trigger_run 曾因 return 块误位移进 list_versions 成不可达死代码，
端点恒 500（批次实际已创建）。本测试钉住成功响应体形状：
{batch_id, status, progress, golden_revision}。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TriggerRunResponseTest(unittest.TestCase):
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
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_trigger_run_returns_batch_summary(self):
        """成功触发必须返回批次摘要 dict（None 返回会被 FastAPI 判 500）。"""
        from unittest.mock import patch

        from app.benchmark import api as benchmark_api, repo

        batch_id = repo.create_batch(
            case_ids=["case-001"], versions=[7],
            golden_revision="rev-x", seeds=1,
            rubric_version="v3", judge_fp="jfp",
        )
        req = benchmark_api.RunRequest(
            versions=[7], case_ids=["case-001"], seeds=1, concurrency=1,
        )
        # 只 patch 触发入口（避免真实线程池），批次读取走真库
        with patch.object(benchmark_api.runner, "trigger_run", return_value=batch_id):
            resp = benchmark_api.trigger_run(req)

        self.assertIsInstance(resp, dict)
        self.assertEqual(resp["batch_id"], batch_id)
        self.assertEqual(resp["golden_revision"], "rev-x")
        self.assertIn(resp["status"], {"running", "done", "partial", "failed", "cancelled"})
        for key in ("total", "done", "failed"):
            self.assertIn(key, resp["progress"])


if __name__ == "__main__":
    unittest.main()
