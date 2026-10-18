"""五段式管线路由测试（REQ-20261018-115508 FR-002/012，AC-003/004/014）。

锁定 executor 管线层：
  - 空工作区 → 题材段；产物树推进阶段自动推导（AC-003）。
  - handoff 点名跳段：override 优先、commit 消费即清、resume 不消费（DEC-002，AC-004）。
  - legacy：旧五件套/旧七步/三步制一律只读（DEC-010，AC-014 判定）。
  - pipeline_status：kind/next_unit 标签（FR-011 五段阶段条数据源）。
"""
from __future__ import annotations

from pathlib import Path

from app.domains.writing import pipeline
from contracts.writing_artifacts import (
    STAGE_CHAPTER,
    STAGE_THEME,
    STAGE_VOLUME,
    load_writing_state,
    save_writing_state,
)

THEME_FINAL = """# 主题 · 测试书
> 状态：定稿｜2026-10-18

## 一句话主题
谁靠什么在什么拉扯里走什么路。

## 约束级
### 核心爽点
卡
### 核心矛盾
卡
### 核心爽点剧情
卡
"""

MASTER_FINAL = """# 总纲 · 测试书
> 状态：定稿｜2026-10-18

## 核心纲

| 卷 | 小目标 | 地图 | BOSS | 关键节点 |
|---|---|---|---|---|
| 一 | 活下去 | 垃圾场 | 工头 | 捡神器 |
"""


def _route(ws: Path, **kwargs):
    return pipeline.route(ws, "thread-1", **kwargs)


def test_route_empty_workspace_starts_theme(tmp_path: Path) -> None:
    d = _route(tmp_path)
    assert d.agent_name == "author"
    assert d.phase == STAGE_THEME
    assert "题材" in d.directive and "进度摘要" in d.directive
    assert "主题：未落盘" in d.directive


def test_route_stage_progression(tmp_path: Path) -> None:
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    assert _route(tmp_path).phase == "outline"
    (tmp_path / "总纲.md").write_text(MASTER_FINAL, encoding="utf-8")
    d = _route(tmp_path)
    assert d.phase == STAGE_VOLUME
    assert "第 1 卷卷纲" in d.directive


def test_route_override_priority_and_consumption(tmp_path: Path) -> None:
    """点名跳段优先于扫描；commit 消费即清；resume 不消费（AC-004）。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    state = load_writing_state(tmp_path)
    state.override_stage = STAGE_CHAPTER
    save_writing_state(tmp_path, state)

    d = _route(tmp_path)
    assert d.phase == STAGE_CHAPTER and d.used_override is True
    pipeline.commit_route(tmp_path, d, "trace-9")
    assert load_writing_state(tmp_path).override_stage is None  # 消费即清

    # resume：override 不被消费（同一轮的拍板答复）
    state = load_writing_state(tmp_path)
    state.override_stage = STAGE_VOLUME
    state.active_threads["author"] = "thread-1:author:1"
    state.active_traces["author"] = "trace-1"
    save_writing_state(tmp_path, state)
    d = _route(tmp_path, resume_trace="trace-1")
    assert d.new_session is False and d.thread_id == "thread-1:author:1"
    assert d.used_override is False
    assert load_writing_state(tmp_path).override_stage == STAGE_VOLUME


def test_route_legacy_variants(tmp_path: Path) -> None:
    """旧五件套 / 旧七步 / 三步制 → 只读 directive + agent None（AC-014）。"""
    for setup in (
        lambda ws: (ws / "storyline.md").write_text("旧五件套", encoding="utf-8"),
        lambda ws: (ws / "outline" / "00-赛道.md").parent.mkdir(parents=True, exist_ok=True)
        or (ws / "outline" / "00-赛道.md").write_text("旧七步", encoding="utf-8"),
        lambda ws: (ws / "outline").mkdir(exist_ok=True)
        or (ws / "outline" / "世界观.md").write_text("三步制", encoding="utf-8"),
    ):
        import shutil

        ws = tmp_path / f"book-{abs(hash(setup)) % 10000}"
        ws.mkdir()
        setup(ws)
        d = _route(ws)
        assert d.phase == "legacy" and d.agent_name is None
        assert "只读" in d.directive


def test_pipeline_status_labels(tmp_path: Path) -> None:
    status = pipeline.pipeline_status(tmp_path)
    assert status["kind"] == "five"
    assert status["next_unit"] == "题材"
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    assert pipeline.pipeline_status(tmp_path)["next_unit"] == "总纲"


def test_next_unit_labels_five_stages(tmp_path: Path) -> None:
    """五段标签齐全（FR-011 阶段条同名对齐）。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    (tmp_path / "总纲.md").write_text(MASTER_FINAL, encoding="utf-8")
    (tmp_path / "卷纲").mkdir()
    (tmp_path / "卷纲" / "第01卷.md").write_text("# 卷纲", encoding="utf-8")
    assert pipeline.next_unit_label(tmp_path) == "章纲"
    (tmp_path / "章纲").mkdir()
    (tmp_path / "章纲" / "第001章.md").write_text("# 章", encoding="utf-8")
    assert pipeline.next_unit_label(tmp_path) == "正文"


def test_run_lifecycle_updates_state(tmp_path: Path) -> None:
    d = _route(tmp_path)
    pipeline.commit_route(tmp_path, d, "trace-1")
    pipeline.on_interrupt(tmp_path, d.agent_name, "trace-1")
    state = load_writing_state(tmp_path)
    assert state.active_traces["author"] == "trace-1"
    pipeline.on_run_complete(tmp_path, d.agent_name)
    state = load_writing_state(tmp_path)
    assert "author" not in state.active_traces
    assert state.stage == STAGE_THEME


def test_current_stage_honors_override(tmp_path: Path) -> None:
    """C3：装配用段解析与 route 同口径——override 优先于扫描。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    assert pipeline.current_stage(tmp_path) == "outline"  # 扫描段
    state = load_writing_state(tmp_path)
    state.override_stage = STAGE_CHAPTER
    save_writing_state(tmp_path, state)
    assert pipeline.current_stage(tmp_path) == STAGE_CHAPTER
