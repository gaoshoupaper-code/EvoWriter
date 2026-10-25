"""用户自定义写作技能：API 路由层（REQ-20261023-000000 FR-001/002，AC-001/002/006）。

轻量 TestClient（只挂 writing_skills 路由 + 依赖覆盖 + 真 SQLite），
锁定：三字段必填与 3000 字边界、每书启用 ≤5 条双保险（服务端拒绝）、
跨账号 404 隔离。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.core.db import (
    Database, UserRepository, WorkspaceRepository, init_database,
)
from app.routers import writing_skills as wsk_router


class _FakeStore:
    """只有 owner_u1 拥有 ws_id 这本书（workspace 归属过滤走这里）。"""

    def __init__(self, owner_u1: str, ws_id: str) -> None:
        self.owner_u1 = owner_u1
        self.ws_id = ws_id

    def get_workspace(self, owner_id: str, workspace_id: str):
        if workspace_id == self.ws_id and owner_id == self.owner_u1:
            return SimpleNamespace(workspace_id=workspace_id)
        return None


@pytest.fixture()
def db(tmp_path) -> Database:
    d = Database(tmp_path / "app.platform.core.db", load_master_key(generate_master_key()))
    init_database(d)
    return d


@pytest.fixture()
def ws1(db, users) -> str:
    u1, _ = users
    return WorkspaceRepository(db).create(owner_id=u1, title="仙侠")["workspace_id"]


@pytest.fixture()
def users(db):
    repo = UserRepository(db)
    u1 = repo.create(username="alice", password="pw123456")["user_id"]
    u2 = repo.create(username="bob", password="pw123456")["user_id"]
    return u1, u2


def _client_for(db, monkeypatch, user_id: str, owner_u1: str, ws_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(wsk_router.router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id=user_id)
    monkeypatch.setattr(
        wsk_router, "get_thread_store", lambda: _FakeStore(owner_u1, ws_id)
    )
    return TestClient(app)


@pytest.fixture()
def client(db, users, ws1, monkeypatch) -> TestClient:
    u1, _ = users
    return _client_for(db, monkeypatch, u1, u1, ws1)


def _payload(**over) -> dict:
    base = {
        "name": "武侠打斗",
        "description": "侧重招式细节与节奏",
        "content": "打斗时先写招式名，再写身体反应。",
        "stage": "text",
    }
    base.update(over)
    return base


def _create_skill(client, **over) -> dict:
    resp = client.post("/api/writing-skills", json=_payload(**over))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 字段校验（FR-001 / AC-001）─────────────────────────────

class TestFieldValidation:
    @pytest.mark.parametrize("field", ["name", "description", "content"])
    def test_empty_field_rejected(self, client, field):
        resp = client.post("/api/writing-skills", json=_payload(**{field: ""}))
        assert resp.status_code == 422

    @pytest.mark.parametrize("field", ["name", "description", "content"])
    def test_whitespace_only_rejected(self, client, field):
        resp = client.post("/api/writing-skills", json=_payload(**{field: "   "}))
        assert resp.status_code == 422

    def test_content_exactly_20000_chars_ok(self, client):
        skill = _create_skill(client, content="字" * 20000)
        assert len(skill["content"]) == 20000

    def test_content_20001_chars_rejected(self, client):
        resp = client.post("/api/writing-skills", json=_payload(content="字" * 20001))
        assert resp.status_code == 422

    def test_update_content_boundary_too(self, client):
        skill = _create_skill(client)
        resp = client.put(
            f"/api/writing-skills/{skill['skill_id']}",
            json=_payload(content="字" * 20001),
        )
        assert resp.status_code == 422

    def test_crud_roundtrip(self, client):
        skill = _create_skill(client)
        listed = client.get("/api/writing-skills").json()
        assert [s["skill_id"] for s in listed] == [skill["skill_id"]]
        upd = client.put(
            f"/api/writing-skills/{skill['skill_id']}", json=_payload(name="新名字"),
        )
        assert upd.status_code == 200 and upd.json()["name"] == "新名字"
        dele = client.delete(f"/api/writing-skills/{skill['skill_id']}")
        assert dele.status_code == 200
        assert client.get("/api/writing-skills").json() == []


# ── 按书启用（FR-002 / AC-002）─────────────────────────────

class TestEnableEndpoints:
    def test_enable_roundtrip(self, client, ws1):
        ids = [_create_skill(client, name=f"技能{i}")["skill_id"] for i in range(2)]
        resp = client.put(f"/api/workspaces/{ws1}/writing-skills", json={"skill_ids": ids})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 响应自带权威启用列表（含 stage），客户端免二次回拉
        assert [s["name"] for s in body["skills"]] == ["技能0", "技能1"]
        got = client.get(f"/api/workspaces/{ws1}/writing-skills").json()
        assert [s["name"] for s in got] == ["技能0", "技能1"]

    def test_six_skills_rejected_by_server(self, client, ws1):
        ids = [_create_skill(client, name=f"技能{i}")["skill_id"] for i in range(6)]
        resp = client.put(f"/api/workspaces/{ws1}/writing-skills", json={"skill_ids": ids})
        assert resp.status_code == 422

    def test_exactly_five_ok(self, client, ws1):
        ids = [_create_skill(client, name=f"技能{i}")["skill_id"] for i in range(5)]
        resp = client.put(f"/api/workspaces/{ws1}/writing-skills", json={"skill_ids": ids})
        assert resp.status_code == 200
        assert len(client.get(f"/api/workspaces/{ws1}/writing-skills").json()) == 5

    def test_duplicate_ids_rejected(self, client, ws1):
        sid = _create_skill(client)["skill_id"]
        resp = client.put(f"/api/workspaces/{ws1}/writing-skills", json={"skill_ids": [sid, sid]})
        assert resp.status_code == 422

    def test_unknown_workspace_404(self, client, ws1):
        sid = _create_skill(client)["skill_id"]
        resp = client.put("/api/workspaces/nope/writing-skills", json={"skill_ids": [sid]})
        assert resp.status_code == 404


# ── 账号隔离（FR-006 / AC-006）──────────────────────────────

class TestOwnerIsolation:
    def test_other_owner_sees_nothing(self, client, db, users, ws1, monkeypatch):
        u1, u2 = users
        _create_skill(client)
        c2 = _client_for(db, monkeypatch, u2, u1, ws1)
        assert c2.get("/api/writing-skills").json() == []

    def test_other_owner_cannot_touch_by_id(self, client, db, users, ws1, monkeypatch):
        u1, u2 = users
        skill = _create_skill(client)
        c2 = _client_for(db, monkeypatch, u2, u1, ws1)
        assert c2.put(
            f"/api/writing-skills/{skill['skill_id']}", json=_payload(name="抢改"),
        ).status_code == 404
        assert c2.delete(f"/api/writing-skills/{skill['skill_id']}").status_code == 404

    def test_other_owner_cannot_enable_others_skill(self, client, db, users, ws1, monkeypatch):
        u1, u2 = users
        skill = _create_skill(client)
        c2 = _client_for(db, monkeypatch, u2, u1, ws1)
        # u2 没有这本书的归属权 → 404（workspace 层先拦）
        resp = c2.put(
            f"/api/workspaces/{ws1}/writing-skills", json={"skill_ids": [skill["skill_id"]]},
        )
        assert resp.status_code == 404


# ── 阶段字段校验（REQ-20261025-205943 FR-001 / AC-001）───────

class TestStageValidation:
    def test_missing_stage_defaults_to_text(self, client):
        """旧桌面端不带 stage 字段（需求风险节兼容口径）：按正文段创建。"""
        payload = _payload()
        del payload["stage"]
        resp = client.post("/api/writing-skills", json=payload)
        assert resp.status_code == 200, resp.text
        assert resp.json()["stage"] == "text"

    @pytest.mark.parametrize("bad", ["theme", "legacy", "foo", ""])
    def test_invalid_stage_rejected(self, client, bad):
        resp = client.post("/api/writing-skills", json=_payload(stage=bad))
        assert resp.status_code == 422

    @pytest.mark.parametrize("stage", ["outline", "volume", "chapter", "text"])
    def test_four_stages_roundtrip(self, client, stage):
        skill = _create_skill(client, stage=stage)
        assert skill["stage"] == stage

    def test_update_stage(self, client):
        skill = _create_skill(client, stage="text")
        resp = client.put(
            f"/api/writing-skills/{skill['skill_id']}", json=_payload(stage="volume"),
        )
        assert resp.status_code == 200 and resp.json()["stage"] == "volume"

    def test_update_invalid_stage_rejected(self, client):
        skill = _create_skill(client)
        resp = client.put(
            f"/api/writing-skills/{skill['skill_id']}", json=_payload(stage="theme"),
        )
        assert resp.status_code == 422

    def test_update_without_stage_preserves_stage(self, client):
        """旧客户端编辑不带 stage（需求风险节兼容）：阶段保持不变。"""
        skill = _create_skill(client, stage="outline")
        payload = _payload(name="改名")
        del payload["stage"]
        resp = client.put(f"/api/writing-skills/{skill['skill_id']}", json=payload)
        assert resp.status_code == 200, resp.text
        assert resp.json()["stage"] == "outline"
        assert resp.json()["name"] == "改名"


# ── 每段启用上限（REQ-20261025-205943 FR-002 / DEC-005 / AC-003）──

class TestPerStageEnableLimit:
    def test_six_same_stage_rejected(self, client, ws1):
        ids = [
            _create_skill(client, name=f"总纲技{i}", stage="outline")["skill_id"]
            for i in range(6)
        ]
        resp = client.put(f"/api/workspaces/{ws1}/writing-skills", json={"skill_ids": ids})
        assert resp.status_code == 422

    def test_five_per_stage_across_stages_ok(self, client, ws1):
        """跨段不挤占：总纲 5 + 正文 5 = 10 条合法（旧全书 ≤5 口径已废）。"""
        ids = [
            _create_skill(client, name=f"总纲技{i}", stage="outline")["skill_id"]
            for i in range(5)
        ]
        ids += [
            _create_skill(client, name=f"正文技{i}", stage="text")["skill_id"]
            for i in range(5)
        ]
        resp = client.put(f"/api/workspaces/{ws1}/writing-skills", json={"skill_ids": ids})
        assert resp.status_code == 200, resp.text
        assert len(client.get(f"/api/workspaces/{ws1}/writing-skills").json()) == 10

    def test_stage_change_rejects_when_book_overflows(self, client, ws1):
        """改阶段不得绕过每段上限：书已启用 5 条总纲，把已启用的正文技能
        改成总纲 → 422；未启用的技能改阶段不受影响。"""
        outline_ids = [
            _create_skill(client, name=f"总纲技{i}", stage="outline")["skill_id"]
            for i in range(5)
        ]
        enabled_text = _create_skill(client, name="正文技", stage="text")["skill_id"]
        idle_text = _create_skill(client, name="未启用正文技", stage="text")["skill_id"]
        resp = client.put(
            f"/api/workspaces/{ws1}/writing-skills",
            json={"skill_ids": outline_ids + [enabled_text]},
        )
        assert resp.status_code == 200, resp.text
        # 已启用：改成总纲会让该书总纲段到 6 条 → 拒
        reject = client.put(
            f"/api/writing-skills/{enabled_text}", json=_payload(stage="outline"),
        )
        assert reject.status_code == 422
        # 未启用：该书没有它的绑定，改阶段不超限 → 过
        ok = client.put(
            f"/api/writing-skills/{idle_text}", json=_payload(stage="outline"),
        )
        assert ok.status_code == 200 and ok.json()["stage"] == "outline"
