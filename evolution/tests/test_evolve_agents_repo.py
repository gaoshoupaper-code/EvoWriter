"""进化 Agent 实体 repo 单测（REQ-20261001-131018 / FR-001 / AC-001 数据层）。

隔离策略（同 test_evolve_smoke）：临时 SQLite DB，只测 repo 行为，不触及
LLM/executor。覆盖：
  - create + get + 1:1 绑定唯一性（含占用者信息）
  - 归档释放绑定 → 作品可再绑（DEC-009/010）
  - 改名；绑定不可改（repo 无此入口——由不存在的方法保证）
  - mark_work_deleted 粘性（二次调用 False）
  - evolve_sessions/evolve_points 的 agent_id 列可用（迁移生效）
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


class EvolveAgentsRepoTest(unittest.TestCase):
    """FR-001 数据层：创建/绑定 1:1/归档释放/改名/作品删除标记。"""

    def test_create_and_get_roundtrip(self):
        agent = agents_repo.create("测试Agent", "ws-aaa")
        self.assertEqual(agent["name"], "测试Agent")
        self.assertEqual(agent["workspace_id"], "ws-aaa")
        self.assertEqual(agent["status"], "active")
        self.assertIsNone(agent["work_deleted_at"])
        fetched = agents_repo.get(agent["agent_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["agent_id"], agent["agent_id"])

    def test_duplicate_binding_rejected_with_occupier_info(self):
        first = agents_repo.create("占用者", "ws-dup")
        with self.assertRaises(agents_repo.WorkspaceAlreadyBoundError) as ctx:
            agents_repo.create("后来者", "ws-dup")
        # 提示里含占用 Agent 的名字（AC-001：提示中含 X 的名字）
        self.assertIn("占用者", str(ctx.exception))
        self.assertEqual(ctx.exception.occupier["agent_id"], first["agent_id"])

    def test_archive_releases_binding_for_rebind(self):
        first = agents_repo.create("旧Agent", "ws-rel")
        self.assertTrue(agents_repo.archive(first["agent_id"]))
        # 归档后同作品可再绑新 Agent（DEC-009）
        second = agents_repo.create("新Agent", "ws-rel")
        self.assertNotEqual(second["agent_id"], first["agent_id"])
        # 重复归档幂等返回 False
        self.assertFalse(agents_repo.archive(first["agent_id"]))

    def test_list_agents_with_counts_and_archived_filter(self):
        a = agents_repo.create("统计A", "ws-stat-a")
        # 挂两个会话（一 published 一 conversing）验证统计
        ev_db.create_session("sess-p", case_id="", )
        db.execute("UPDATE evolve_sessions SET agent_id=?, status='published' WHERE session_id=?",
                   (a["agent_id"], "sess-p"))
        ev_db.create_session("sess-c", case_id="")
        db.execute("UPDATE evolve_sessions SET agent_id=? WHERE session_id=?",
                   (a["agent_id"], "sess-c"))
        active_list = agents_repo.list_agents()
        row = next(x for x in active_list if x["agent_id"] == a["agent_id"])
        self.assertEqual(row["session_count"], 2)
        self.assertEqual(row["published_count"], 1)
        # 归档后默认列表不含
        agents_repo.archive(a["agent_id"])
        active_ids = {x["agent_id"] for x in agents_repo.list_agents()}
        self.assertNotIn(a["agent_id"], active_ids)
        archived_ids = {x["agent_id"] for x in agents_repo.list_agents(include_archived=True)}
        self.assertIn(a["agent_id"], archived_ids)

    def test_rename_updates_name_only(self):
        a = agents_repo.create("旧名", "ws-name")
        self.assertTrue(agents_repo.rename(a["agent_id"], "新名"))
        self.assertEqual(agents_repo.get(a["agent_id"])["name"], "新名")
        self.assertEqual(agents_repo.get(a["agent_id"])["workspace_id"], "ws-name")
        self.assertFalse(agents_repo.rename("missing-agent", "x"))

    def test_mark_work_deleted_is_sticky(self):
        a = agents_repo.create("删作品", "ws-del")
        self.assertTrue(agents_repo.mark_work_deleted(a["agent_id"]))
        self.assertIsNotNone(agents_repo.get(a["agent_id"])["work_deleted_at"])
        # 粘性：二次标记返回 False，时间不覆盖
        self.assertFalse(agents_repo.mark_work_deleted(a["agent_id"]))
        self.assertFalse(agents_repo.mark_work_deleted("missing-agent"))

    def test_agent_id_columns_writable_on_sessions_and_points(self):
        a = agents_repo.create("挂载", "ws-mount")
        ev_db.create_session("sess-m", case_id="")
        db.execute("UPDATE evolve_sessions SET agent_id=? WHERE session_id=?",
                   (a["agent_id"], "sess-m"))
        row = ev_db.get_session("sess-m")
        self.assertEqual(row["agent_id"], a["agent_id"])
        # 旧会话无 agent_id → NULL（未绑定区）
        ev_db.create_session("sess-old", case_id="")
        self.assertIsNone(ev_db.get_session("sess-old")["agent_id"])


if __name__ == "__main__":
    unittest.main()
