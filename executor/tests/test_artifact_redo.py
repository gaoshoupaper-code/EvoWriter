"""定稿产物删除重做测试（REQ-20261027-213246 修订 2026-10-28）。

跑法（在 executor 目录）：python -m pytest tests/test_artifact_redo.py -v

锁定：
  - 最下游判定：总纲有卷纲/章纲/正文任一即拒；卷纲非最大卷号即拒、有章纲/正文即拒
  - 删除动作：文件移入 .writing/trash、标注清除、段指针下游回落/上游不动
  - 范围外 rel 与不存在文件拒绝；legacy 409（端点）
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.domains.writing.artifact_redo import (
    delete_artifact_for_redo,
    redo_eligibility,
    redo_target_stage,
)
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


def _seed(ws: Path, *, master=True, volumes: list[int] = (), chapters: list[int] = ()) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    if master:
        (ws / "总纲.md").write_text("# 总纲\n> 状态：定稿\n\n核心纲内容\n", encoding="utf-8")
    if volumes:
        (ws / "卷纲").mkdir(exist_ok=True)
        for v in volumes:
            (ws / "卷纲" / f"第{v:02d}卷.md").write_text(f"# 卷纲 · 第 {v} 卷\n> 状态：定稿\n", encoding="utf-8")
    if chapters:
        (ws / "章纲").mkdir(exist_ok=True)
        for c in chapters:
            (ws / "章纲" / f"第{c:03d}章.md").write_text(f"# 章纲 · 第{c:03d}章\n", encoding="utf-8")


# ── 域函数 ─────────────────────────────────────────────────


def test_redo_target_stage_mapping():
    assert redo_target_stage("总纲.md") == "outline"
    assert redo_target_stage("卷纲/第02卷.md") == "volume"
    assert redo_target_stage("文风.md") is None
    assert redo_target_stage("正文/第001章开局.md") is None


def test_master_not_eligible_with_any_downstream(tmp_path: Path):
    _seed(tmp_path, volumes=[1])
    ok, reason = redo_eligibility(tmp_path, "总纲.md")
    assert not ok and "最下游" in reason

    ws2 = tmp_path / "b"
    _seed(ws2, chapters=[1])
    ok, reason = redo_eligibility(ws2, "总纲.md")
    assert not ok and "最下游" in reason


def test_master_eligible_when_downstream_empty(tmp_path: Path):
    _seed(tmp_path)
    assert redo_eligibility(tmp_path, "总纲.md") == (True, "")


def test_volume_eligibility_rules(tmp_path: Path):
    _seed(tmp_path, volumes=[1, 2])
    # 卷1 非最大卷号 → 拒；卷2 最下游 → 允许
    ok, _ = redo_eligibility(tmp_path, "卷纲/第01卷.md")
    assert not ok
    assert redo_eligibility(tmp_path, "卷纲/第02卷.md") == (True, "")
    # 有章纲 → 连最新卷也拒
    (tmp_path / "章纲").mkdir(exist_ok=True)
    (tmp_path / "章纲" / "第001章.md").write_text("# 章纲\n", encoding="utf-8")
    ok, reason = redo_eligibility(tmp_path, "卷纲/第02卷.md")
    assert not ok and "最下游" in reason


def test_delete_master_moves_trash_and_resets_stage(tmp_path: Path):
    from contracts.writing_artifacts import load_writing_state, save_writing_state
    from app.domains.writing.annotations import create_annotation, load_annotations
    from app.schemas.screenplay import AnnotationCreateRequest

    _seed(tmp_path)
    # 指针拨到 volume（下游）+ 挂一条总纲标注
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "volume"
    state.stage = "volume"
    save_writing_state(tmp_path, state)
    create_annotation(tmp_path, AnnotationCreateRequest(
        artifact_rel="总纲.md", label="abstract", quote="核心纲内容",
    ))

    result = delete_artifact_for_redo(tmp_path, "总纲.md")
    assert result["status"] == "ok"
    assert not (tmp_path / "总纲.md").exists()
    assert ".writing/trash" in result["trash_rel"] and (tmp_path / result["trash_rel"]).is_file()
    assert result["removed_annotations"] == 1
    assert load_annotations(tmp_path) == []
    assert result["stage_reset_to"] == "outline"
    assert load_writing_state(tmp_path).confirmed_stage == "outline"


def test_delete_keeps_pointer_when_upstream(tmp_path: Path):
    """指针在 theme（上游）时不借删除往前推段。"""
    from contracts.writing_artifacts import load_writing_state, save_writing_state

    _seed(tmp_path)
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "theme"
    state.stage = "theme"
    save_writing_state(tmp_path, state)
    result = delete_artifact_for_redo(tmp_path, "总纲.md")
    assert result["stage_reset_to"] is None
    assert load_writing_state(tmp_path).confirmed_stage == "theme"


def test_delete_rejects_out_of_scope_and_missing(tmp_path: Path):
    _seed(tmp_path, volumes=[1])
    with pytest.raises(ValueError, match="只支持"):
        delete_artifact_for_redo(tmp_path, "文风.md")
    with pytest.raises(ValueError, match="不存在"):
        delete_artifact_for_redo(tmp_path, "卷纲/第09卷.md")


# ── 端点 ──────────────────────────────────────────────────


def test_endpoint_delete_and_legacy_409(client: TestClient, tmp_path: Path):
    _seed(tmp_path, volumes=[2])
    resp = client.post("/workspaces/ws1/artifact/delete", json={"rel": "卷纲/第02卷.md"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == "卷纲/第02卷.md"
    assert body["pipeline"]["kind"] == "five"

    # 资格不符 → 409 带中文原因
    _seed(tmp_path, volumes=[1, 2])
    resp = client.post("/workspaces/ws1/artifact/delete", json={"rel": "卷纲/第01卷.md"})
    assert resp.status_code == 409
    assert "最下游" in resp.json()["detail"]


def test_endpoint_legacy_guard(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "outline").mkdir()
    (tmp_path / "outline" / "世界观.md").write_text("三步制", encoding="utf-8")
    app = FastAPI()
    app.include_router(ws_router.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    monkeypatch.setattr(ws_router, "get_thread_store", lambda: _FakeStore(tmp_path))
    client = TestClient(app)
    resp = client.post("/workspaces/ws1/artifact/delete", json={"rel": "总纲.md"})
    assert resp.status_code == 409
