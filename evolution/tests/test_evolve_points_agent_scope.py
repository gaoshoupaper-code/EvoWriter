"""进化点 Agent 归属单测（REQ-20261001-131018 / FR-008 / AC-008）。

覆盖：
  - 会话 1 propose 的点（带 agent_id），会话 2 的浮窗端点可见
  - 会话 2 可更新该点状态（update 工具的归属校验按 Agent）
  - finalize 的 accepted 计数按 Agent（跨会话合计）
  - 不同 Agent 的点互相不可操作（隔离）
  - 旧链路（无 agent_id）按 session 匹配不变
"""
import os
import sys
import tempfile
import unittest
import unittest.mock
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
from app.evolve.agent.tools.points import _point_owned_by_ctx
from app.evolve.ctx import EvolveContext
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


class PointsAgentScopingTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        self.agent = agents_repo.create("点归属", "ws-pts")
        self.other_agent = agents_repo.create("别家", "ws-pts-other")
        ev_db.create_session("p-s1", case_id="", agent_id=self.agent["agent_id"])
        ev_db.create_session("p-s2", case_id="", agent_id=self.agent["agent_id"])
        ev_db.create_session("p-s3", case_id="", agent_id=self.other_agent["agent_id"])

    def test_cross_session_visibility_and_update(self):
        # 会话 1 提出的点
        p1 = _propose("p-s1", self.agent["agent_id"], "prompts/meta.md")
        # 会话 2 的浮窗端点可见（AC-008）
        r = self.client.get("/api/evolve/sessions/p-s2/points")
        self.assertEqual(r.status_code, 200)
        ids = [p["id"] for p in r.json()["points"]]
        self.assertIn(p1["id"], ids)
        # 会话 2 可继续更新状态
        EvolvePointsRepo.accept(p1["id"], chosen_option=0)
        updated = EvolvePointsRepo.get_by_id(p1["id"])
        self.assertEqual(updated["status"], "accepted")

    def test_agent_isolation_on_ownership(self):
        p_mine = _propose("p-s1", self.agent["agent_id"], "middleware/x.py")
        p_other = _propose("p-s3", self.other_agent["agent_id"], "middleware/y.py")
        ctx_mine = EvolveContext("p-s2")
        ctx_mine.agent_id = self.agent["agent_id"]
        ctx_other = EvolveContext("p-s3")
        ctx_other.agent_id = self.other_agent["agent_id"]
        self.assertTrue(_point_owned_by_ctx(p_mine, ctx_mine))
        self.assertFalse(_point_owned_by_ctx(p_other, ctx_mine))   # 别家的点不可操作
        self.assertTrue(_point_owned_by_ctx(p_other, ctx_other))

    def test_finalize_counts_by_agent_across_sessions(self):
        # Agent 名下：会话 1 一个 accepted，会话 2 一个 accepted → 合计 2
        pa = _propose("p-s1", self.agent["agent_id"], "t/a.md")
        pb = _propose("p-s2", self.agent["agent_id"], "t/b.md")
        _propose("p-s3", self.other_agent["agent_id"], "t/c.md")  # 别家不算
        EvolvePointsRepo.accept(pa["id"], chosen_option=0)
        EvolvePointsRepo.accept(pb["id"], chosen_option=1)
        self.assertEqual(
            EvolvePointsRepo.count_accepted_by_agent(self.agent["agent_id"]), 2)
        # design_doc 生成覆盖 Agent 级 accepted（两个 change）
        ev_db.update_session("p-s2", status="conversing")
        import app.evolve.docs as docs_mod
        with unittest.mock.patch.object(docs_mod, "write_design_doc") as wdoc:
            wdoc.return_value = "/tmp/design.md"
            path = generate_design_doc_from_points(
                "p-s2", agent_id=self.agent["agent_id"])
        self.assertIsNotNone(path)
        # write_design_doc 收到的 changes 应含 2 条（a+b），别家的 c 不在
        changes = wdoc.call_args[1].get("changes") or wdoc.call_args[0][1]
        self.assertEqual(len(changes), 2)
        targets = {c["target"] for c in changes}
        self.assertEqual(targets, {"t/a.md", "t/b.md"})

    def test_list_by_agent_excludes_legacy_null_points(self):
        # 旧链路点（agent_id NULL）不属于任何 Agent
        legacy = EvolvePointsRepo.propose(
            "legacy-s", target="old.md", problem="旧问题",
            options=[{"description": "x"}], agent_id=None)
        mine = _propose("p-s1", self.agent["agent_id"], "new.md")
        listed = EvolvePointsRepo.list_by_agent(self.agent["agent_id"])
        ids = [p["id"] for p in listed]
        self.assertIn(mine["id"], ids)
        self.assertNotIn(legacy["id"], ids)

    def test_legacy_session_scoped_ownership_unchanged(self):
        p = EvolvePointsRepo.propose(
            "legacy-s", target="old2.md", problem="旧",
            options=[{"description": "x"}], agent_id=None)
        ctx_same = EvolveContext("legacy-s")
        ctx_diff = EvolveContext("other-s")
        self.assertTrue(_point_owned_by_ctx(p, ctx_same))
        self.assertFalse(_point_owned_by_ctx(p, ctx_diff))


if __name__ == "__main__":
    unittest.main()
