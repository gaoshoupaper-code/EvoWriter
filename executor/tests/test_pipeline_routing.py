"""单主控三步管线编排层测试（REQ-20261015-161200 FR-002/003，DEC-005）。

路由 / 会话切换 / resume 定位 / legacy（五件套与旧七步）只读 / 阶段条数据。
"""
from __future__ import annotations

from pathlib import Path

from contracts.outline_pipeline import load_pipeline_state
from app.domains.writing import pipeline as P


def _mkfile(ws: Path, rel: str) -> None:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


def test_new_workspace_routes_worldview(tmp_path):
    d = P.route(tmp_path, "base-t")
    assert d.agent_name == "writer"
    assert d.new_session
    assert "世界观" in d.directive
    assert P.classify_workspace(tmp_path) == "pipeline"
    assert P.scan_phase(tmp_path) == "worldview"


def test_resume_returns_to_same_session(tmp_path):
    d = P.route(tmp_path, "base-t")
    P.commit_route(tmp_path, d, "trace-1")
    P.on_interrupt(tmp_path, d.agent_name, "trace-1")
    d2 = P.route(tmp_path, "base-t", user_text="继续", resume_trace="trace-1")
    assert d2.thread_id == d.thread_id
    assert not d2.new_session


def test_new_message_without_resume_opens_new_session(tmp_path):
    d = P.route(tmp_path, "base-t")
    P.commit_route(tmp_path, d, "trace-1")
    P.on_interrupt(tmp_path, d.agent_name, "trace-1")
    d3 = P.route(tmp_path, "base-t", user_text="继续")
    assert d3.thread_id != d.thread_id
    assert d3.new_session
    old = P.pop_awaiting_trace(tmp_path, d.agent_name)
    assert old == "trace-1"
    assert load_pipeline_state(tmp_path).active_traces.get("writer") is None


def test_progression_through_three_steps(tmp_path):
    # 世界观落盘 → 下一单元：总纲卷纲段
    _mkfile(tmp_path, "outline/世界观.md")
    prog = P.scan_progress(tmp_path)
    assert prog.worldview_done and not prog.master_done
    assert P.scan_phase(tmp_path) == "outline"
    d = P.route(tmp_path, "base-t", user_text="继续")
    assert d.agent_name == "writer"
    assert "总纲" in d.directive

    # 总纲落盘但无卷纲 → 仍是总纲卷纲段（下一单元：第 1 卷卷纲）
    _mkfile(tmp_path, "outline/总纲.md")
    assert P.scan_phase(tmp_path) == "outline"
    assert "第 1 卷卷纲" in P.route(tmp_path, "base-t").directive

    # 第 1 卷卷纲落盘 → 章纲段（下一单元：卷 1 批 1）
    _mkfile(tmp_path, "outline/卷纲-第1卷.md")
    assert P.scan_phase(tmp_path) == "chapters"
    assert "第 1 卷第 1 批" in P.route(tmp_path, "base-t").directive

    # 批 1 落盘 → 下一单元：卷 1 批 2
    _mkfile(tmp_path, "outline/章纲-卷1批1.md")
    assert "第 1 卷第 2 批" in P.route(tmp_path, "base-t").directive

    # 第 2 卷卷纲落盘 → 下一单元跟随第 2 卷
    _mkfile(tmp_path, "outline/卷纲-第2卷.md")
    assert "第 2 卷第 1 批" in P.route(tmp_path, "base-t").directive


def test_modification_request_routes_same_controller(tmp_path):
    _mkfile(tmp_path, "outline/世界观.md")
    _mkfile(tmp_path, "outline/总纲.md")
    d = P.route(tmp_path, "base-t", user_text="把金手指的代价改重一点")
    assert d.agent_name == "writer"
    assert "谁产出谁能改" in d.directive


def test_legacy_five_piece_workspace_readonly(tmp_path):
    (tmp_path / "storyline.md").write_text("old", encoding="utf-8")
    d = P.route(tmp_path, "base-t")
    assert d.agent_name is None
    assert d.phase == "legacy"
    assert "只读" in d.directive
    assert P.classify_workspace(tmp_path) == "legacy"


def test_legacy_seven_step_workspace_readonly(tmp_path):
    _mkfile(tmp_path, "outline/00-赛道.md")
    _mkfile(tmp_path, "outline/04-总纲.md")
    assert P.classify_workspace(tmp_path) == "legacy"
    d = P.route(tmp_path, "base-t")
    assert d.agent_name is None
    assert d.phase == "legacy"
    assert "只读" in d.directive


def test_pipeline_status_for_stage_bar(tmp_path):
    _mkfile(tmp_path, "outline/世界观.md")
    _mkfile(tmp_path, "outline/总纲.md")
    _mkfile(tmp_path, "outline/卷纲-第1卷.md")
    P.on_run_complete(tmp_path, "writer")
    s = P.pipeline_status(tmp_path)
    assert s["kind"] == "pipeline"
    assert s["phase"] == "chapters"
    assert s["next_unit"] == "章纲"
    assert s["awaiting"] == {}


def test_seven_step_state_file_discarded(tmp_path):
    # 旧七步状态文件（schema v1，含 rework 段）加载时按默认状态丢弃
    _mkfile(tmp_path, "outline/世界观.md")
    state_file = tmp_path / "outline" / "_pipeline.json"
    state_file.write_text(
        '{"schema_version": 1, "phase": "storydesign", "rework": {"ticket": "001", "stage": "design_revising"}}',
        encoding="utf-8",
    )
    state = load_pipeline_state(tmp_path)
    assert state.schema_version == 2
    assert getattr(state, "rework", None) is None


def test_abandon_keeps_session_registry(tmp_path):
    d = P.route(tmp_path, "base-t")
    P.commit_route(tmp_path, d, "trace-9")
    P.on_run_abandoned(tmp_path, d.agent_name)
    state = load_pipeline_state(tmp_path)
    assert state.active_traces.get("writer") is None
    assert state.active_threads.get("writer") == d.thread_id
