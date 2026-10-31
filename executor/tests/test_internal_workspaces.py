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


class TestInternalWorkspaceWritingArtifacts:
    def _workspace_path(self, workspace_id: str) -> Path:
        from app.platform.core.db import UserRepository, get_database
        from app.routers.context import get_thread_store

        owner = UserRepository(get_database()).get_by_username("writer")
        workspace = get_thread_store().get_workspace(owner["user_id"], workspace_id)
        return Path(workspace.workspace_path)


    def test_lists_character_cards_by_category(self, client):
        """人物分区：人物/ 目录卡片按 category=character 列出；非法路径不入列。"""
        from pathlib import Path
    def test_lists_five_segment_files_by_category_and_page(self, client):
        workspace = _make_workspace("writer", "五段作品")
        root = self._workspace_path(workspace["workspace_id"])
        (root / "主题.md").write_text("主题", encoding="utf-8")
        (root / "总纲.md").write_text("总纲", encoding="utf-8")
        (root / "文风.md").write_text("文风", encoding="utf-8")
        directory = root / "正文"
        directory.mkdir(exist_ok=True)
        for chapter in range(1, 4):
            (directory / f"第{chapter:03d}章.md").write_text(
                f"正文 {chapter}", encoding="utf-8"
            )
        (root / ".writing").mkdir()
        (root / ".writing" / "state.json").write_text("{}", encoding="utf-8")

        path = f"/internal/workspaces/{workspace['workspace_id']}/writing-artifacts"
        page1 = client.get(path, params={"category": "chapter_text", "offset": 0, "limit": 2})
        page2 = client.get(path, params={"category": "chapter_text", "offset": 2, "limit": 2})
        assert page1.status_code == page2.status_code == 200
        assert [item["path"] for item in page1.json()["files"]] == [
            "正文/第001章.md", "正文/第002章.md"
        ]
        assert [item["path"] for item in page2.json()["files"]] == ["正文/第003章.md"]
        assert page1.json()["total"] == 3
        all_files = client.get(path).json()["files"]
        assert {item["path"] for item in all_files} == {
            "主题.md", "总纲.md", "文风.md",
            "正文/第001章.md", "正文/第002章.md", "正文/第003章.md",
        }
        assert all(not item["path"].startswith(".writing") for item in all_files)

    def test_pagination_covers_hundred_chapter_files_without_gaps(self, client):
        workspace = _make_workspace("writer", "长篇作品")
        root = self._workspace_path(workspace["workspace_id"])
        directory = root / "正文"
        directory.mkdir(exist_ok=True)
        for chapter in range(1, 251):
            (directory / f"第{chapter:03d}章.md").write_text("正文", encoding="utf-8")

        endpoint = f"/internal/workspaces/{workspace['workspace_id']}/writing-artifacts"
        paths = []
        for offset in range(0, 250, 73):
            response = client.get(endpoint, params={
                "category": "chapter_text", "offset": offset, "limit": 73,
            })
            assert response.status_code == 200
            paths.extend(item["path"] for item in response.json()["files"])
        assert len(paths) == 250
        assert len(set(paths)) == 250
        assert paths[0] == "正文/第001章.md"
        assert paths[-1] == "正文/第250章.md"

    def test_reads_single_allowed_file_and_rejects_non_artifact_paths(self, client):
        workspace = _make_workspace("writer", "读取作品")
        root = self._workspace_path(workspace["workspace_id"])
        (root / "主题.md").write_text("当前主题", encoding="utf-8")
        base = f"/internal/workspaces/{workspace['workspace_id']}/writing-artifacts/content"

        read = client.get(base, params={"path": "主题.md"})
        assert read.status_code == 200
        assert read.json()["content"] == "当前主题"

        for rel in (
            "../outside.md", ".writing/state.json", "正文/../../outside.md",
            "正文\\第001章.md",
        ):
            rejected = client.get(base, params={"path": rel})
            assert rejected.status_code == 400

        (root / "主题.md").unlink()
        missing = client.get(base, params={"path": "主题.md"})
        assert missing.status_code == 404

    def test_rejects_symlink_that_escapes_workspace(self, client, tmp_path):
        workspace = _make_workspace("writer", "链接作品")
        root = self._workspace_path(workspace["workspace_id"])
        outside = tmp_path / "outside.md"
        outside.write_text("越界内容", encoding="utf-8")
        try:
            (root / "主题.md").symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不允许创建符号链接")

        response = client.get(
            f"/internal/workspaces/{workspace['workspace_id']}/writing-artifacts/content",
            params={"path": "主题.md"},
        )
        assert response.status_code == 400

    def test_rejects_resolved_file_outside_workspace(self, client, tmp_path, monkeypatch):
        workspace = _make_workspace("writer", "路径校验作品")
        root = self._workspace_path(workspace["workspace_id"])
        (root / "主题.md").write_text("占位", encoding="utf-8")
        outside = tmp_path / "outside.md"
        outside.write_text("越界内容", encoding="utf-8")
        resolve = Path.resolve

        def resolve_escaped_file(path, *args, **kwargs):
            if path == root / "主题.md":
                return outside
            return resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve_escaped_file)
        response = client.get(
            f"/internal/workspaces/{workspace['workspace_id']}/writing-artifacts/content",
            params={"path": "主题.md"},
        )
        assert response.status_code == 400


if __name__ == "__main__":
    import unittest
    unittest.main()


# ── 人物卡产物列表（REQ-20261031-183257 FR-001/AC-006）─────────────



    workspace_root = Path(client_with_five_workspace["workspace_path"])
    ch_dir = workspace_root / "人物"
    ch_dir.mkdir(parents=True, exist_ok=True)
    (ch_dir / "主角.md").write_text("# 人物卡 · 主角 · 林晚\n", encoding="utf-8")
    (ch_dir / "林晚的随从.md").write_text("# 人物卡 · 工具配角\n", encoding="utf-8")
    (ch_dir / "子目录.md").mkdir(exist_ok=True)  # 不应是文件——跳过
    (ch_dir / "bad:name.md").write_text("x", encoding="utf-8")

    resp = client_with_five_workspace["client"].get(
        f"/internal/workspaces/{client_with_five_workspace['workspace_id']}/writing-artifacts",
        params={"category": "character"},
    )
    assert resp.status_code == 200
    files = resp.json()["files"]
    paths = [f["path"] for f in files]
    assert "人物/主角.md" in paths
    assert "人物/林晚的随从.md" in paths
    assert all("bad:" not in p for p in paths)
