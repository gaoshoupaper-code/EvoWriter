import pytest

from contracts.target_tasks import TargetSpec, TargetTaskRequest, TaskInput
from app.domains.writing.target_tasks import (
    _next_step, _scoped_outputs, completion_checks, materialize_input, validate_input,
)


def task(kind, files, **scope):
    return TargetTaskRequest(task_id="sample", attempt_id="sample-a1", source_commit="a" * 40,
        target=TargetSpec(task_type=kind, **scope),
        input=TaskInput(files=files, demand_md="固定任务", confirmation_rules="独立创作"))


@pytest.mark.parametrize(("agent", "kind", "files", "scope", "stage"), [
    ("outline", "master_outline", {}, {}, "outline"),
    ("volume", "volume_outlines", {"总纲.md": "草稿总纲"}, {"volume_ids": [2]}, "volume"),
    ("chapter", "chapter_outlines", {"卷纲/第02卷.md": "卷纲草稿"}, {"volume_ids": [2], "chapter": 21}, "chapter"),
    ("text", "chapter_text", {"章纲/第002章.md": "草稿"}, {"chapter": 2}, "text"),
])
def test_target_starts_in_its_own_harness(tmp_path, monkeypatch, agent, kind, files, scope, stage):
    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    request = task(kind, files, **scope)
    validate_input(request.target, request.input)
    materialize_input(tmp_path, request.input)
    assert _next_step(request.target, tmp_path)[0] == stage
    if files:
        removed = next(iter(files))
        incomplete = request.input.model_copy(update={"files": {k: v for k, v in files.items() if k != removed}})
        with pytest.raises(ValueError, match="缺少必需资料"):
            validate_input(request.target, incomplete)


def test_master_outline_requires_theme_and_cannot_rewrite_downstream(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_ID", "outline")
    request = task("master_outline", {})
    header = "# 总纲\n> 状态：定稿\n"
    master = tmp_path / "总纲.md"
    master.write_text(header + "## 主题\n## 主线\n冲突", encoding="utf-8")
    assert not completion_checks(request.target, tmp_path)["complete"]
    master.write_text(header + "## 主题\n选择的代价\n## 主线\n冲突", encoding="utf-8")
    assert completion_checks(request.target, tmp_path)["complete"]
    assert _next_step(request.target, tmp_path) is None
    artifacts = {"demand.md": "固定任务", "总纲.md": master.read_text(encoding="utf-8")}
    assert set(_scoped_outputs(request, artifacts)) == {"总纲.md"}
    with pytest.raises(ValueError):
        _scoped_outputs(request, {**artifacts, "卷纲/第01卷.md": "越界"})


def test_text_target_cannot_change_frozen_character_backstory(monkeypatch):
    from app.domains.writing.creative_scope import _DYNAMIC
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    card = "人物/主角.md"
    fixed = {card: "# 主角\n原籍临河", "章纲/第001章.md": "章纲", "文风.md": "文风"}
    request = task("chapter_text", fixed, chapter=1, volume_ids=[1])
    written = {**fixed, "demand.md": "固定任务", "正文/第001章.md": "新正文",
               card: fixed[card] + _DYNAMIC + "## 人物现状\n- 阵营：独立\n## 变化记录\n- 第1章：退出船队"}
    with pytest.raises(ValueError, match="保存记录"):
        _scoped_outputs(request, written)
    evidence = {"card_writes": {card: True}}
    assert set(_scoped_outputs(request, written, evidence)) == {card, "正文/第001章.md"}
    written[card] = written[card].replace("临河", "山城")
    with pytest.raises(ValueError, match="原始设定"):
        _scoped_outputs(request, written, evidence)


@pytest.mark.parametrize("changed_input", [False, True])
def test_result_only_discloses_checked_local_drafts(tmp_path, monkeypatch, changed_input):
    from app.domains.writing.target_tasks import TargetTaskManager

    monkeypatch.setenv("WRITING_AGENT_ID", "volume")
    request = task("volume_outlines", {"总纲.md": "上游总纲"}, volume_ids=[1])
    workspace = tmp_path / "workspace"
    materialize_input(workspace, request.input)
    volume = "卷纲/第01卷.md"
    (workspace / "卷纲").mkdir()
    (workspace / volume).write_text("本套草稿", encoding="utf-8")
    if changed_input:
        (workspace / "总纲.md").write_text("变化的上游输入", encoding="utf-8")
    manager = TargetTaskManager(tmp_path / "jobs")
    manager.store.create(request, metadata={"workspace": str(workspace)})
    manager.store.update(request.task_id, status="running", artifacts=dict(request.input.files), checks={"unfinished": True})
    progress = manager.result(request.task_id)
    assert progress.artifacts == {} and "unfinished" not in progress.checks
    manager.store.update(request.task_id, status="failed", error="未定稿")
    result = manager.verify_result(request.task_id, manager.store.trusted(request.task_id))
    assert result.error == "未定稿"
    assert result.artifacts == ({} if changed_input else {volume: "本套草稿"})
