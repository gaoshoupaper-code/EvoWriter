"""发版终态切换 API 级单测（REQ-20261004-212948 / FR-001 / AC-001/002）。

隔离策略：临时 SQLite DB + TestClient；Platform 发版原语（probe/promote）、
candidate 冻结（git_ops）全部打桩——本文件只验证 publish 成功/失败路径上
进化点终态与版本记录的正确性，不真正调 Platform。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

from fastapi.testclient import TestClient

import app.core.db as db
import app.core.git_ops as git_ops
import app.versioning.release_gate as release_gate
import app.versioning.registry_repo as registry_repo
from app.core.settings import settings
from app.evolve import agents_repo
from app.evolve import db as ev_db
from app.evolve.docs import generate_design_doc_from_points
from app.evolve.evolve_repo import EvolvePointsRepo, EvolveReleasesRepo
from app.main import app
from app.versioning.release_gate import ReleasePromoteError

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


def _promote_ok(source_commit: str, version_note: str = ""):
    return SimpleNamespace(commit=source_commit, version=99, reload_notified=True)


class PublishTerminalStateTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_releases")
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        self.agent = agents_repo.create("PubAgent", "ws-pub")
        self.session_id = "sess-pub-1"
        ev_db.create_session(self.session_id, agent_id=self.agent["agent_id"])

        # 桩：candidate 无记录（新发版路径）、git 冻结、Platform 门禁/晋升。
        # 必须用 patch.object（测试结束自动还原）——直接赋值会污染后续测试文件
        for patcher in (
            mock.patch.object(registry_repo, "get_version_by_session", lambda sid: None),
            mock.patch.object(git_ops, "commit_candidate", lambda *a, **k: "commit-abc"),
            mock.patch.object(git_ops, "push_mirror", lambda: None),
            mock.patch.object(release_gate, "probe_candidate", lambda commit: None),
            mock.patch.object(release_gate, "promote_release", _promote_ok),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _land_two_points(self):
        """模拟一轮拍板：2 个 accepted 点 + design_doc 生成（回填 landed_session_id）。"""
        p1 = _propose(self.session_id, self.agent["agent_id"], "middleware/retry.py")
        p2 = _propose(self.session_id, self.agent["agent_id"], "prompts/meta_system.md")
        EvolvePointsRepo.accept(p1["id"], chosen_option=0)
        EvolvePointsRepo.accept(p2["id"], chosen_option=0)
        path = generate_design_doc_from_points(
            self.session_id, agent_id=self.agent["agent_id"],
        )
        self.assertIsNotNone(path)
        ev_db.update_session(self.session_id, status="pending_review")
        return [p1["id"], p2["id"]]

    def test_publish_success_ships_points_and_records_version(self):
        """AC-001：发布成功 → 本轮落地点全部切 shipped + 版本 0.1。"""
        ids = self._land_two_points()
        # 拍板后新采纳的点（未随本次落地）不得被切走
        p3 = _propose(self.session_id, self.agent["agent_id"], "middleware/ratelimit.py")
        EvolvePointsRepo.accept(p3["id"], chosen_option=1)

        resp = self.client.post(f"/api/evolve/sessions/{self.session_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body.get("release_version"), "0.1")

        for pid in ids:
            g = EvolvePointsRepo.get_by_id(pid)
            self.assertEqual((g["status"], g["version"]), ("shipped", "0.1"))
        g3 = EvolvePointsRepo.get_by_id(p3["id"])
        self.assertEqual((g3["status"], g3.get("version")), ("accepted", None))

        rel = EvolveReleasesRepo.get_by_session(self.session_id)
        self.assertIsNotNone(rel)
        self.assertEqual((rel["version"], rel["point_count"]), ("0.1", 2))
        self.assertEqual(rel["workspace_id"], "ws-pub")

    def test_publish_failure_keeps_accepted_then_retry_ships(self):
        """AC-002：promote 失败（502）→ 点保持 accepted 无版本；重试成功后正确封存。"""
        ids = self._land_two_points()
        with mock.patch.object(
            release_gate, "promote_release",
            side_effect=ReleasePromoteError("platform down"),
        ):
            resp = self.client.post(f"/api/evolve/sessions/{self.session_id}/publish")
        self.assertEqual(resp.status_code, 502)
        for pid in ids:
            g = EvolvePointsRepo.get_by_id(pid)
            self.assertEqual((g["status"], g.get("version")), ("accepted", None))
        self.assertIsNone(EvolveReleasesRepo.get_by_session(self.session_id))

        # 重试发版（Platform 恢复）——promote_release 用回 setUp 的成功桩
        resp = self.client.post(f"/api/evolve/sessions/{self.session_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("release_version"), "0.1")
        for pid in ids:
            g = EvolvePointsRepo.get_by_id(pid)
            self.assertEqual((g["status"], g["version"]), ("shipped", "0.1"))

    def test_publish_reentry_reuses_version_and_backfills(self):
        """AC-001 幂等：重入 publish（candidate 已 production）复用版本号并补切漏切。"""
        ids = self._land_two_points()
        self.client.post(f"/api/evolve/sessions/{self.session_id}/publish")

        # 模拟漏切现场：publish 在 ship 之后、session 状态推进之前中断
        # （session 停回 pending_review，registry 已 production，点未切终态）
        ev_db.update_session(self.session_id, status="pending_review")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE evolve_points SET status='accepted' WHERE id = ?", (ids[0],),
            )
        # 幂等分支：registry 报 production（局部替换，本测试内自动还原）
        with mock.patch.object(
            registry_repo, "get_version_by_session",
            lambda sid: {
                "status": "production", "version": 88, "commit_hash": "commit-abc",
            },
        ):
            resp = self.client.post(f"/api/evolve/sessions/{self.session_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("release_version"), "0.1")
        g = EvolvePointsRepo.get_by_id(ids[0])
        self.assertEqual((g["status"], g["version"]), ("shipped", "0.1"))
        # 版本序列未被重入推进：仍只有一条 release 记录
        self.assertEqual(len(EvolveReleasesRepo.list_recent()), 1)

    def test_publish_ships_legacy_pending_review_points(self):
        """review P2×2：升级前已 finalize 的点（无 landed_session_id）publish 正常封存。

        模拟存量窗口：旧代码拍板（design_ref 回填但无 landed_session_id）、
        会话停在 pending_review，升级后 publish——不补标会封 0 点 + 烧版本号。
        """
        p = _propose(self.session_id, self.agent["agent_id"], "middleware/retry.py")
        EvolvePointsRepo.accept(p["id"], chosen_option=0)
        # 旧链路痕迹：design_ref 回填、landed_session_id 为 NULL（不调 mark_landed）
        with db.transaction() as conn:
            conn.execute(
                "UPDATE evolve_points SET design_ref = 1 WHERE id = ?", (p["id"],),
            )
        ev_db.update_session(self.session_id, status="pending_review")

        resp = self.client.post(f"/api/evolve/sessions/{self.session_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("release_version"), "0.1")
        g = EvolvePointsRepo.get_by_id(p["id"])
        self.assertEqual((g["status"], g["version"]), ("shipped", "0.1"))
        rel = EvolveReleasesRepo.get_by_session(self.session_id)
        self.assertEqual((rel["version"], rel["point_count"]), ("0.1", 1))

    def test_second_workspace_bumps_major_version(self):
        """DEC-003：换作品发版整数 +1（0.1 → 1.0）。"""
        self._land_two_points()
        self.client.post(f"/api/evolve/sessions/{self.session_id}/publish")

        agent2 = agents_repo.create("PubAgent2", "ws-pub2")
        sid2 = "sess-pub-2"
        ev_db.create_session(sid2, agent_id=agent2["agent_id"])
        p = _propose(sid2, agent2["agent_id"], "middleware/cache.py")
        EvolvePointsRepo.accept(p["id"], chosen_option=0)
        generate_design_doc_from_points(sid2, agent_id=agent2["agent_id"])
        ev_db.update_session(sid2, status="pending_review")

        resp = self.client.post(f"/api/evolve/sessions/{sid2}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("release_version"), "1.0")
        g = EvolvePointsRepo.get_by_id(p["id"])
        self.assertEqual((g["status"], g["version"]), ("shipped", "1.0"))


if __name__ == "__main__":
    unittest.main()
