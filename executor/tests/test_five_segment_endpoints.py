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


# ── 人工段指针端点（REQ-20261019-232617 FR-002/004，AC-003/006）─────


def test_stage_pointer_update_roundtrip(client: TestClient, tmp_path: Path) -> None:
    """AC-003：点确认写指针，返回新 phase 与就绪信号。"""
    from contracts.writing_artifacts import load_writing_state

    (tmp_path / "主题.md").write_text(
        "# 主题 · 书\n> 状态：定稿\n\n## 一句话主题\nx\n\n## 约束级\n"
        "### 核心爽点\nx\n### 核心矛盾\nx\n### 核心爽点剧情\nx\n", encoding="utf-8",
    )
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "outline"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "outline"
    assert load_writing_state(tmp_path).confirmed_stage == "outline"
    # 回退同样走同一入口（AC-006）
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "theme"})
    assert resp.status_code == 200
    assert resp.json()["phase"] == "theme"
    assert load_writing_state(tmp_path).confirmed_stage == "theme"


def test_stage_pointer_rejects_invalid_and_legacy(client: TestClient, tmp_path: Path) -> None:
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "不存在的段"})
    assert resp.status_code == 422
    # legacy 工作区只读：切段一律 409
    (tmp_path / "storyline.md").write_text("旧五件套", encoding="utf-8")
    resp = client.put("/workspaces/ws1/writing/stage", json={"stage": "theme"})
    assert resp.status_code == 409
    resp = client.put("/workspaces/ws2/writing/stage", json={"stage": "theme"})
    assert resp.status_code == 404


# ── 段审循环推进端点（2026-10-27 自动模式卷纲连写 bug 修复）────────


def _seed_confirm_loop_book(ws: Path) -> None:
    """3 卷计划：第 1 卷 3 章写完，第 2 卷卷纲刚定稿（确认后应进章纲）。"""
    (ws / "卷纲").mkdir(exist_ok=True)
    (ws / "章纲").mkdir(exist_ok=True)
    (ws / "正文").mkdir(exist_ok=True)
    (ws / "主题.md").write_text(
        "# 主题 · 书\n> 状态：定稿\n\n## 一句话主题\nx\n\n## 约束级\n"
        "### 核心爽点\nx\n### 核心矛盾\nx\n### 核心爽点剧情\nx\n", encoding="utf-8",
    )
    (ws / "总纲.md").write_text(
        "# 总纲 · 书\n> 状态：定稿\n\n## 核心纲\n\n"
        "| 卷 | 小目标 | 地图 | BOSS | 关键节点 |\n|---|---|---|---|---|\n"
        "| 一 | 活下去 | 垃圾场 | 工头 | 捡神器 |\n"
        "| 二 | 保住小队 | 城区 | 连长 | 反杀 |\n"
        "| 三 | 夺回矿区 | 矿区 | 矿主 | 破局 |\n", encoding="utf-8",
    )
    vol = (
        "# 卷纲 · 第 1 卷 · 活下去\n> 状态：定稿\n\n| 卷末超大爽点 | 捡到首件神器 |\n|---|---|\n"
        "## 发展阶段\n阶段表\n## 爽点节奏表\n节奏表\n## 爽点质检表\n质检表\n## 卷末钩子\n钩子\n"
    )
    (ws / "卷纲" / "第01卷.md").write_text(vol, encoding="utf-8")
    (ws / "卷纲" / "第02卷.md").write_text(vol, encoding="utf-8")
    for n in (1, 2, 3):
        (ws / "章纲" / f"第{n:03d}章.md").write_text("# 章纲\n", encoding="utf-8")
        (ws / "正文" / f"第{n:03d}章完.md").write_text(
            f"# 第{n:03d}章 · 完\n> 状态：人修定稿\n", encoding="utf-8",
        )


def test_stage_advance_loop_roundtrip(client: TestClient, tmp_path: Path) -> None:
    """卷纲段审确认 → 进章纲（不连写下一卷）；trace_id 幂等重放 no-op。"""
    from contracts.writing_artifacts import load_writing_state

    _seed_confirm_loop_book(tmp_path)
    assert client.put("/workspaces/ws1/writing/stage", json={"stage": "volume"}).status_code == 200
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "tr-1"})
    assert resp.status_code == 200
    assert resp.json()["phase"] == "chapter"
    state = load_writing_state(tmp_path)
    assert state.confirmed_stage == "chapter"
    assert state.last_stage_advance["trace_id"] == "tr-1"
    assert state.last_stage_advance["from"] == "volume"
    # 同一待审 trace 重放（resume 网络重试）→ no-op，不二次跳段
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "tr-1"})
    assert resp.status_code == 200
    assert resp.json()["phase"] == "chapter"
    # 新一段的确认（新 trace）：章纲批已落盘 → 进正文
    (tmp_path / "章纲" / "第004章.md").write_text("# 章纲\n", encoding="utf-8")
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "tr-2"})
    assert resp.json()["phase"] == "text"


def test_stage_advance_rejects_legacy_and_missing(client: TestClient, tmp_path: Path) -> None:
    _seed_confirm_loop_book(tmp_path)
    assert client.post("/workspaces/ws2/writing/stage/advance", json={"trace_id": "t"}).status_code == 404
    (tmp_path / "storyline.md").write_text("旧五件套", encoding="utf-8")
    resp = client.post("/workspaces/ws1/writing/stage/advance", json={"trace_id": "t"})
    assert resp.status_code == 409
