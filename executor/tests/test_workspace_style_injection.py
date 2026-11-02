"""Whole-card runtime snapshot shared by author and review (AC-006/008)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.writing.agent import MetaAgentService
from app.platform.agent.loader import load_package
from contracts.writing_artifacts import WritingState, save_writing_state

ROOT = Path(__file__).resolve().parents[2]
CARD = "# 风格\n短句，冷静口吻。\n\n保留完整样章。\n"


@pytest.fixture()
def context(tmp_path):
    save_writing_state(tmp_path, WritingState(stage="text", confirmed_stage="text"))
    (tmp_path / "文风.md").write_bytes(CARD.encode())
    return MetaAgentService.__new__(MetaAgentService), tmp_path, load_package(ROOT / "evolution/harnesses/repo")


def apply(context, styles=None, **extra):
    svc, ws, pkg = context
    return svc._apply_workspace_style_card(styles=styles, agent_name="author", workspace_path=ws, package=pkg, **extra)


def test_author_and_reviewer_receive_full_same_card_after_skills(context):
    original = {"author": "段规程\n技能要求长句、热烈口吻", "review": "技能审稿约束"}
    result = apply(context, original)
    assert original["author"] == "段规程\n技能要求长句、热烈口吻"
    for scope in ("author", "review"):
        assert CARD in result[scope]
        assert "文风优先" in result[scope]
        assert "章纲" in result[scope]
        assert "权限" in result[scope]
    assert result["author"].split("## 当前作品文风", 1)[1] == result["review"].split("## 当前作品文风", 1)[1]


def test_no_enabled_skills_still_injects_review_and_author(context):
    result = apply(context)
    assert CARD in result["review"] and CARD in result["author"]


@pytest.mark.parametrize("stage", ["theme", "outline", "volume", "chapter"])
def test_other_stages_keep_existing_behavior(context, stage):
    svc, ws, _ = context
    save_writing_state(ws, WritingState(stage=stage, confirmed_stage=stage))
    original = {"author": "原规程"}
    assert apply(context, original) is original


def test_missing_card_keeps_existing_missing_style_workflow(context):
    _, ws, _ = context
    (ws / "文风.md").unlink()
    assert apply(context) is None


def test_read_failure_is_explicit_instead_of_silent_fallback(context, monkeypatch):
    original = Path.read_bytes

    def cannot_read(path):
        if path.name == "文风.md":
            raise PermissionError("unreadable card")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", cannot_read)
    with pytest.raises(RuntimeError, match="文风.*读取失败"):
        apply(context)


def test_missing_harness_capability_is_explicit(context):
    svc, ws, _ = context
    with pytest.raises(RuntimeError, match="harness"):
        svc._apply_workspace_style_card(styles=None, agent_name="author", workspace_path=ws, package=SimpleNamespace())


def test_snapshot_does_not_change_when_card_is_changed_later(context):
    _, ws, _ = context
    result = apply(context)
    (ws / "文风.md").write_text("另一份文风", encoding="utf-8")
    assert CARD in result["author"] and CARD in result["review"]


def test_style_suffix_reaches_actual_author_and_review_prompts(context):
    from app.platform.agent.architecture import AgentBuildCtx, _system_prompt
    from contracts.architecture_manifest import load_architecture_manifest
    from contracts.runtime_context import RuntimeContext

    _, ws, _ = context
    package_dir = ROOT / "evolution/harnesses/repo"
    manifest = load_architecture_manifest(package_dir)
    ctx = RuntimeContext(model=object(), backend=object(), checkpointer=None, workspace_path=ws, styles=apply(context))
    for name in ("author", "review"):
        prompt = _system_prompt(AgentBuildCtx(ctx, manifest, manifest.agent(name), "harness_current", package_dir))
        assert CARD in prompt
        assert "文风优先" in prompt


def test_real_assembly_uses_book_card_with_conflicting_account_skills(context, monkeypatch):
    import app.domains.writing.agent as module
    import app.platform.agent.loader as loader
    from app.platform.core.db import Database, UserRepository, WritingSkillRepository, init_database
    from app.platform.core.security import generate_master_key, load_master_key

    service, workspace, package = context
    db = Database(workspace / "assets.db", load_master_key(generate_master_key()))
    init_database(db)
    owner = UserRepository(db).create(username="assembly", password="password123")["user_id"]
    repo = WritingSkillRepository(db)
    skill = repo.create(owner_id=owner, name="相反技能", description="表达", content="长句、热烈口吻")
    repo.set_enabled_for_stage(owner, "text", [skill["skill_id"]])
    captured = []
    monkeypatch.setattr(loader, "load_current_package", lambda: package)
    monkeypatch.setattr(package, "assemble", lambda ctx, **kwargs: captured.append(ctx))
    monkeypatch.setattr(module, "get_memory_backend", lambda *args: None)
    monkeypatch.setattr(module, "_get_memory_recall_cls", lambda *args: None)
    monkeypatch.setattr(service, "_backend_for_workspace", lambda *args: object())
    monkeypatch.setattr(service, "_resolve_meta_style", lambda *args: None)
    monkeypatch.setattr(service, "_resolve_style_for_subagent", lambda *args: None)
    service.trace_recorder = SimpleNamespace()
    try:
        service._assemble_via_package(workspace, workspace_id="book", owner_id=owner, model=object(), checkpointer=object(), agent_name="author")
        for name in ("author", "review"):
            assert CARD in captured[0].styles[name]
            assert "长句、热烈口吻" in captured[0].styles[name]
            assert "文风优先" in captured[0].styles[name]
    finally:
        db.close()
