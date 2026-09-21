"""进化端冒烟单测（重构安全网，决策 D3 / 设计 S4）。

隔离策略（S4）：FastAPI TestClient + 临时 SQLite DB，只测校验逻辑——
不触及 LLM/executor。

覆盖（自由启动改造，REQ-20260921-124733 DEC-004）：
  - import 冒烟：evolve/tests 全部模块可正常 import（重构后路径正确性）
  - 旧入口 /evolve/start 已裁撤（404）
  - start-converse 请求体无必填字段（空 body 不 422）
  - evolve sessions 列表：空 DB 下返回空列表

设计依据：.claude/md/20260701_213000_进化端重构_设计.md
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 把 evolution/ 加入 sys.path（同 test_increment_reconstruct 模式）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── 在 import app 之前，把 DB 指向临时文件 ──────────────────────
# settings 是模块级单例，必须在 import app.core.settings 前注入环境变量。
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
# 测试不触发真实的 executor 轮询 / 活跃大盘轮询，禁用避免后台线程干扰
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

from fastapi.testclient import TestClient

import app.core.db as db
from app.core.settings import settings
from app.main import app

_old_db = settings.evolution_db


def setUpModule() -> None:
    """模块级初始化：重置 DB 连接 + 建表，确保用临时空库。"""
    settings.evolution_db = _tmp_db.name
    db._conn = None  # 重置单例连接，强制重连到临时库
    db.init_db()


def tearDownModule() -> None:
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    settings.evolution_db = _old_db
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass


class ImportSmokeTest(unittest.TestCase):
    """所有核心模块能正常 import（重构后导入路径正确性的守门员）。"""

    def test_import_evolve_modules(self) -> None:
        import app.evolve.api  # noqa: F401
        import app.evolve.ctx  # noqa: F401
        import app.evolve.db  # noqa: F401
        import app.evolve.docs  # noqa: F401
        import app.evolve.agent.agent  # noqa: F401
        import app.evolve.agent.middleware.flow_guard  # noqa: F401
        import app.evolve.agent.tools.flow  # noqa: F401
        import app.evolve.agent.tools.inspect  # noqa: F401

    def test_import_tests_modules(self) -> None:
        import app.tests.api  # noqa: F401
        import app.tests.repo  # noqa: F401


class EvolveStartContractTest(unittest.TestCase):
    """自由启动契约（DEC-004）：无必填业务输入，旧入口已裁撤。"""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_legacy_start_endpoint_removed(self) -> None:
        """旧 /evolve/start（评估卷宗强前置）随休眠系统裁撤 → 404。"""
        resp = self.client.post(
            "/api/evolve/start", json={"eval_dossier_id": "whatever"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_start_converse_accepts_empty_body(self) -> None:
        """无任何业务输入即可启动（不 422）；测试环境无 recorder → 503 守门。"""
        resp = self.client.post("/api/evolve/start-converse", json={})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("trace_recorder", resp.json()["detail"]["missing_fields"])


class EvolveSessionsQueryTest(unittest.TestCase):
    """evolve sessions 查询端点（空库基线）。"""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_list_sessions_empty(self) -> None:
        """空 DB 下 sessions 列表返回空。"""
        resp = self.client.get("/api/evolve/sessions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["sessions"], [])


if __name__ == "__main__":
    unittest.main()
