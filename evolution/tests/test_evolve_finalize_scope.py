"""拍板口径与终态只读单测（REQ-20261004-212948 / FR-002/FR-004 / AC-004/005）。

覆盖：
  - AC-004：已发版（shipped）点不进入下一轮拍板——design_doc 只覆盖未发版 accepted
  - AC-005：全部点已发版时拍板被 400 拒绝，错误信息说明无待落地点
  - 工具层终态只读：update/reject 已发版点被拒绝（FR-004 引导的兜底）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

from fastapi.testclient import TestClient

import app.core.db as db
from app.core.settings import settings
from app.evolve import agents_repo
from app.evolve import db as ev_db
from app.evolve.agent.tools.points import make_points_tools
from app.evolve.ctx import EvolveContext, set_tool_context
from app.evolve.docs import generate_design_doc_from_points
from app.evolve.evolve_repo import EvolvePointsRepo
from app.main import app

_old_db = settings.evolution_db


def setUpModule() -> None:
    settings.evolution_db = _tmp_db.name
    db._conn = None
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


def _propose(session_id, agent_id, target):
    return EvolvePointsRepo.propose(
        session_id, target=target, problem=f"{target} 的问题",
        options=[
            {"description": "方案甲", "pros": [], "cons": [], "expected_impact": ""},
            {"description": "方案乙", "pros": [], "cons": [], "expected_impact": ""},
        ],
        agent_id=agent_id,
    )


class FinalizeScopeTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_releases")
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        self.agent = agents_repo.create("ScopeAgent", "ws-scope")
        self.agent_id = self.agent["agent_id"]

    def test_shipped_points_excluded_from_next_finalize(self):
        """AC-004：上一版 shipped 的点不被下轮拍板覆盖，新点正常进入。"""
        # 第一轮：p1 随 v0.1 发版封存
        s1 = "sess-scope-1"
        ev_db.create_session(s1, agent_id=self.agent_id)
        p1 = _propose(s1, self.agent_id, "middleware/retry.py")
        EvolvePointsRepo.accept(p1["id"], chosen_option=0)
        generate_design_doc_from_points(s1, agent_id=self.agent_id)
        EvolvePointsRepo.ship_points(s1, version="0.1")

        # 第二轮拍板：新采纳 p2（p1 已 shipped）
        s2 = "sess-scope-2"
        ev_db.create_session(s2, agent_id=self.agent_id)
        p2 = _propose(s2, self.agent_id, "prompts/meta_system.md")
        EvolvePointsRepo.accept(p2["id"], chosen_option=0)
        path = generate_design_doc_from_points(s2, agent_id=self.agent_id)
        self.assertIsNotNone(path)

        g1 = EvolvePointsRepo.get_by_id(p1["id"])
        g2 = EvolvePointsRepo.get_by_id(p2["id"])
        # p1 仍是 v0.1 终态，未被第二轮覆盖
        self.assertEqual((g1["status"], g1["version"], g1["landed_session_id"]),
                         ("shipped", "0.1", s1))
        # p2 被第二轮拍板覆盖
        self.assertEqual((g2["status"], g2["landed_session_id"], g2["design_ref"]),
                         ("accepted", s2, 1))

        # 拍板计数只数未发版 accepted（AC-005 前置）
        from app.evolve.evolve_repo import EvolvePointsRepo as repo
        self.assertEqual(repo.count_accepted_by_agent(self.agent_id), 1)

    def test_finalize_rejected_when_all_shipped(self):
        """AC-005：只有已发版点时拍板 400，错误信息说明无待落地点。"""
        s1 = "sess-scope-all"
        ev_db.create_session(s1, agent_id=self.agent_id)
        p = _propose(s1, self.agent_id, "middleware/retry.py")
        EvolvePointsRepo.accept(p["id"], chosen_option=0)
        generate_design_doc_from_points(s1, agent_id=self.agent_id)
        EvolvePointsRepo.ship_points(s1, version="0.1")
        ev_db.update_session(s1, status="conversing")

        resp = self.client.post(f"/api/evolve/sessions/{s1}/finalize")
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("待落地", resp.json()["detail"])

    def test_tools_reject_operating_shipped_point(self):
        """FR-004 兜底：update/reject 已发版点被拒绝，终态只读。"""
        s1 = "sess-scope-ro"
        ev_db.create_session(s1, agent_id=self.agent_id)
        p = _propose(s1, self.agent_id, "middleware/retry.py")
        EvolvePointsRepo.accept(p["id"], chosen_option=0)
        generate_design_doc_from_points(s1, agent_id=self.agent_id)
        EvolvePointsRepo.ship_points(s1, version="0.2")

        ctx = EvolveContext(session_id=s1)
        ctx.agent_id = self.agent_id
        set_tool_context(ctx)
        propose_tool, update_tool, reject_tool, _list = make_points_tools()

        out = update_tool.invoke({"point_id": p["id"], "chosen_option": 0})
        self.assertIn("已发版", out)
        self.assertIn("0.2", out)
        out = reject_tool.invoke({"point_id": p["id"], "reason": "不想要了"})
        self.assertIn("已发版", out)
        # 终态未被改动
        g = EvolvePointsRepo.get_by_id(p["id"])
        self.assertEqual((g["status"], g["version"]), ("shipped", "0.2"))


if __name__ == "__main__":
    unittest.main()
