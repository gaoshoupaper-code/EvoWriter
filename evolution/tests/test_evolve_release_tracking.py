"""发版终态与版本闭环单测（REQ-20261004-212948 / DEC-001/003 / AC-001/009 库层）。

覆盖：
  - 版本号序列推进：首版 0.1；同作品小数 +1；换作品（含回访）整数 +1（AC-009）
  - mark_landed 拍板回填 + ship_points 发版切终态：只切本次落地会话的点（AC-001 库层）
  - 拍板后新采纳的点（未回填 landed_session_id）不被 publish 切走
  - ship_points 幂等可重放；release 记录按 session 唯一
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
from app.evolve.evolve_repo import EvolvePointsRepo, EvolveReleasesRepo

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


def _record_release(session_id, workspace_id, agent_id="ag-1"):
    version = EvolveReleasesRepo.next_version(workspace_id)
    EvolveReleasesRepo.record_release(
        session_id, agent_id=agent_id, workspace_id=workspace_id, version=version,
    )
    return version


class VersionSequenceTest(unittest.TestCase):
    """AC-009：作品语义版本序列推进。"""

    def setUp(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_releases")
            conn.execute("DELETE FROM evolve_points")


    def test_sequence_same_switch_back_and_forth(self):
        # 作品 A：首版 0.1，连续 0.2 / 0.3
        self.assertEqual(_record_release("s-a1", "ws-a"), "0.1")
        self.assertEqual(_record_release("s-a2", "ws-a"), "0.2")
        self.assertEqual(_record_release("s-a3", "ws-a"), "0.3")
        # 换作品 B：整数 +1 → 1.0，连续 1.1
        self.assertEqual(_record_release("s-b1", "ws-b", agent_id="ag-2"), "1.0")
        self.assertEqual(_record_release("s-b2", "ws-b", agent_id="ag-2"), "1.1")
        # 回访作品 A：视同换作品 → 2.0
        self.assertEqual(_record_release("s-a4", "ws-a"), "2.0")

    def test_record_release_idempotent_by_session(self):
        _record_release("s-idem", "ws-idem")
        rel = EvolveReleasesRepo.get_by_session("s-idem")
        self.assertIsNotNone(rel)
        self.assertEqual(rel["version"], "0.1")
        # publish 幂等分支语义：已有记录时复用版本号、不再重插（先查再插），
        # 版本序列不因重复发版被推进
        self.assertEqual(
            EvolveReleasesRepo.next_version("ws-idem"), "0.2"
        )


class ShipPointsTest(unittest.TestCase):
    """AC-001 库层：发版切终态只覆盖本次落地会话回填过的点。"""

    def setUp(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_releases")
            conn.execute("DELETE FROM evolve_points")


    def test_ship_only_landed_points_of_session(self):
        p1 = _propose("s-ship-1", "ag-ship", "middleware/retry.py")
        p2 = _propose("s-ship-2", "ag-ship", "prompts/meta_system.md")
        EvolvePointsRepo.accept(p1["id"], chosen_option=0)
        EvolvePointsRepo.accept(p2["id"], chosen_option=0)

        # 拍板（session s-ship-1）：design_doc 覆盖当时全部 accepted（p1、p2 都进）
        EvolvePointsRepo.mark_landed([p1["id"], p2["id"]], "s-ship-1")
        # 拍板后新采纳的点（未回填）不应被 publish 切走
        p3 = _propose("s-ship-2", "ag-ship", "middleware/ratelimit.py")
        EvolvePointsRepo.accept(p3["id"], chosen_option=1)

        shipped = EvolvePointsRepo.ship_points("s-ship-1", version="0.7")
        self.assertEqual(shipped, 2)

        g1 = EvolvePointsRepo.get_by_id(p1["id"])
        g2 = EvolvePointsRepo.get_by_id(p2["id"])
        g3 = EvolvePointsRepo.get_by_id(p3["id"])
        self.assertEqual((g1["status"], g1["version"]), ("shipped", "0.7"))
        self.assertEqual((g2["status"], g2["version"]), ("shipped", "0.7"))
        self.assertEqual((g3["status"], g3.get("version")), ("accepted", None))

        # 幂等重放：再 ship 一次无新增变更（p3 永不属于 s-ship-1）
        self.assertEqual(EvolvePointsRepo.ship_points("s-ship-1", version="0.7"), 0)

    def test_accept_after_landed_overwritten_on_next_finalize(self):
        """丢弃语义（DEC-006）：重拍板时 landed_session_id 被覆盖为新会话。"""
        p = _propose("s-reland-1", "ag-reland", "middleware/retry.py")
        EvolvePointsRepo.accept(p["id"], chosen_option=0)
        EvolvePointsRepo.mark_landed([p["id"]], "s-reland-1")
        # 该会话被丢弃，下次拍板重新回填
        EvolvePointsRepo.mark_landed([p["id"]], "s-reland-2")
        EvolvePointsRepo.ship_points("s-reland-2", version="0.1")
        g = EvolvePointsRepo.get_by_id(p["id"])
        self.assertEqual((g["status"], g["landed_session_id"]), ("shipped", "s-reland-2"))


if __name__ == "__main__":
    unittest.main()
