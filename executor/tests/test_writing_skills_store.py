"""用户自定义写作技能：存储层（REQ-20261023-000000 FR-001/002/005/006）。

跑法（在 executor 目录）：
    .venv/Scripts/python.exe -m pytest tests/test_writing_skills_store.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.platform.core.security import generate_master_key, load_master_key
from app.platform.core.db import (
    Database,
    UserRepository,
    WritingSkillRepository,
    init_database,
)
from app.platform.state.thread_store import ThreadStore


# ── fixtures ───────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path) -> Database:
    d = Database(tmp_path / "app.platform.core.db", load_master_key(generate_master_key()))
    init_database(d)
    return d


@pytest.fixture()
def workspace_root(tmp_path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture()
def thread_store(db, workspace_root) -> ThreadStore:
    return ThreadStore(db, workspace_root)


@pytest.fixture()
def repo(db) -> WritingSkillRepository:
    return WritingSkillRepository(db)


@pytest.fixture()
def two_users(db):
    repo = UserRepository(db)
    u1 = repo.create(username="alice", password="pw123456")
    u2 = repo.create(username="bob", password="pw123456")
    return u1["user_id"], u2["user_id"]


def _make_skill(repo: WritingSkillRepository, owner_id: str, name: str = "武侠打斗"):
    return repo.create(
        owner_id=owner_id, name=name,
        description="侧重招式细节与节奏", content="打斗时先写招式名，再写身体反应。",
    )


# ── CRUD 与账号隔离（FR-001 / FR-006 / AC-006）─────────────

class TestSkillCrud:
    def test_create_and_get_roundtrip(self, repo, two_users):
        u1, _ = two_users
        sk = _make_skill(repo, u1)
        assert sk["name"] == "武侠打斗"
        assert sk["description"] == "侧重招式细节与节奏"
        assert sk["content"] == "打斗时先写招式名，再写身体反应。"
        assert repo.get(sk["skill_id"], u1)["name"] == "武侠打斗"

    def test_list_by_owner_only_sees_own(self, repo, two_users):
        u1, u2 = two_users
        _make_skill(repo, u1, "u1 技能")
        _make_skill(repo, u2, "u2 技能")
        names_u1 = [s["name"] for s in repo.list_by_owner(u1)]
        assert names_u1 == ["u1 技能"]
        names_u2 = [s["name"] for s in repo.list_by_owner(u2)]
        assert names_u2 == ["u2 技能"]

    def test_update_changes_content(self, repo, two_users):
        u1, _ = two_users
        sk = _make_skill(repo, u1)
        updated = repo.update(sk["skill_id"], u1, content="新指令内容")
        assert updated["content"] == "新指令内容"
        assert updated["name"] == "武侠打斗"

    def test_get_and_update_reject_other_owner(self, repo, two_users):
        u1, u2 = two_users
        sk = _make_skill(repo, u1)
        assert repo.get(sk["skill_id"], u2) is None
        assert repo.update(sk["skill_id"], u2, name="抢改") is None


# ── 按书启用绑定（FR-002 / AC-002）─────────────────────────

class TestEnableBinding:
    def test_set_and_list_enabled(self, repo, thread_store, two_users):
        u1, _ = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        a = _make_skill(repo, u1, "技能A")
        b = _make_skill(repo, u1, "技能B")
        repo.set_enabled(ws.workspace_id, u1, [a["skill_id"], b["skill_id"]])
        enabled = repo.list_enabled_for_workspace(ws.workspace_id, u1)
        assert [s["name"] for s in enabled] == ["技能A", "技能B"]
        assert enabled[0]["content"] == "打斗时先写招式名，再写身体反应。"

    def test_set_enabled_replaces_previous(self, repo, thread_store, two_users):
        u1, _ = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        a = _make_skill(repo, u1, "技能A")
        b = _make_skill(repo, u1, "技能B")
        repo.set_enabled(ws.workspace_id, u1, [a["skill_id"], b["skill_id"]])
        repo.set_enabled(ws.workspace_id, u1, [b["skill_id"]])
        assert [s["name"] for s in repo.list_enabled_for_workspace(ws.workspace_id, u1)] == ["技能B"]

    def test_max_five_per_workspace(self, repo, thread_store, two_users):
        u1, _ = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        ids = [_make_skill(repo, u1, f"技能{i}")["skill_id"] for i in range(6)]
        with pytest.raises(ValueError):
            repo.set_enabled(ws.workspace_id, u1, ids)
        # 恰好 5 条合法
        repo.set_enabled(ws.workspace_id, u1, ids[:5])
        assert len(repo.list_enabled_for_workspace(ws.workspace_id, u1)) == 5

    def test_cannot_enable_others_skill(self, repo, thread_store, two_users):
        u1, u2 = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        other = _make_skill(repo, u2, "别人的技能")
        with pytest.raises(ValueError):
            repo.set_enabled(ws.workspace_id, u1, [other["skill_id"]])

    def test_binding_is_per_workspace(self, repo, thread_store, two_users):
        u1, _ = two_users
        ws1 = thread_store.create_workspace(u1, "仙侠")
        ws2 = thread_store.create_workspace(u1, "都市")
        a = _make_skill(repo, u1, "技能A")
        repo.set_enabled(ws1.workspace_id, u1, [a["skill_id"]])
        assert repo.list_enabled_for_workspace(ws2.workspace_id, u1) == []

    def test_list_enabled_rejects_other_owner(self, repo, thread_store, two_users):
        u1, u2 = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        a = _make_skill(repo, u1, "技能A")
        repo.set_enabled(ws.workspace_id, u1, [a["skill_id"]])
        assert repo.list_enabled_for_workspace(ws.workspace_id, u2) == []


# ── 删除联动（FR-005 / AC-005）──────────────────────────────

class TestDeleteCascade:
    def test_delete_clears_bindings_across_workspaces(self, repo, thread_store, db, two_users):
        u1, _ = two_users
        ws1 = thread_store.create_workspace(u1, "仙侠")
        ws2 = thread_store.create_workspace(u1, "都市")
        a = _make_skill(repo, u1, "技能A")
        b = _make_skill(repo, u1, "技能B")
        repo.set_enabled(ws1.workspace_id, u1, [a["skill_id"], b["skill_id"]])
        repo.set_enabled(ws2.workspace_id, u1, [a["skill_id"]])
        assert repo.delete(a["skill_id"], u1) is True
        assert [s["name"] for s in repo.list_enabled_for_workspace(ws1.workspace_id, u1)] == ["技能B"]
        assert repo.list_enabled_for_workspace(ws2.workspace_id, u1) == []
        # 无孤儿绑定行
        rows = db.conn.execute("SELECT * FROM workspace_writing_skills").fetchall()
        orphan = [r for r in rows if r["skill_id"] == a["skill_id"]]
        assert orphan == []

    def test_delete_rejects_other_owner(self, repo, two_users):
        u1, u2 = two_users
        sk = _make_skill(repo, u1)
        assert repo.delete(sk["skill_id"], u2) is False
        assert repo.get(sk["skill_id"], u1) is not None

    def test_delete_missing_returns_false(self, repo, two_users):
        u1, _ = two_users
        assert repo.delete("nonexistent", u1) is False
