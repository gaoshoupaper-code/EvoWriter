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
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

from fastapi.testclient import TestClient

import app.core.db as db
from app.evolve import agents_repo
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
            conn.execute("DELETE FROM artifact_revisions")
            conn.execute("DELETE FROM artifacts")
            conn.execute("DELETE FROM payload_objects")
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

    def test_agent_artifact_summary_uses_bound_workspace_and_hides_payload(self):
        agent = agents_repo.create("产物Agent", WORK["workspace_id"])
        request = SimpleNamespace(state=SimpleNamespace(is_super_admin=False, user_id="reader"))
        current = {
            "workspace_id": "untrusted-id",
            "total": 1,
            "files": [{"path": "正文/第001章.md", "category": "chapter_text"}],
        }
        db.execute(
            "INSERT INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at)"
            " VALUES ('artifact-ours', 'workspace_file', ?, '/正文/第001章.md', '2026-10-01')",
            (WORK["workspace_id"],),
        )
        db.execute(
            "INSERT INTO payload_objects "
            "(payload_id, content_hash, kind, size_bytes, sensitivity, expires_at, storage_path, created_at) "
            "VALUES ('payload-api', 'hash-api', 'text', 4, 'normal', '2099-01-01', '/unused', '2026-10-01')"
        )
        db.execute(
            "INSERT INTO artifact_revisions "
            "(artifact_revision_id, artifact_id, payload_id, content_hash, producer_trace_id, created_at) "
            "VALUES ('revision-api', 'artifact-ours', 'payload-api', 'hash-api', 'trace-1', '2026-10-01')"
        )
        db.execute(
            "INSERT INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at)"
            " VALUES ('artifact-internal', 'workspace_file', ?, '/.writing/state.md', '2026-10-01')",
            (WORK["workspace_id"],),
        )
        db.execute(
            "INSERT INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at)"
            " VALUES ('artifact-theirs', 'workspace_file', 'other-work', '/秘密.md', '2026-10-01')"
        )
        with mock.patch.object(agents_api, "fetch_workspace_writing_artifacts", return_value=current) as fetch:
            result = agents_api.list_agent_artifacts(
                agent["agent_id"], request, category=None, offset=0, limit=100,
                revision_offset=0, revision_limit=100,
            )
        fetch.assert_called_once_with(
            WORK["workspace_id"], category=None, offset=0, limit=100
        )
        self.assertEqual(result["workspace_id"], WORK["workspace_id"])
        self.assertFalse(result["can_read_content"])
        self.assertEqual([group["logical_key"] for group in result["revisions"]], ["/正文/第001章.md"])
        self.assertEqual(result["revision_total"], 1)
        self.assertNotIn("payload_id", str(result))
        self.assertNotIn("/.writing/state.md", str(result))

    def test_agent_artifact_revision_pages_are_stable_and_scoped(self):
        agent = agents_repo.create("修订分页Agent", WORK["workspace_id"])
        db.execute(
            "INSERT INTO payload_objects "
            "(payload_id, content_hash, kind, size_bytes, sensitivity, expires_at, storage_path, created_at) "
            "VALUES ('payload-pages', 'hash-pages', 'text', 1, 'normal', '2099-01-01', '/unused', '2026-10-01')"
        )
        db.execute(
            "INSERT INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at) "
            "VALUES ('artifact-page', 'workspace_file', ?, '/正文/第001章.md', '2026-10-01')",
            (WORK["workspace_id"],),
        )
        for number in range(3):
            db.execute(
                "INSERT INTO artifact_revisions "
                "(artifact_revision_id, artifact_id, payload_id, content_hash, created_at) "
                "VALUES (?, 'artifact-page', 'payload-pages', ?, ?)",
                (f"revision-page-{number}", f"hash-{number}", f"2026-10-0{number + 1}"),
            )
        request = SimpleNamespace(state=SimpleNamespace(is_super_admin=False, user_id="reader"))
        with mock.patch.object(agents_api, "fetch_workspace_writing_artifacts", return_value={
            "workspace_id": WORK["workspace_id"], "kind": "five", "total": 0, "files": [],
        }):
            first = agents_api.list_agent_artifacts(
                agent["agent_id"], request, revision_offset=0, revision_limit=2
            )
            second = agents_api.list_agent_artifacts(
                agent["agent_id"], request, revision_offset=2, revision_limit=2
            )
        first_ids = [r["artifact_revision_id"] for g in first["revisions"] for r in g["revisions"]]
        second_ids = [r["artifact_revision_id"] for g in second["revisions"] for r in g["revisions"]]
        self.assertEqual(first["revision_total"], 3)
        self.assertEqual(len(first_ids), 2)
        self.assertEqual(second_ids, ["revision-page-2"])
        self.assertTrue(set(first_ids).isdisjoint(second_ids))

    def test_agent_revision_content_requires_admin_scope_and_preserves_expiry(self):
        from contracts.trace.payload import ContentAddressedPayloadStore

        agent = agents_repo.create("历史正文Agent", WORK["workspace_id"])
        payload_ref = ContentAddressedPayloadStore(settings.trace_payload_path).put("历史修订正文")
        db.execute(
            "INSERT INTO payload_objects "
            "(payload_id, content_hash, kind, size_bytes, sensitivity, expires_at, storage_path, created_at) "
            "VALUES (?, ?, ?, ?, 'restricted', ?, ?, '2026-10-01')",
            (payload_ref.payload_id, payload_ref.content_hash, payload_ref.kind,
             payload_ref.size_bytes, payload_ref.expires_at,
             str(settings.trace_payload_path / f"{payload_ref.payload_id}.json")),
        )
        for artifact_id, workspace_id, path in (
            ("artifact-rev-owned", WORK["workspace_id"], "/正文/第001章.md"),
            ("artifact-rev-other", "other-work", "/正文/第002章.md"),
        ):
            db.execute(
                "INSERT INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at) "
                "VALUES (?, 'workspace_file', ?, ?, '2026-10-01')",
                (artifact_id, workspace_id, path),
            )
        for revision_id, artifact_id in (
            ("revision-owned", "artifact-rev-owned"),
            ("revision-other", "artifact-rev-other"),
        ):
            db.execute(
                "INSERT INTO artifact_revisions "
                "(artifact_revision_id, artifact_id, payload_id, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, '2026-10-01')",
                (revision_id, artifact_id, payload_ref.payload_id, payload_ref.content_hash),
            )
        reader = SimpleNamespace(state=SimpleNamespace(is_super_admin=False, user_id="reader"))
        with self.assertRaises(agents_api.HTTPException) as denied:
            agents_api.read_agent_artifact_revision(agent["agent_id"], "revision-owned", reader)
        self.assertEqual(denied.exception.status_code, 403)

        admin = SimpleNamespace(state=SimpleNamespace(is_super_admin=True, user_id="admin"))
        owned = agents_api.read_agent_artifact_revision(agent["agent_id"], "revision-owned", admin)
        self.assertEqual(owned["content"], "历史修订正文")
        with self.assertRaises(agents_api.HTTPException) as foreign:
            agents_api.read_agent_artifact_revision(agent["agent_id"], "revision-other", admin)
        self.assertEqual(foreign.exception.status_code, 404)
        audit = db.query_one(
            "SELECT actor_user_id, object_type FROM access_audit WHERE object_id='revision-owned'"
        )
        self.assertEqual(audit["actor_user_id"], "admin")
        self.assertEqual(audit["object_type"], "artifact_revision")

    def test_agent_artifact_content_requires_super_admin_before_fetch(self):
        agent = agents_repo.create("正文权限Agent", WORK["workspace_id"])
        request = SimpleNamespace(state=SimpleNamespace(is_super_admin=False, user_id="reader"))
        with mock.patch.object(agents_api, "fetch_workspace_writing_artifact") as fetch:
            with self.assertRaises(agents_api.HTTPException) as denied:
                agents_api.read_agent_artifact(agent["agent_id"], request, "主题.md")
        self.assertEqual(denied.exception.status_code, 403)
        fetch.assert_not_called()

    def test_agent_artifact_content_uses_bound_workspace_and_audits(self):
        agent = agents_repo.create("正文读取Agent", WORK["workspace_id"])
        request = SimpleNamespace(state=SimpleNamespace(is_super_admin=True, user_id="admin"))
        with mock.patch.object(
            agents_api, "fetch_workspace_writing_artifact", return_value="当前主题"
        ) as fetch:
            result = agents_api.read_agent_artifact(agent["agent_id"], request, "主题.md")
        fetch.assert_called_once_with(WORK["workspace_id"], "主题.md")
        self.assertEqual(result["content"], "当前主题")
        audit = db.query_one("SELECT actor_user_id, object_type FROM access_audit")
        self.assertEqual(audit["actor_user_id"], "admin")
        self.assertEqual(audit["object_type"], "workspace_artifact")


if __name__ == "__main__":
    unittest.main()
