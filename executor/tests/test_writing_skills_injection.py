"""用户自定义写作技能：正文段注入（REQ-20261023-000000 FR-003，AC-003）。

锁定服务端硬闸：仅五段制工作区且当前段=text 时把启用技能拼进
styles["author"]（附「低于表达铁律」声明）；其他段/legacy/未启用/异常
一律原样不动。最终 system prompt 的 suffix 拼接链路由
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


# ── 仅正文段注入（FR-003 / AC-003）─────────────────────────

class TestCompose:
    def test_text_stage_injects_with_priority_statement(self, svc, seeded):
        text = _compose(svc, seeded)
        assert text is not None
        assert "打斗先写招式名" in text
        assert "每三章安排一个期待感钩子" in text
        # DEC-003：必须自带低于表达铁律的声明
        assert "表达铁律" in text

    @pytest.mark.parametrize("stage", ["theme", "outline", "volume", "chapter"])
    def test_non_text_stages_inject_nothing(self, svc, seeded, stage):
        u1, ws_id, ws_path, _ = seeded
        save_writing_state(ws_path, WritingState(confirmed_stage=stage, stage=stage))
        assert svc._compose_user_writing_skills(ws_id, u1, ws_path) is None

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
