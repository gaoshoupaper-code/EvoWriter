"""删作品级联删除测试：workspaces 行的子表必须全部清掉。

背景（bug 复现）：images（DD7a）与 workspace_writing_skills（REQ-20261023）
都带 REFERENCES workspaces(workspace_id) 外键。删作品只删 threads + workspaces，
生成过图或启用过写作技能的书触发 FOREIGN KEY constraint failed → API 500。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.core.db import (
    Database,
    ImageRepository,
    ThreadRepository,
    UserRepository,
    WorkspaceRepository,
    WritingSkillRepository,
    init_database,
)
from app.routers import workspaces as ws_router


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "app.db", load_master_key(generate_master_key()))
    init_database(db)
    u = UserRepository(db).create(username="cascade", password="pw123456")
    owner_id = u["user_id"]
    ws = WorkspaceRepository(db).create(owner_id=owner_id, title="历史作品", domain="writing")
    return {"db": db, "owner_id": owner_id, "workspace_id": ws["workspace_id"]}


def test_delete_workspace_cascades_child_tables(env):
    """有图 + 有技能绑定的作品能删干净，不留子表残留。"""
    db, owner_id, ws_id = env["db"], env["owner_id"], env["workspace_id"]

    ThreadRepository(db).create(workspace_id=ws_id, owner_id=owner_id, session_name="s1")
    ImageRepository(db).create(
        image_id=uuid4().hex, workspace_id=ws_id, owner_id=owner_id,
        round=1, version_id="v1", sample_index=0,
        direction=None, prompt=None, file_path="/images/r1_v1_s0.png",
    )
    skills = WritingSkillRepository(db)
    skill = skills.create(owner_id=owner_id, name="节奏", description="", content="x")
    skills.set_enabled(ws_id, owner_id, [skill["skill_id"]])

    assert WorkspaceRepository(db).delete(ws_id, owner_id) is True

    for table in ("threads", "images", "workspace_writing_skills"):
        n = db.conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE workspace_id = ?", (ws_id,)
        ).fetchone()["c"]
        assert n == 0, f"{table} 残留 {n} 行"
    n = db.conn.execute(
        "SELECT COUNT(*) AS c FROM workspaces WHERE workspace_id = ?", (ws_id,)
    ).fetchone()["c"]
    assert n == 0


def test_delete_workspace_missing_returns_false(env):
    """删不存在的作品返回 False（走 404），不抛异常。"""
    assert WorkspaceRepository(env["db"]).delete("no-such-ws", env["owner_id"]) is False


class _BrokenCheckpointService:
    """delete_thread_checkpoint 一律失败（模拟 no such table / 库损坏）。"""

    async def delete_thread_checkpoint(self, thread_id, *, owner_id=None):
        raise RuntimeError("checkpoint cleanup exploded")


class _FakeThreadStore:
    """delete_workspace 直接成功并返回一个待清理 thread。"""

    def delete_workspace(self, owner_id, workspace_id):
        return ["t1"]


def test_delete_workspace_survives_checkpoint_cleanup_failure(monkeypatch):
    """checkpoint 清理失败不阻断删除：元数据已删成功，应返回 200（线上
    曾因全局 checkpoints.db 缺表 no such table → 删作品 500）。"""
    app = FastAPI()
    app.include_router(ws_router.router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="u1")
    monkeypatch.setattr(ws_router, "get_thread_store", lambda: _FakeThreadStore())
    monkeypatch.setattr(ws_router, "get_agent_service", lambda: _BrokenCheckpointService())
    monkeypatch.setattr(ws_router, "get_character_service", lambda: _BrokenCheckpointService())

    resp = TestClient(app).delete("/api/workspaces/anything")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
