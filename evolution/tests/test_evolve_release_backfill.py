"""存量发版历史回填单测（REQ-20261004-212948 / FR-006 / AC-010）。

覆盖：
  - 按 published 会话时间序重放：老点切 shipped + 版本号（同作品 0.1 → 0.2）
  - 换作品的 published 会话整数 +1（1.0）
  - 发布后采纳的点保持 accepted（不误切）
  - discarded 会话的点随下一次 published 归版（DEC-006 丢弃语义）
  - accepted_at 缺失的老点保持 accepted（人工核对清单语义）
  - 幂等：重跑无任何变更（版本计数、点状态均不变）
"""
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
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


def _iso(minute: int) -> str:
    return (datetime(2026, 10, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minute)).isoformat()


def _propose_old(session_id, agent_id, target, accepted_at=None):
    """直插 propose + 可选 accepted_at（模拟存量数据的任意时间线）。"""
    p = EvolvePointsRepo.propose(
        session_id, target=target, problem=f"{target} 的问题",
        options=[
            {"description": "甲", "pros": [], "cons": [], "expected_impact": ""},
            {"description": "乙", "pros": [], "cons": [], "expected_impact": ""},
        ],
        agent_id=agent_id,
    )
    if accepted_at is not None:
        EvolvePointsRepo.accept(p["id"], chosen_option=0)
        with db.transaction() as conn:
            conn.execute(
                "UPDATE evolve_points SET accepted_at = ? WHERE id = ?",
                (accepted_at, p["id"]),
            )
        # 存量落地痕迹：design_ref 非空
        with db.transaction() as conn:
            conn.execute(
                "UPDATE evolve_points SET design_ref = 1 WHERE id = ?", (p["id"],),
            )
    return p


def _force_published(session_id, updated_at):
    with db.transaction() as conn:
        conn.execute(
            "UPDATE evolve_sessions SET status='published', updated_at=? WHERE session_id=?",
            (updated_at, session_id),
        )


class ReleaseBackfillTest(unittest.TestCase):
    def setUp(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_releases")
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        self.agent = agents_repo.create("BackfillAgent", "ws-bf")

    def _run_backfill(self):
        conn = db.get_conn()
        db._backfill_evolve_release_history(conn)

    def test_backfill_replays_versions_and_ships_old_points(self):
        """AC-010：published 时间窗内的老点归对应版本；窗外不动。"""
        aid = self.agent["agent_id"]
        # 第一版（12:10 发布）：p1(12:01 采纳)、p2(12:05 采纳)
        s1 = "sess-bf-1"
        ev_db.create_session(s1, agent_id=aid)
        p1 = _propose_old(s1, aid, "middleware/retry.py", _iso(1))
        p2 = _propose_old(s1, aid, "prompts/meta_system.md", _iso(5))
        _force_published(s1, _iso(10))
        # 第二版（12:20 发布）：p3(12:12 采纳，含被丢弃会话 s-disc 的语义)
        s2 = "sess-bf-2"
        ev_db.create_session(s2, agent_id=aid)
        p3 = _propose_old(s2, aid, "middleware/cache.py", _iso(12))
        _force_published(s2, _iso(20))
        # 发布后新采纳（12:30）：不切
        p4 = _propose_old(s1, aid, "middleware/ratelimit.py", _iso(30))

        self._run_backfill()

        g1, g2, g3, g4 = (EvolvePointsRepo.get_by_id(p["id"]) for p in (p1, p2, p3, p4))
        self.assertEqual((g1["status"], g1["version"]), ("shipped", "0.1"))
        self.assertEqual((g2["status"], g2["version"]), ("shipped", "0.1"))
        self.assertEqual((g3["status"], g3["version"]), ("shipped", "0.2"))
        self.assertEqual((g4["status"], g4.get("version")), ("accepted", None))
        self.assertEqual(len(EvolveReleasesRepo.list_recent()), 2)

    def test_backfill_switches_workspace_bumps_major(self):
        """回放中换作品（另一 Agent 的 published 在后）→ 整数 +1。"""
        aid = self.agent["agent_id"]
        s1 = "sess-bf-a"
        ev_db.create_session(s1, agent_id=aid)
        _propose_old(s1, aid, "middleware/retry.py", _iso(1))
        _force_published(s1, _iso(10))

        agent2 = agents_repo.create("BackfillAgent2", "ws-bf2")
        s2 = "sess-bf-b"
        ev_db.create_session(s2, agent_id=agent2["agent_id"])
        p = _propose_old(s2, agent2["agent_id"], "middleware/cache.py", _iso(12))
        _force_published(s2, _iso(20))

        self._run_backfill()
        g = EvolvePointsRepo.get_by_id(p["id"])
        self.assertEqual((g["status"], g["version"]), ("shipped", "1.0"))

    def test_backfill_missing_accepted_at_kept_with_warning(self):
        """accepted_at 缺失的老点保持 accepted（人工核对语义）。"""
        aid = self.agent["agent_id"]
        s1 = "sess-bf-na"
        ev_db.create_session(s1, agent_id=aid)
        p = _propose_old(s1, aid, "middleware/retry.py", accepted_at=_iso(1))
        # 有 accept 痕迹但 accepted_at 置空（极老数据）
        with db.transaction() as conn:
            conn.execute(
                "UPDATE evolve_points SET accepted_at = NULL WHERE id = ?", (p["id"],),
            )
        _force_published(s1, _iso(10))

        self._run_backfill()
        g = EvolvePointsRepo.get_by_id(p["id"])
        self.assertEqual(g["status"], "accepted")

    def test_backfill_idempotent(self):
        """AC-010：重跑无变更（版本计数、点状态、release 条数均稳定）。"""
        aid = self.agent["agent_id"]
        s1 = "sess-bf-idem"
        ev_db.create_session(s1, agent_id=aid)
        _propose_old(s1, aid, "middleware/retry.py", _iso(1))
        _force_published(s1, _iso(10))

        self._run_backfill()
        self._run_backfill()
        self._run_backfill()

        self.assertEqual(len(EvolveReleasesRepo.list_recent()), 1)
        # 版本序列未被重放推进：下一次真实发版应是 0.2
        self.assertEqual(EvolveReleasesRepo.next_version("ws-bf"), "0.2")

    def test_backfill_skips_sessions_with_existing_release(self):
        """新链路已记 release 的发布不被回填重放（幂等键= session 唯一）。"""
        aid = self.agent["agent_id"]
        s1 = "sess-bf-new"
        ev_db.create_session(s1, agent_id=aid)
        p = _propose_old(s1, aid, "middleware/retry.py", _iso(1))
        EvolvePointsRepo.accept(p["id"], chosen_option=0)
        # 模拟新链路：publish 路径已封存 + 记录
        EvolvePointsRepo.mark_landed([p["id"]], s1)
        EvolvePointsRepo.ship_points(s1, version="0.5")
        EvolveReleasesRepo.record_release(
            s1, agent_id=aid, workspace_id="ws-bf", version="0.5", point_count=1,
        )
        _force_published(s1, _iso(10))

        self._run_backfill()
        # 未被重放覆盖（版本仍 0.5，release 仍 1 条）
        g = EvolvePointsRepo.get_by_id(p["id"])
        self.assertEqual((g["status"], g["version"]), ("shipped", "0.5"))
        self.assertEqual(len(EvolveReleasesRepo.list_recent()), 1)


if __name__ == "__main__":
    unittest.main()
