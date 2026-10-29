"""用户自定义写作技能：多段注入（REQ-20261025-205943 FR-003，AC-004）。

锁定服务端硬闸：仅五段制工作区且当前段 ∈ {outline, volume, chapter,
text} 时，把归属该段的启用技能拼进 styles["author"]（附两条声明：低于
表达铁律＋不突破产物结构约定，DEC-008）；题材段/legacy/他段技能/
未启用/异常一律不注入。最终 system prompt 的 suffix 拼接链路由
architecture._system_prompt 既有逻辑保证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domains.writing.agent import MetaAgentService
from app.platform.core.db import (
    Database,
    UserRepository,
    WorkspaceRepository,
    WritingSkillRepository,
    init_database,
)
from app.platform.core.security import generate_master_key, load_master_key
from contracts.writing_artifacts import save_writing_state, WritingState


@pytest.fixture()
def db(tmp_path) -> Database:
    d = Database(tmp_path / "app.platform.core.db", load_master_key(generate_master_key()))
    init_database(d)
    return d


@pytest.fixture()
def seeded(db, tmp_path):
    """u1 + 一本书（真 DB 行 + five 目录，段指针 text）+ 2 条启用技能。"""
    u1 = UserRepository(db).create(username="alice", password="pw123456")["user_id"]
    ws = WorkspaceRepository(db).create(owner_id=u1, title="仙侠")
    ws_id = ws["workspace_id"]

    ws_path = tmp_path / "workspace" / u1 / ws_id
    ws_path.mkdir(parents=True)
    save_writing_state(ws_path, WritingState(confirmed_stage="text", stage="text"))

    repo = WritingSkillRepository(db)
    ids = [
        repo.create(owner_id=u1, name="武侠打斗", description="招式与节奏",
                    content="打斗先写招式名，再写身体反应。")["skill_id"],
        repo.create(owner_id=u1, name="爽点节奏", description="三章一小爽",
                    content="每三章安排一个期待感钩子。")["skill_id"],
    ]
    repo.set_enabled(ws_id, u1, ids)
    return u1, ws_id, ws_path, db


@pytest.fixture()
def svc() -> MetaAgentService:
    # 注入逻辑不依赖实例状态（DB 走全局单例、段指针走文件系统），
    # 跳过重型 __init__ 直接测方法。
    return MetaAgentService.__new__(MetaAgentService)


def _compose(svc, seeded):
    u1, ws_id, ws_path, _ = seeded
    return svc._compose_user_writing_skills(ws_id, u1, ws_path)


# ── 多段注入（FR-003 / AC-004）──────────────────────────────

class TestCompose:
    def test_text_stage_injects_with_both_statements(self, svc, seeded):
        text = _compose(svc, seeded)
        assert text is not None
        assert "打斗先写招式名" in text
        assert "每三章安排一个期待感钩子" in text
        # DEC-008：两条声明——低于表达铁律＋不突破产物结构约定
        assert "表达铁律" in text
        assert "产物结构" in text

    def test_theme_stage_injects_nothing(self, svc, seeded):
        """题材段零注入（AC-004 防绕过：启用列表非空也拦）。"""
        u1, ws_id, ws_path, _ = seeded
        save_writing_state(ws_path, WritingState(confirmed_stage="theme", stage="theme"))
        assert svc._compose_user_writing_skills(ws_id, u1, ws_path) is None

    @pytest.mark.parametrize("stage", ["outline", "volume", "chapter", "text"])
    def test_stage_injects_only_own_stage_skills(self, svc, seeded, db, stage):
        """当前段只注入归属该段的技能；他段技能即使已启用也不进提示词。"""
        u1, ws_id, ws_path, _ = seeded
        repo = WritingSkillRepository(db)
        own = repo.create(
            owner_id=u1, name=f"{stage}技", description="本段专属",
            content=f"【{stage}专属指令】", stage=stage,
        )["skill_id"]
        enabled = [s["skill_id"] for s in repo.list_enabled_for_workspace(ws_id, u1)]
        repo.set_enabled(ws_id, u1, enabled + [own])
        save_writing_state(ws_path, WritingState(confirmed_stage=stage, stage=stage))
        text = svc._compose_user_writing_skills(ws_id, u1, ws_path)
        assert text is not None
        assert f"【{stage}专属指令】" in text
        if stage != "text":
            assert "打斗先写招式名" not in text  # text 段技能不在大纲段注入

    def test_no_enabled_skills_returns_none(self, svc, seeded):
        u1, ws_id, ws_path, db = seeded
        WritingSkillRepository(db).set_enabled(ws_id, u1, [])
        assert _compose(svc, seeded) is None

    def test_legacy_workspace_returns_none(self, svc, seeded):
        u1, ws_id, ws_path, _ = seeded
        (ws_path / "storyline.md").write_text("旧工作区", encoding="utf-8")
        assert _compose(svc, seeded) is None

    def test_missing_ids_return_none(self, svc, seeded):
        _, ws_id, ws_path, _ = seeded
        assert svc._compose_user_writing_skills(None, "u", ws_path) is None
        assert svc._compose_user_writing_skills(ws_id, None, ws_path) is None


# ── styles 合成与兜底（FR-003 失败语义）────────────────────

class TestApply:
    def test_appends_after_stage_prompt(self, svc, seeded):
        u1, ws_id, ws_path, _ = seeded
        styles = {"author": "段规程正文"}
        out = svc._apply_user_writing_skills(
            styles=styles, agent_name="author", workspace_id=ws_id,
            owner_id=u1, workspace_path=ws_path,
        )
        assert out["author"].startswith("段规程正文\n\n")
        assert "打斗先写招式名" in out["author"]

    def test_non_author_agent_untouched(self, svc, seeded):
        u1, ws_id, ws_path, _ = seeded
        styles = {"author": "段规程正文"}
        out = svc._apply_user_writing_skills(
            styles=styles, agent_name=None, workspace_id=ws_id,
            owner_id=u1, workspace_path=ws_path,
        )
        assert out == {"author": "段规程正文"}

    def test_db_failure_skips_injection(self, svc, seeded, monkeypatch):
        u1, ws_id, ws_path, _ = seeded
        import app.domains.writing.agent as agent_mod
        monkeypatch.setattr(
            "app.platform.core.db.get_database", lambda: (_ for _ in ()).throw(RuntimeError("db down"))
        )
        styles = {"author": "段规程正文"}
        out = svc._apply_user_writing_skills(
            styles=styles, agent_name="author", workspace_id=ws_id,
            owner_id=u1, workspace_path=ws_path,
        )
        assert out == {"author": "段规程正文"}


# ── 审稿约束注入 styles["review"]（REQ-20261025-230811 FR-005 / AC-004）──

class TestReviewConstraints:
    def test_text_stage_injects_review_key(self, svc, seeded):
        """正文段：激活技能同时进 styles["review"]，声明为不可违反的审查约束。"""
        u1, ws_id, ws_path, _ = seeded
        out = svc._apply_user_writing_skills(
            styles={"author": "段规程正文"}, agent_name="author",
            workspace_id=ws_id, owner_id=u1, workspace_path=ws_path,
        )
        assert "review" in out
        assert "打斗先写招式名" in out["review"]
        assert "不得将其判为问题" in out["review"]
        # author 键照旧（段规程 + 技能）
        assert "打斗先写招式名" in out["author"]

    def test_non_text_stage_no_review_key(self, svc, seeded):
        """非正文段：无审稿注入（review 子代理只在正文段被委托）。"""
        u1, ws_id, ws_path, _ = seeded
        save_writing_state(ws_path, WritingState(confirmed_stage="chapter", stage="chapter"))
        out = svc._apply_user_writing_skills(
            styles={"author": "段规程正文"}, agent_name="author",
            workspace_id=ws_id, owner_id=u1, workspace_path=ws_path,
        )
        assert "review" not in out


    def test_no_skills_no_review_key(self, svc, seeded):
        """正文段但无启用技能：review 键不注入。"""
        u1, ws_id, ws_path, db = seeded
        WritingSkillRepository(db).set_enabled(ws_id, u1, [])
        out = svc._apply_user_writing_skills(
            styles={"author": "段规程正文"}, agent_name="author",
            workspace_id=ws_id, owner_id=u1, workspace_path=ws_path,
        )
        assert "review" not in out


class TestVolumeConstraintSnapshot:
    def test_chapter_write_uses_volume_skills_without_injecting_text_skills(self, svc, seeded):
        u1, ws_id, ws_path, db = seeded
        repo = WritingSkillRepository(db)
        volume = repo.create(owner_id=u1, name="闭合定义", description="范围", content="清单内条目", stage="volume")["skill_id"]
        repo.set_enabled(ws_id, u1, [volume])
        save_writing_state(ws_path, WritingState(confirmed_stage="chapter", stage="chapter"))
        skills = svc._list_volume_constraint_skills(ws_id, u1, ws_path)
        assert [skill["skill_id"] for skill in skills] == [volume]
        assert svc._compose_user_writing_skills(ws_id, u1, ws_path) is None

    def test_volume_prompt_uses_the_frozen_skill_snapshot(self, svc, seeded, monkeypatch):
        u1, ws_id, ws_path, _ = seeded
        save_writing_state(ws_path, WritingState(confirmed_stage="volume", stage="volume"))
        snapshot = [{"skill_id": "frozen", "name": "定义", "description": "来源", "content": "冻结的原文"}]
        monkeypatch.setattr(svc, "_list_enabled_stage_skills", lambda *args: (_ for _ in ()).throw(AssertionError("must not re-read")))
        text = svc._compose_user_writing_skills(ws_id, u1, ws_path, skills=snapshot)
        assert "冻结的原文" in text

    def test_explicit_rejection_and_resume_share_server_bound_revision(self, svc, seeded):
        u1, ws_id, ws_path, db = seeded
        save_writing_state(ws_path, WritingState(confirmed_stage="volume", stage="volume"))
        repo = WritingSkillRepository(db)
        skill = repo.create(owner_id=u1, name="清单", description="定义", content="已有条目", stage="volume")["skill_id"]
        repo.set_enabled(ws_id, u1, [skill])
        svc.trace_recorder = None
        original, _ = svc._volume_payoff_runtime(ws_path, ws_id, u1, "same-trace", object())
        revised, _ = svc._volume_payoff_runtime(
            ws_path, ws_id, u1, "same-trace", object(), message_id="author-message", new_revision=True,
        )
        resumed, _ = svc._volume_payoff_runtime(
            ws_path, ws_id, u1, "same-trace", object(), message_id="ordinary-answer",
        )
        retried, _ = svc._volume_payoff_runtime(
            ws_path, ws_id, u1, "same-trace", object(), message_id="author-message", new_revision=True,
        )
        assert original.operation_id != revised.operation_id
        assert revised.operation_id == resumed.operation_id == retried.operation_id

    def test_chapter_rejection_does_not_create_a_pending_volume_task(self, svc, seeded):
        from contracts.writing_artifacts import volume_review_block_reason

        u1, ws_id, ws_path, db = seeded
        repo = WritingSkillRepository(db)
        skill = repo.create(owner_id=u1, name="清单", description="定义", content="已有条目", stage="volume")["skill_id"]
        repo.set_enabled(ws_id, u1, [skill])
        save_writing_state(ws_path, WritingState(confirmed_stage="chapter", stage="chapter"))
        svc.trace_recorder = None
        svc._volume_payoff_runtime(
            ws_path, ws_id, u1, "chapter-only", object(), message_id="reject-chapter", new_revision=True,
        )
        assert volume_review_block_reason(ws_path, operation_id="chapter-only") is None
