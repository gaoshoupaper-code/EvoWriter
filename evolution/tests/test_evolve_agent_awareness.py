"""Agent 感知已发版点单测（REQ-20261004-212948 / FR-004/FR-005 / AC-007/008）。

覆盖：
  - AC-007：list_evolution_points 与开场注入（_format_work_binding）对已发版点
    标注终态 + 版本号，并附「勿重复 propose」引导
  - AC-008：propose 命中已发版同目标 → 点照常创建 + 返回附提醒；
    未命中目标 → 无提醒
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

import app.core.db as db
from app.core.settings import settings
from app.evolve import agents_repo
from app.evolve import db as ev_db
from app.evolve.agent.agent import _format_work_binding
from app.evolve.agent.tools.points import make_points_tools
from app.evolve.ctx import EvolveContext, set_tool_context
from app.evolve.docs import generate_design_doc_from_points
from app.evolve.evolve_repo import EvolvePointsRepo
from app.evolve import agents_repo as _ar

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


def _propose_repo(session_id, agent_id, target):
    return EvolvePointsRepo.propose(
        session_id, target=target, problem=f"{target} 的问题",
        options=[
            {"description": "方案甲", "pros": [], "cons": [], "expected_impact": ""},
            {"description": "方案乙", "pros": [], "cons": [], "expected_impact": ""},
        ],
        agent_id=agent_id,
    )


def _ship_one(session_id, agent_id, target, version):
    p = _propose_repo(session_id, agent_id, target)
    EvolvePointsRepo.accept(p["id"], chosen_option=0)
    generate_design_doc_from_points(session_id, agent_id=agent_id)
    EvolvePointsRepo.ship_points(session_id, version=version)
    return p


class AgentAwarenessTest(unittest.TestCase):
    def setUp(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_releases")
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        self.agent = agents_repo.create("AwareAgent", "ws-aware")
        self.agent_id = self.agent["agent_id"]
        self.session_id = "sess-aware-1"
        ev_db.create_session(self.session_id, agent_id=self.agent_id)
        self.ctx = EvolveContext(session_id=self.session_id)
        self.ctx.agent_id = self.agent_id
        set_tool_context(self.ctx)

    def test_list_tool_annotates_shipped_with_version(self):
        """AC-007：list 工具返回含 shipped 状态 + 版本号 + 勿重复引导。"""
        _ship_one(self.session_id, self.agent_id, "middleware/retry.py", "0.3")
        _propose_repo(self.session_id, self.agent_id, "prompts/meta_system.md")

        _propose_tool, _update, _reject, list_tool = make_points_tools()
        out = list_tool.invoke({})
        self.assertIn("shipped", out)
        self.assertIn("v0.3", out)
        self.assertIn("middleware/retry.py", out)
        self.assertIn("勿重复 propose", out)

    def test_work_binding_injection_annotates_shipped(self):
        """AC-007：开场注入的既有进化点清单含版本号与引导。"""
        _ship_one(self.session_id, self.agent_id, "middleware/retry.py", "0.4")
        binding = _format_work_binding(self.ctx)
        self.assertIn("shipped", binding)
        self.assertIn("v0.4", binding)
        self.assertIn("勿重复 propose", binding)

    def test_propose_same_target_gets_reminder(self):
        """AC-008：propose 已发版同目标 → 创建成功 + 返回附提醒。"""
        _ship_one(self.session_id, self.agent_id, "middleware/retry.py", "0.5")
        propose_tool, _u, _r, _l = make_points_tools()
        out = propose_tool.invoke({
            "target": "middleware/retry.py",
            "problem": "trace-x 显示重试风暴",
            "options": [
                {"description": "甲", "pros": [], "cons": [], "expected_impact": ""},
                {"description": "乙", "pros": [], "cons": [], "expected_impact": ""},
            ],
        })
        self.assertIn("已提出进化点", out)          # 创建成功
        self.assertIn("v0.5", out)                  # 提醒带版本
        self.assertIn("回归", out)                  # 引导确认新问题/回归
        # 新点确实创建为 proposed
        pts = EvolvePointsRepo.list_by_agent(self.agent_id)
        self.assertEqual(len([p for p in pts if p["target"] == "middleware/retry.py"]), 2)
        self.assertEqual(pts[-1]["status"], "proposed")

    def test_propose_new_target_no_reminder(self):
        """AC-008：未命中已发版目标 → 无提醒（不误报）。"""
        _ship_one(self.session_id, self.agent_id, "middleware/retry.py", "0.6")
        propose_tool, _u, _r, _l = make_points_tools()
        out = propose_tool.invoke({
            "target": "middleware/cache.py",
            "problem": "缓存未命中风暴",
            "options": [
                {"description": "甲", "pros": [], "cons": [], "expected_impact": ""},
                {"description": "乙", "pros": [], "cons": [], "expected_impact": ""},
            ],
        })
        self.assertIn("已提出进化点", out)
        self.assertNotIn("发版改过", out)


if __name__ == "__main__":
    unittest.main()
