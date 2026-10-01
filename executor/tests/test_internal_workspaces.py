"""executor 内部作品端点单测（REQ-20261001-131018 / FR-001/004/006/009 数据面）。

覆盖 evolution 绑定链路消费的三个内部端点：
  - GET /internal/workspaces：跨 owner 全量列表（含 owner_username/session_count）
  - GET /internal/workspaces/{id}：概要 + 404（作品删除探测语义）
  - GET /internal/workspaces/{id}/artifacts：当前产物快照（storyline/worldview/characters）

跑法（在 executor 目录）：python -m pytest tests/test_internal_workspaces.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    import secrets
    backend_dir = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"

    monkeypatch.setenv("MASTER_KEY", secrets.token_hex(32))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.platform.core.db"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("ADMIN_USERNAME", "rootadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw-123")
    monkeypatch.setenv("WRITER_MODEL", "gpt-4o")
    monkeypatch.setenv("WRITER_AGENT_MODE", "mock")
    monkeypatch.setenv("WRITER_FRONTEND_ORIGIN", "http://localhost:3000")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11111/v1")
    monkeypatch.chdir(backend_dir)

    import importlib
    import app.platform.core.settings as settings_mod
    settings_mod.get_settings.cache_clear()
    import app.platform.core.db as db_mod
    db_mod._database = None
    import app.platform.core.checkpoint_pool as pool_mod
    pool_mod._pool = None

    from app.main import app
    importlib.reload(sys_modules_app())
    yield app


def sys_modules_app():
    import sys
    return sys.modules["app.main"]


@pytest.fixture()
def client(app_env):
    with TestClient(app_env) as c:
        yield c


def _make_workspace(owner_username: str, title: str) -> dict:
    """直接经 ThreadStore 建用户+作品（绕开 HTTP 注册流）。"""
    from app.platform.core.db import Database, UserRepository, get_database
    from app.platform.state.thread_store import ThreadStore
    from app.routers.context import get_thread_store

    db = get_database()
    users = UserRepository(db)
    existing = users.get_by_username(owner_username)
    owner = existing or users.create(username=owner_username, password="pw123456")
    store = get_thread_store()
    return store.create_workspace(owner["user_id"], title, "writing").model_dump(mode="json")


class TestInternalWorkspacesList:
    def test_lists_all_workspaces_cross_owner_with_username(self, client):
        w1 = _make_workspace("alice", "Alice的作品")
        w2 = _make_workspace("bob", "Bob的作品")
        r = client.get("/internal/workspaces")
        assert r.status_code == 200
        data = r.json()
        ids = {w["workspace_id"]: w for w in data["workspaces"]}
        assert w1["workspace_id"] in ids and w2["workspace_id"] in ids
        row = ids[w1["workspace_id"]]
        assert row["owner_username"] == "alice"
        assert row["title"] == "Alice的作品"
        assert "session_count" in row

    def test_empty_db_returns_empty_list(self, client):
        r = client.get("/internal/workspaces")
        assert r.status_code == 200
        assert r.json() == {"workspaces": [], "total": 0}


class TestInternalWorkspaceDetail:
    def test_summary_contains_owner_and_counts(self, client):
        w = _make_workspace("carol", "概要作品")
        r = client.get(f"/internal/workspaces/{w['workspace_id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "概要作品"
        assert body["owner_username"] == "carol"
        assert body["workspace_id"] == w["workspace_id"]

    def test_missing_workspace_404(self, client):
        r = client.get("/internal/workspaces/deadbeef")
        assert r.status_code == 404


class TestInternalWorkspaceArtifacts:
    def test_artifacts_snapshot_reflects_files(self, client):
        from app.platform.core.db import UserRepository, get_database
        from app.routers.context import get_thread_store

        w = _make_workspace("dave", "产物作品")
        owner = UserRepository(get_database()).get_by_username("dave")
        ws = get_thread_store().get_workspace(owner["user_id"], w["workspace_id"])
        ws_path = Path(ws.workspace_path)
        ws_path.mkdir(parents=True, exist_ok=True)
        (ws_path / "worldview.md").write_text("# 世界观\n魔法世界", encoding="utf-8")
        (ws_path / "character").mkdir(exist_ok=True)
        (ws_path / "character" / "林零.md").write_text("# 林零\n主角", encoding="utf-8")

        r = client.get(f"/internal/workspaces/{w['workspace_id']}/artifacts")
        assert r.status_code == 200
        body = r.json()
        assert "魔法世界" in body["worldview"]["markdown"]
        chars = {c["name"]: c for c in body["characters"]["characters"]}
        assert "林零" in chars and "主角" in chars["林零"]["markdown"]
        # 未写过的 storyline 也返回结构（空 v2），不 500
        assert body["storyline"] is not None

    def test_artifacts_missing_workspace_404(self, client):
        r = client.get("/internal/workspaces/none/artifacts")
        assert r.status_code == 404


if __name__ == "__main__":
    import unittest
    unittest.main()
