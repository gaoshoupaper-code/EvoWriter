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


# ── 阶段归属（REQ-20261025-205943 FR-001 / AC-001/002）───────

class TestStageField:
    def test_create_with_stage_roundtrip(self, repo, two_users):
        u1, _ = two_users
        sk = repo.create(
            owner_id=u1, name="伏笔编织", description="总纲段用",
            content="埋线要记回收位置。", stage="outline",
        )
        assert sk["stage"] == "outline"
        assert repo.get(sk["skill_id"], u1)["stage"] == "outline"

    def test_create_defaults_to_text(self, repo, two_users):
        u1, _ = two_users
        sk = _make_skill(repo, u1)
        assert sk["stage"] == "text"

    def test_stage_survives_enable_binding(self, repo, thread_store, two_users):
        u1, _ = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        sk = repo.create(
            owner_id=u1, name="卷节奏", description="卷纲段用",
            content="三幕推进。", stage="volume",
        )
        repo.set_enabled(ws.workspace_id, u1, [sk["skill_id"]])
        enabled = repo.list_enabled_for_workspace(ws.workspace_id, u1)
        assert enabled[0]["stage"] == "volume"

    def test_update_stage(self, repo, two_users):
        u1, _ = two_users
        sk = _make_skill(repo, u1)
        updated = repo.update(sk["skill_id"], u1, stage="chapter")
        assert updated["stage"] == "chapter"


# ── 旧库迁移：存量技能归正文段（REQ-20261025-205943 DEC-004 / AC-002）──

class TestStageMigration:
    def test_legacy_rows_default_to_text(self, tmp_path):
        """模拟无 stage 列的旧库：重开后自动补列，存量行归 'text'（DEC-004）。"""
        from app.platform.core.security import generate_master_key, load_master_key

        key = load_master_key(generate_master_key())
        db_path = tmp_path / "legacy.db"
        d1 = Database(db_path, key)
        owner = UserRepository(d1).create(username="alice", password="pw123456")
        # 旧库没有 stage 列：手工剥掉后插一行「升级前」的数据
        d1.conn.execute("ALTER TABLE writing_skills DROP COLUMN stage")
        d1.conn.execute(
            "INSERT INTO writing_skills (skill_id, owner_id, name, description, "
            "content, created_at, updated_at) VALUES "
            "('legacy1', ?, '旧技能', 'x', 'y', '2026-01-01', '2026-01-01')",
            (owner["user_id"],),
        )
        d1.conn.close()

        d2 = Database(db_path, key)  # _migrate 补列，DEFAULT 'text' 回填旧行
        row = d2.conn.execute(
            "SELECT stage FROM writing_skills WHERE skill_id = 'legacy1'"
        ).fetchone()
        assert row["stage"] == "text"
        d2.conn.close()


# ── 每段启用上限（REQ-20261025-205943 FR-002 / DEC-005 / AC-003）──

class TestPerStageLimit:
    def _make(self, repo, owner, name, stage):
        return repo.create(
            owner_id=owner, name=name, description="d", content="c", stage=stage,
        )["skill_id"]

    def test_six_same_stage_rejected_five_ok(self, repo, thread_store, two_users):
        u1, _ = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        ids = [self._make(repo, u1, f"总纲技{i}", "outline") for i in range(6)]
        with pytest.raises(ValueError):
            repo.set_enabled(ws.workspace_id, u1, ids)
        repo.set_enabled(ws.workspace_id, u1, ids[:5])  # 恰好 5 条合法
        assert len(repo.list_enabled_for_workspace(ws.workspace_id, u1)) == 5

    def test_mixed_stages_beyond_five_total_ok(self, repo, thread_store, two_users):
        """旧「全书 ≤5」口径已废（DEC-005）：跨段合计 6 条合法。"""
        u1, _ = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        ids = [self._make(repo, u1, f"总纲技{i}", "outline") for i in range(3)]
        ids += [self._make(repo, u1, f"正文技{i}", "text") for i in range(3)]
        repo.set_enabled(ws.workspace_id, u1, ids)
        assert len(repo.list_enabled_for_workspace(ws.workspace_id, u1)) == 6

    def test_twenty_across_four_stages_ok(self, repo, thread_store, two_users):
        u1, _ = two_users
        ws = thread_store.create_workspace(u1, "仙侠")
        ids = []
        for stage in ("outline", "volume", "chapter", "text"):
            ids += [self._make(repo, u1, f"{stage}{i}", stage) for i in range(5)]
        repo.set_enabled(ws.workspace_id, u1, ids)
        assert len(repo.list_enabled_for_workspace(ws.workspace_id, u1)) == 20
