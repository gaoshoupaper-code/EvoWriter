"""五段产物树与正文端点测试（REQ-20261018-115508 FR-009/011，AC-011/013 数据面）。

轻量 TestClient（只挂 workspaces 路由 + 依赖覆盖），锁定：
  - _read_outline_content：five 工作区返回五段产物树（顺序：主题→总纲→文风→卷纲→章纲→正文）。
  - GET/PUT manuscript：人修保存直写、finalize 改状态行、缺失 404/exists=False。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.routers import workspaces as ws_router


class _FakeStore:
    def __init__(self, workspace_path: Path) -> None:
        self._ws = SimpleNamespace(workspace_path=str(workspace_path))

    def get_workspace(self, owner_id: str, workspace_id: str):
        return self._ws if workspace_id == "ws1" else None


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(ws_router.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    monkeypatch.setattr(ws_router, "get_thread_store", lambda: _FakeStore(tmp_path))
    return TestClient(app)


def _seed_book(ws: Path) -> None:
    (ws / "主题.md").write_text("# 主题 · 书\n> 状态：定稿\n", encoding="utf-8")
    (ws / "总纲.md").write_text("# 总纲 · 书\n> 状态：定稿\n", encoding="utf-8")
    (ws / "文风.md").write_text("# 文风 · 书\n> 状态：风格卡已定\n", encoding="utf-8")
    (ws / "卷纲").mkdir()
    (ws / "卷纲" / "第01卷.md").write_text("# 卷纲 · 第 1 卷\n", encoding="utf-8")
    (ws / "章纲").mkdir()
    (ws / "章纲" / "第001章.md").write_text("# 章纲 · 第001章\n", encoding="utf-8")
    (ws / "正文").mkdir()
    (ws / "正文" / "第001章开局.md").write_text(
        "# 第001章 · 开局\n> 状态：初稿｜2026-10-18\n\n正文内容", encoding="utf-8"
    )


def test_outline_tree_returns_five_segments(client: TestClient, tmp_path: Path) -> None:
    _seed_book(tmp_path)
    resp = client.get("/workspaces/ws1/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "five"
    rels = [f["rel"] for f in body["files"]]
    assert rels == [
        "主题.md", "总纲.md", "文风.md",
        "卷纲/第01卷.md", "章纲/第001章.md", "正文/第001章开局.md",
    ]
    assert body["pipeline"]["kind"] == "five"


def test_outline_tree_empty_book(client: TestClient, tmp_path: Path) -> None:
    resp = client.get("/workspaces/ws1/outline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "five" and body["files"] == []


def test_manuscript_get_missing(client: TestClient) -> None:
    resp = client.get("/workspaces/ws1/manuscript/9")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False and body["status"] == "draft"


def test_manuscript_roundtrip_and_finalize(client: TestClient, tmp_path: Path) -> None:
    _seed_book(tmp_path)
    got = client.get("/workspaces/ws1/manuscript/1").json()
    assert got["exists"] is True and got["status"] == "draft"
    assert "正文内容" in got["markdown"]

    edited = got["markdown"].replace("正文内容", "人修后的正文内容")
    resp = client.put("/workspaces/ws1/manuscript/1", json={"markdown": edited, "finalize": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "finalized"
    disk = (tmp_path / "正文" / "第001章开局.md").read_text(encoding="utf-8")
    assert "人修定稿" in disk and "人修后的正文内容" in disk


def test_manuscript_save_without_finalize_keeps_status(client: TestClient, tmp_path: Path) -> None:
    _seed_book(tmp_path)
    resp = client.put(
        "/workspaces/ws1/manuscript/1",
        json={"markdown": "# 第001章 · 开局\n> 状态：初稿｜2026-10-18\n\n改了一版", "finalize": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert "初稿" in (tmp_path / "正文" / "第001章开局.md").read_text(encoding="utf-8")


def test_manuscript_put_missing_404(client: TestClient) -> None:
    resp = client.put("/workspaces/ws1/manuscript/9", json={"markdown": "x", "finalize": False})
    assert resp.status_code == 404


def test_manuscript_finalize_without_status_line(client: TestClient, tmp_path: Path) -> None:
    """T5：正文无状态行时 finalize 走 insert(1)——标题仍居首行。"""
    (tmp_path / "正文").mkdir()
    (tmp_path / "正文" / "第002章.md").write_text(
        "# 第002章 · 无状态行\n\n正文", encoding="utf-8"
    )
    resp = client.put(
        "/workspaces/ws1/manuscript/2",
        json={"markdown": "# 第002章 · 无状态行\n\n正文改", "finalize": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "finalized"
    disk = (tmp_path / "正文" / "第002章.md").read_text(encoding="utf-8")
    lines = disk.splitlines()
    assert lines[0] == "# 第002章 · 无状态行"
    assert "人修定稿" in lines[1]


def test_watch_classify_five_tree(tmp_path: Path) -> None:
    """T6：五段产物树变更全归 outline 事件；.writing 状态文件不归类。"""
    from app.routers.workspaces import _classify_changes

    def _changes(*rels):
        return [("modified", str(tmp_path / r)) for r in rels]

    cats = _classify_changes(_changes("主题.md", "总纲.md", "文风.md", "卷纲/第01卷.md", "章纲/第001章.md", "正文/第001章.md"), tmp_path)
    assert cats == {"outline"}
    assert _classify_changes(_changes(".writing/state.json"), tmp_path) == set()
    assert _classify_changes(_changes("outline/总纲.md"), tmp_path) == {"outline"}
