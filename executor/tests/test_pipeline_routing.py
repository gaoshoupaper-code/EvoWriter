"""双 agent 管线编排层测试（REQ-20261011-142529 FR-002/003/004，DEC-006/008/020）。

路由 / 会话切换 / resume 定位 / 回炉状态机 / legacy 分类 / 阶段条数据。
"""
from __future__ import annotations

from pathlib import Path

from contracts.outline_pipeline import (
    REWORK_DESIGN,
    REWORK_IMPACT,
    ReworkState,
    load_pipeline_state,
    save_pipeline_state,
)
from app.domains.writing import pipeline as P


def _mkdesign(ws: Path) -> None:
    (ws / "outline").mkdir(exist_ok=True)
    for f in P.DESIGN_FILES:
        (ws / "outline" / f).write_text("x", encoding="utf-8")


def test_new_workspace_routes_design_step0(tmp_path):
    d = P.route(tmp_path, "base-t")
    assert d.agent_name == "storydesign"
    assert d.new_session
    assert "赛道确认" in d.directive
    assert P.classify_workspace(tmp_path) == "pipeline"


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
    # 丢弃重开：作废未决 trace 后清空登记
    old = P.pop_awaiting_trace(tmp_path, d.agent_name)
    assert old == "trace-1"
    assert load_pipeline_state(tmp_path).active_traces.get("storydesign") is None


def test_design_done_routes_structure_and_keyword_override(tmp_path):
    _mkdesign(tmp_path)
    d = P.route(tmp_path, "base-t", user_text="继续")
    assert d.agent_name == "storystructure"
    d5 = P.route(tmp_path, "base-t", user_text="把金手指的代价改重一点")
    assert d5.agent_name == "storydesign"


def test_rework_state_machine_drives_routing(tmp_path):
    _mkdesign(tmp_path)
    st = load_pipeline_state(tmp_path)
    st.rework = ReworkState(ticket="001", title="t", stage=REWORK_DESIGN)
    save_pipeline_state(tmp_path, st)
    d6 = P.route(tmp_path, "base-t", user_text="继续")
    assert d6.agent_name == "storydesign"
    assert "回炉修订" in d6.directive
    P.on_run_complete(tmp_path, "storydesign")
    assert load_pipeline_state(tmp_path).rework.stage == REWORK_IMPACT
    d7 = P.route(tmp_path, "base-t", user_text="继续")
    assert d7.agent_name == "storystructure"
    assert "波及修订" in d7.directive
    P.on_run_complete(tmp_path, "storystructure")
    assert load_pipeline_state(tmp_path).rework is None


def test_legacy_workspace_routes_legacy_mode(tmp_path):
    (tmp_path / "storyline.md").write_text("old", encoding="utf-8")
    d = P.route(tmp_path, "base-t")
    assert d.agent_name is None
    assert d.phase == "legacy"
    assert "旧版" in d.directive
    assert P.classify_workspace(tmp_path) == "legacy"


def test_pipeline_status_for_stage_bar(tmp_path):
    _mkdesign(tmp_path)
    P.on_run_complete(tmp_path, "storystructure")
    s = P.pipeline_status(tmp_path)
    assert s["kind"] == "pipeline"
    assert s["design_done"] is True
    assert s["rework"] is None


def test_abandon_keeps_rework_stage(tmp_path):
    _mkdesign(tmp_path)
    st = load_pipeline_state(tmp_path)
    st.rework = ReworkState(ticket="002", title="t", stage=REWORK_DESIGN)
    save_pipeline_state(tmp_path, st)
    P.on_run_abandoned(tmp_path, "storydesign")
    st2 = load_pipeline_state(tmp_path)
    assert st2.rework is not None and st2.rework.stage == REWORK_DESIGN
