"""账号全局技能：旧清单迁移、分阶段确认及失败保护。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.platform.core.db import (
    Database, UserRepository, WorkspaceRepository, WritingSkillRepository,
)
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.state.thread_store import ThreadStore


@pytest.fixture()
def setup(tmp_path):
    key = load_master_key(generate_master_key())
    db = Database(tmp_path / "skills.db", key)
    owner = UserRepository(db).create(username="alice", password="pw123456")["user_id"]
    other = UserRepository(db).create(username="bob", password="pw123456")["user_id"]
    workspaces = [WorkspaceRepository(db).create(owner_id=owner, title=name)["workspace_id"]
                  for name in ("甲", "乙")]
    yield db, WritingSkillRepository(db), owner, other, workspaces, key
    db.close()


def make(repo, owner, name, stage="text"):
    return repo.create(owner_id=owner, name=name, description="说明", content="正文", stage=stage)["skill_id"]


def ids(skills):
    return {skill["skill_id"] for skill in skills}


def seed_overflow(repo, owner, workspaces):
    skills = [make(repo, owner, f"正文{i}") for i in range(6)]
    outline = make(repo, owner, "总纲", "outline")
    repo.set_enabled(workspaces[0], owner, skills[:5] + [outline])
    repo.set_enabled(workspaces[1], owner, skills[1:])
    return skills, outline


def test_merge_deduplicates_identity_but_keeps_same_named_skills(setup):
    db, repo, owner, _, workspaces, _ = setup
    a, b, c = [make(repo, owner, "同名") for _ in range(3)]
    repo.set_enabled(workspaces[0], owner, [a, b])
    repo.set_enabled(workspaces[1], owner, [b, c])

    state = repo.get_global_state(owner)

    assert ids(state["enabled"]) == {a, b, c}
    assert state["pending_stages"] == []
    assert state["candidates"] == []
    assert db.conn.execute("SELECT COUNT(*) FROM workspace_writing_skills").fetchone()[0] == 4


def test_overflow_remains_candidates_without_disabling_other_stages(setup):
    _, repo, owner, _, workspaces, _ = setup
    skills, outline = seed_overflow(repo, owner, workspaces)

    state = repo.get_global_state(owner)

    assert state["pending_stages"] == ["text"]
    assert ids(state["candidates"]) == set(skills)
    assert ids(state["enabled"]) == {outline}
    assert ids(repo.list_enabled_for_owner(owner)) == {outline}


def test_confirm_only_one_stage_and_allow_explicit_empty_selection(setup):
    _, repo, owner, _, workspaces, _ = setup
    skills, outline = seed_overflow(repo, owner, workspaces)
    repo.get_global_state(owner)

    state = repo.set_enabled_for_stage(owner, "outline", [])
    assert state["pending_stages"] == ["text"]
    assert ids(state["candidates"]) == set(skills)
    state = repo.set_enabled_for_stage(owner, "text", [])
    assert state == {"enabled": [], "pending_stages": [], "candidates": []}
    assert repo.get_global_state(owner) == state
    assert repo.get(outline, owner) is not None


def test_confirm_overflow_five_and_persist_across_reopen(setup):
    db, repo, owner, _, workspaces, key = setup
    skills, outline = seed_overflow(repo, owner, workspaces)
    state = repo.set_enabled_for_stage(owner, "text", skills[:5])
    assert ids(state["enabled"]) == set(skills[:5] + [outline])
    assert state["pending_stages"] == []
    assert state["candidates"] == []

    reopened = Database(db.db_path, key)
    try:
        assert WritingSkillRepository(reopened).get_global_state(owner) == state
    finally:
        reopened.close()


def test_initial_empty_configuration_does_not_import_later_legacy_writes(setup):
    _, repo, owner, _, workspaces, _ = setup
    assert repo.get_global_state(owner)["enabled"] == []
    skill = make(repo, owner, "后写入旧表")
    repo.set_enabled(workspaces[0], owner, [skill])
    assert repo.get_global_state(owner) == {"enabled": [], "pending_stages": [], "candidates": []}


@pytest.mark.parametrize("invalid", ["six", "duplicate", "foreign", "wrong_stage", "bad_stage"])
def test_invalid_save_preserves_confirmed_and_pending_state(setup, invalid):
    _, repo, owner, other, workspaces, _ = setup
    skills, outline = seed_overflow(repo, owner, workspaces)
    state = repo.get_global_state(owner)
    stage = "text"
    selection = skills
    if invalid == "duplicate":
        selection = [skills[0], skills[0]]
    elif invalid == "foreign":
        selection = [make(repo, other, "别人的技能")]
    elif invalid == "wrong_stage":
        selection = [outline]
    elif invalid == "bad_stage":
        stage, selection = "theme", []

    with pytest.raises(ValueError):
        repo.set_enabled_for_stage(owner, stage, selection)
    assert repo.get_global_state(owner) == state


def test_database_save_failure_rolls_back_selection(setup):
    db, repo, owner, _, _, _ = setup
    a, b = [make(repo, owner, name) for name in ("A", "B")]
    state = repo.set_enabled_for_stage(owner, "text", [a])
    db.conn.execute("CREATE TRIGGER reject_global_save BEFORE INSERT ON user_writing_skills "
                    "BEGIN SELECT RAISE(ABORT, 'test save failure'); END")

    with pytest.raises(sqlite3.IntegrityError):
        repo.set_enabled_for_stage(owner, "text", [b])
    assert repo.get_global_state(owner) == state


def test_migration_failure_rolls_back_and_can_retry(setup):
    db, repo, owner, _, workspaces, _ = setup
    skill = make(repo, owner, "旧配置")
    repo.set_enabled(workspaces[0], owner, [skill])
    db.conn.execute("CREATE TRIGGER reject_global_migration BEFORE INSERT ON user_writing_skills "
                    "BEGIN SELECT RAISE(ABORT, 'test migration failure'); END")

    with pytest.raises(sqlite3.IntegrityError):
        repo.get_global_state(owner)
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skill_stages").fetchone()[0] == 0
    assert repo.list_enabled_for_workspace(workspaces[0], owner)[0]["skill_id"] == skill
    db.conn.execute("DROP TRIGGER reject_global_migration")
    assert ids(repo.get_global_state(owner)["enabled"]) == {skill}


def test_owner_isolation_and_delete_cleans_global_bindings(setup):
    _, repo, owner, other, workspaces, _ = setup
    skills, outline = seed_overflow(repo, owner, workspaces)
    repo.get_global_state(owner)
    assert repo.get_global_state(other) == {"enabled": [], "pending_stages": [], "candidates": []}
    assert repo.delete(skills[0], other) is False
    assert repo.delete(skills[0], owner) is True
    state = repo.get_global_state(owner)
    assert state["pending_stages"] == ["text"]
    assert ids(state["candidates"]) == set(skills[1:])
    repo.delete(outline, owner)
    assert repo.get_global_state(owner)["enabled"] == []


def test_stage_edit_cannot_overflow_global_enabled_selection(setup):
    _, repo, owner, _, _, _ = setup
    outlines = [make(repo, owner, f"纲{i}", "outline") for i in range(5)]
    text = make(repo, owner, "正文")
    repo.set_enabled_for_stage(owner, "outline", outlines)
    repo.set_enabled_for_stage(owner, "text", [text])

    with pytest.raises(ValueError):
        repo.update(text, owner, name="不可改名", stage="outline")
    assert repo.get(text, owner)["stage"] == "text"
    assert repo.get(text, owner)["name"] == "正文"


def test_moving_candidate_to_ready_stage_does_not_silently_enable_it(setup):
    _, repo, owner, _, workspaces, _ = setup
    skills, outline = seed_overflow(repo, owner, workspaces)
    repo.get_global_state(owner)
    repo.update(skills[0], owner, stage="outline")

    state = repo.get_global_state(owner)
    assert ids(state["enabled"]) == {outline}
    assert state["pending_stages"] == ["text"]
    assert ids(state["candidates"]) == set(skills[1:])


def test_enabled_skill_moved_into_pending_stage_requires_explicit_confirmation(setup):
    _, repo, owner, _, workspaces, _ = setup
    skills, outline = seed_overflow(repo, owner, workspaces)
    repo.get_global_state(owner)
    repo.update(outline, owner, stage="text")

    state = repo.get_global_state(owner)
    assert state["enabled"] == []
    assert ids(state["candidates"]) == set(skills + [outline])
    assert state["pending_stages"] == ["text"]


def test_delete_workspace_before_first_global_read_preserves_all_legacy_choices(setup):
    db, repo, owner, _, workspaces, _ = setup
    skills, outline = seed_overflow(repo, owner, workspaces)

    assert WorkspaceRepository(db).delete(workspaces[0], owner) is True

    state = repo.get_global_state(owner)
    assert ids(state["enabled"]) == {outline}
    assert state["pending_stages"] == ["text"]
    assert ids(state["candidates"]) == set(skills)
    assert repo.list_enabled_for_workspace(workspaces[0], owner) == []
    assert WorkspaceRepository(db).delete(workspaces[1], owner) is True
    assert repo.get_global_state(owner) == state


@pytest.mark.parametrize("target", ["missing", "foreign"])
def test_rejected_workspace_delete_does_not_initialize_global_state(setup, target):
    db, _, owner, other, workspaces, _ = setup
    workspace_id, actor = ("missing", owner) if target == "missing" else (workspaces[0], other)

    assert WorkspaceRepository(db).delete(workspace_id, actor) is False
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skill_stages").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skills").fetchone()[0] == 0


def test_workspace_delete_migration_failure_keeps_workspace_and_legacy_choices(setup):
    db, repo, owner, _, workspaces, _ = setup
    skill = make(repo, owner, "删除前需迁移")
    repo.set_enabled(workspaces[0], owner, [skill])
    db.conn.execute("CREATE TRIGGER reject_delete_migration BEFORE INSERT ON user_writing_skills "
                    "BEGIN SELECT RAISE(ABORT, 'test migration failure'); END")

    with pytest.raises(sqlite3.IntegrityError):
        WorkspaceRepository(db).delete(workspaces[0], owner)
    assert WorkspaceRepository(db).get(workspaces[0], owner) is not None
    assert ids(repo.list_enabled_for_workspace(workspaces[0], owner)) == {skill}
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skill_stages").fetchone()[0] == 0


def test_rollback_legacy_skill_delete_needs_no_new_table_cleanup(setup):
    db, repo, owner, _, workspaces, _ = setup
    skill = make(repo, owner, "已迁移技能")
    repo.set_enabled(workspaces[0], owner, [skill])
    repo.get_global_state(owner)

    # 回滚后的旧代码只知道旧绑定表和技能表。
    with db.transaction() as conn:
        conn.execute("DELETE FROM workspace_writing_skills WHERE skill_id = ? AND owner_id = ?",
                     (skill, owner))
        conn.execute("DELETE FROM writing_skills WHERE skill_id = ? AND owner_id = ?",
                     (skill, owner))
    assert repo.get_global_state(owner)["enabled"] == []
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skills").fetchone()[0] == 0


def test_rollback_legacy_owner_delete_needs_no_new_table_cleanup(setup):
    db, repo, _, other, _, _ = setup
    skill = make(repo, other, "待删账号的技能")
    repo.set_enabled_for_stage(other, "text", [skill])

    with db.transaction() as conn:
        conn.execute("DELETE FROM writing_skills WHERE owner_id = ?", (other,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (other,))
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skill_stages WHERE owner_id = ?",
                           (other,)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skills WHERE owner_id = ?",
                           (other,)).fetchone()[0] == 0


def test_service_delete_keeps_files_and_database_when_skill_migration_fails(setup, tmp_path):
    db, repo, owner, _, _, _ = setup
    store = ThreadStore(db, tmp_path / "workspace")
    workspace = store.create_workspace(owner, "迁移失败要保留")
    thread = store.create_thread(owner, workspace.workspace_id, "待保留对话")
    artifact = Path(workspace.workspace_path) / "chapter.md"
    artifact.write_text("已写好的正文", encoding="utf-8")
    skill = make(repo, owner, "待迁移启用技能")
    repo.set_enabled(workspace.workspace_id, owner, [skill])
    db.conn.execute("CREATE TRIGGER reject_service_migration BEFORE INSERT ON user_writing_skills "
                    "BEGIN SELECT RAISE(ABORT, 'test migration failure'); END")

    with pytest.raises(sqlite3.IntegrityError):
        store.delete_workspace(owner, workspace.workspace_id)

    assert artifact.read_text(encoding="utf-8") == "已写好的正文"
    assert store.get_workspace(owner, workspace.workspace_id) is not None
    assert store.get_thread(owner, thread.thread_id) is not None
    assert ids(repo.list_enabled_for_workspace(workspace.workspace_id, owner)) == {skill}
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skill_stages").fetchone()[0] == 0


def test_service_delete_cleans_files_and_children_after_preserving_global_skills(setup, tmp_path):
    db, repo, owner, _, _, _ = setup
    store = ThreadStore(db, tmp_path / "workspace")
    workspace = store.create_workspace(owner, "正常删除")
    thread = store.create_thread(owner, workspace.workspace_id, "待删除对话")
    artifact = Path(workspace.workspace_path) / "chapter.md"
    artifact.write_text("待删除正文", encoding="utf-8")
    skill = make(repo, owner, "全局保留的技能")
    repo.set_enabled(workspace.workspace_id, owner, [skill])

    assert store.delete_workspace(owner, workspace.workspace_id) == [thread.thread_id]

    assert not Path(workspace.workspace_path).exists()
    assert store.get_workspace(owner, workspace.workspace_id) is None
    assert store.get_thread(owner, thread.thread_id) is None
    assert repo.list_enabled_for_workspace(workspace.workspace_id, owner) == []
    assert ids(repo.list_enabled_for_owner(owner)) == {skill}


@pytest.mark.parametrize("target", ["missing", "foreign"])
def test_service_rejected_delete_keeps_files_and_does_not_migrate(setup, tmp_path, target):
    db, _, owner, other, _, _ = setup
    store = ThreadStore(db, tmp_path / "workspace")
    workspace = store.create_workspace(owner, "无权不能删")
    original = Path(workspace.workspace_path) / "chapter.md"
    original.write_text("作者的正文", encoding="utf-8")
    workspace_id, actor = ("missing", owner) if target == "missing" else (workspace.workspace_id, other)
    target_path = store._ws_path(actor, workspace_id)
    target_path.mkdir(parents=True)
    sentinel = target_path / "keep.md"
    sentinel.write_text("未授权删除目录", encoding="utf-8")

    assert store.delete_workspace(actor, workspace_id) is None

    assert original.read_text(encoding="utf-8") == "作者的正文"
    assert sentinel.read_text(encoding="utf-8") == "未授权删除目录"
    assert store.get_workspace(owner, workspace.workspace_id) is not None
    assert db.conn.execute("SELECT COUNT(*) FROM user_writing_skill_stages").fetchone()[0] == 0
