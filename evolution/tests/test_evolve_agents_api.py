"""进化 Agent CRUD API 单测（REQ-20261001-131018 / FR-001 / FR-009 / AC-001）。

隔离策略（同 test_evolve_smoke）：临时 SQLite DB + FastAPI TestClient。
executor 以 monkeypatch 桩掉 executor_client 的三个取数函数——本文件只测
evolution 侧行为（校验/标记/归档），executor 真实行为由 executor 侧
test_internal_workspaces.py 覆盖。

覆盖：
  - 创建成功 + 1:1 重复绑定 409（提示含占用 Agent 名字，AC-001）
  - 作品不存在 400；executor 不可达 502（明确报错，不静默）
  - 详情：作品 404 → 粘性标记 work_deleted；不可达 → 不误标（AC-009）
  - 改名；归档释放绑定（再绑成功）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

from fastapi.testclient import TestClient

import app.core.db as db
import app.evolve.agents_api as agents_api
from app.core.settings import settings
from app.evolve import executor_client
from app.main import app

_old_db = settings.evolution_db

WORK = {
    "workspace_id": "ws-live",
    "title": "测试作品",
    "domain": "writing",
    "owner_user_id": "u1",
    "owner_username": "alice",
    "session_count": 3,
}


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


def _ok_fetch_workspace(workspace_id: str) -> dict:
    if workspace_id == WORK["workspace_id"]:
        return dict(WORK)
    raise executor_client.WorkNotFoundError(workspace_id)


class EvolveAgentsApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # 清表：每个用例独立
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_agents")
            conn.execute("DELETE FROM evolve_sessions")

    def test_create_agent_happy_path(self):
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            r = self.client.post("/api/evolve/agents", json={
                "name": "作品进化师", "workspace_id": WORK["workspace_id"],
            })
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["name"], "作品进化师")
        self.assertEqual(body["workspace_id"], WORK["workspace_id"])
        self.assertEqual(body["status"], "active")

    def test_create_duplicate_binding_409_with_occupier_name(self):
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            r1 = self.client.post("/api/evolve/agents", json={
                "name": "第一任", "workspace_id": WORK["workspace_id"]})
            self.assertEqual(r1.status_code, 201)
            r2 = self.client.post("/api/evolve/agents", json={
                "name": "第二任", "workspace_id": WORK["workspace_id"]})
        self.assertEqual(r2.status_code, 409)
        detail = r2.json()["detail"]
        self.assertIn("第一任", detail["message"])
        self.assertEqual(detail["bound_agent_name"], "第一任")

    def test_create_missing_work_400(self):
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            r = self.client.post("/api/evolve/agents", json={
                "name": "绑空气", "workspace_id": "ws-gone"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不存在", r.json()["detail"])

    def test_create_executor_unreachable_502_not_silent(self):
        def _boom(workspace_id):
            raise executor_client.ExecutorUnavailableError("conn refused")
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_boom):
            r = self.client.post("/api/evolve/agents", json={
                "name": "绑不上", "workspace_id": "ws-any"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("不可达", r.json()["detail"])

    def test_detail_marks_work_deleted_on_404_sticky(self):
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            r = self.client.post("/api/evolve/agents", json={
                "name": "删作品者", "workspace_id": WORK["workspace_id"]})
        agent_id = r.json()["agent_id"]

        def _gone(workspace_id):
            raise executor_client.WorkNotFoundError(workspace_id)
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_gone):
            d1 = self.client.get(f"/api/evolve/agents/{agent_id}")
            self.assertEqual(d1.status_code, 200)
            self.assertTrue(d1.json()["work_deleted"])
            self.assertEqual(d1.json()["work_probe"], "missing")
            # 粘性：作品"复活"后标记仍在（uuid 不复活，防御性断言）
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            d2 = self.client.get(f"/api/evolve/agents/{agent_id}")
            self.assertTrue(d2.json()["work_deleted"])

    def test_detail_unreachable_does_not_mark(self):
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            r = self.client.post("/api/evolve/agents", json={
                "name": "探测失败者", "workspace_id": WORK["workspace_id"]})
        agent_id = r.json()["agent_id"]

        def _boom(workspace_id):
            raise executor_client.ExecutorUnavailableError("timeout")
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_boom):
            d = self.client.get(f"/api/evolve/agents/{agent_id}")
            self.assertEqual(d.status_code, 200)
            self.assertFalse(d.json()["work_deleted"])
            self.assertEqual(d.json()["work_probe"], "unreachable")

    def test_rename_and_archive_release_binding(self):
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            r = self.client.post("/api/evolve/agents", json={
                "name": "旧名", "workspace_id": WORK["workspace_id"]})
            agent_id = r.json()["agent_id"]
            # 改名
            rn = self.client.patch(f"/api/evolve/agents/{agent_id}", json={"name": "新名"})
            self.assertEqual(rn.status_code, 200)
            self.assertEqual(rn.json()["name"], "新名")
            self.assertEqual(rn.json()["workspace_id"], WORK["workspace_id"])
            # 归档 → 释放绑定
            ar = self.client.post(f"/api/evolve/agents/{agent_id}/archive")
            self.assertEqual(ar.status_code, 200)
            # 再绑同作品成功
            r2 = self.client.post("/api/evolve/agents", json={
                "name": "继任者", "workspace_id": WORK["workspace_id"]})
            self.assertEqual(r2.status_code, 201)

    def test_list_and_detail_include_sessions(self):
        with mock.patch.object(agents_api, "fetch_workspace", side_effect=_ok_fetch_workspace):
            r = self.client.post("/api/evolve/agents", json={
                "name": "会话归属", "workspace_id": WORK["workspace_id"]})
            agent_id = r.json()["agent_id"]
        from app.evolve import db as ev_db
        ev_db.create_session("sess-x1", case_id="", agent_id=agent_id)
        listing = self.client.get("/api/evolve/agents")
        row = next(a for a in listing.json()["agents"] if a["agent_id"] == agent_id)
        self.assertEqual(row["session_count"], 1)
        detail = self.client.get(f"/api/evolve/agents/{agent_id}")
        self.assertEqual(len(detail.json()["sessions"]), 1)
        self.assertEqual(detail.json()["sessions"][0]["session_id"], "sess-x1")

    def test_workspaces_proxy_endpoint(self):
        with mock.patch.object(agents_api, "fetch_workspaces",
                               return_value=[dict(WORK)]) as pw:
            r = self.client.get("/api/evolve/workspaces")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 1)
        self.assertEqual(r.json()["workspaces"][0]["owner_username"], "alice")
        pw.assert_called_once()


if __name__ == "__main__":
    unittest.main()
