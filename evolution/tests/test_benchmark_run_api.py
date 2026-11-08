"""旧版启动请求退役响应，不能绕过接口创建旧批次。"""
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

    def test_trigger_run_is_retired_without_dispatch(self):
        from fastapi import HTTPException
        from unittest.mock import patch
        from app.benchmark import api as benchmark_api

        with patch.object(benchmark_api.runner, "trigger_run") as trigger:
            with self.assertRaises(HTTPException) as error:
                benchmark_api.trigger_run(benchmark_api.RunRequest(judge_group_id=1))
        self.assertEqual(error.exception.status_code, 410)
        self.assertIn("退役", error.exception.detail)
        trigger.assert_not_called()
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM benchmark_runs")["n"], 0)


if __name__ == "__main__":
    unittest.main()
