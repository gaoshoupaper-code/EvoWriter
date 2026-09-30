"""storyline_graph 服务测试：单文件区块格式的解析 / timeline 派生 / 全景数据。

样本：storyline.md 单文件，1 主线 + 1 支线，4 事件（时序含小数插入），1 交汇。
REQ-20260930-163019 FR-002/003：泳道图（storyline_graph.md）停产，模块职责收敛为
解析 + timeline.md（agent 上下文）+ 全景事件数据（前端大纲全景表数据源）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.domains.writing.expert_agent.services.storyline_graph import (
    build_panorama_events,
    build_storyline_graph_data,
    build_timeline_markdown,
    generate_storyline_graph,
    is_stale,
)

_SAMPLE = """# 故事核心

- Logline：示例
- 最终结局：略

## 复仇线 · 主线 · 活跃

- 主要地点：青云宗
- 全局走向：从起到终

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T1 | 灭门之夜 | 冲突 | 发展 | 青云宗 | 林寒 | | 一夜之间家破人亡。 |
| T2.5 | 祭祖大典的闯入 | 冲突 | 发展 | 青云宗 | 林寒 | 感情线 | 当众闯坛，两线同时改写。 |
| T3 | 主线收束 | 胜利 | 终局 | 青云宗 | 林寒 | | 张力结算。 |

## 感情线 · 支线 · 活跃

- 主要地点：青云宗、云岚城
- 全局走向：并肩成长

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T2 | 初次相遇 | 悬念 | 发展 | 云岚城 | 林寒、苏晚 | | 缺口撕开。 |
"""


def _write_sample(workspace: Path) -> None:
    (workspace / "storyline.md").write_text(_SAMPLE, encoding="utf-8")


def test_build_parses_blocks_events_and_intersection(tmp_path: Path) -> None:
    _write_sample(tmp_path)
    data = build_storyline_graph_data(tmp_path)
    assert data is not None
    assert [sl.name for sl in data.storylines] == ["复仇线", "感情线"]
    assert data.storylines[0].type == "主线"
    assert data.storylines[0].status == "活跃"
    assert data.storylines[0].direction == "从起到终"
    assert data.storylines[0].locations == "青云宗"
    assert len(data.events) == 4
    # 交汇事件同时属于两条线（主属复仇线 + 交汇感情线）
    assert set(data.events["祭祖大典的闯入"].storylines) == {"复仇线", "感情线"}
    # 事件级字段
    ev = data.events["灭门之夜"]
    assert ev.type == "冲突"
    assert ev.stage == "发展"
    assert ev.location == "青云宗"
    assert ev.characters == "林寒"
    # FR-002：泳道图已停产，派生产物只剩 timeline
    assert "全景时间轴" in data.timeline_markdown


def test_build_panorama_events(tmp_path: Path) -> None:
    """FR-003/DEC-005/010：全景事件按时序排序，t_raw 保留原始 T 号（含小数插入），
    交汇事件 storylines 含全部参与线，剧情字段齐全。"""
    _write_sample(tmp_path)
    events = build_panorama_events(tmp_path)
    assert events is not None
    assert [ev.name for ev in events] == ["灭门之夜", "初次相遇", "祭祖大典的闯入", "主线收束"]
    assert [ev.t_raw for ev in events] == ["T1", "T2", "T2.5", "T3"]
    cross = events[2]
    assert list(cross.storylines) == ["复仇线", "感情线"]
    assert cross.desc == "当众闯坛，两线同时改写。"
    assert cross.characters == "林寒"
    first = events[0]
    assert first.type == "冲突"
    assert first.location == "青云宗"


def test_build_panorama_events_returns_none_on_missing_or_legacy(tmp_path: Path) -> None:
    """无 storyline.md 或旧格式（无线区块）：返回 None（前端降级，不抛异常）。"""
    assert build_panorama_events(tmp_path) is None
    (tmp_path / "storyline.md").write_text("# 故事线一览表\n\n无区块。\n", encoding="utf-8")
    assert build_panorama_events(tmp_path) is None


def test_t_numbers_read_directly_and_reranked(tmp_path: Path) -> None:
    """T 序直接读事件时序号；t_map 为排序后重整化的连续序（rank）。"""
    _write_sample(tmp_path)
    data = build_storyline_graph_data(tmp_path)
    assert data is not None
    assert data.events["灭门之夜"].t_num == 1.0
    assert data.events["初次相遇"].t_num == 2.0
    assert data.events["祭祖大典的闯入"].t_num == 2.5
    assert data.events["主线收束"].t_num == 3.0
    # 重整化：T1→1，初次相遇(T2)→2，交汇(T2.5)→3，收束(T3)→4
    assert data.t_map["灭门之夜"] == 1
    assert data.t_map["初次相遇"] == 2
    assert data.t_map["祭祖大典的闯入"] == 3
    assert data.t_map["主线收束"] == 4


def test_build_timeline_markdown(tmp_path: Path) -> None:
    """全景时间轴：全部事件按 T 升序、序号连续、交汇多线归属、无描述列。"""
    _write_sample(tmp_path)
    data = build_storyline_graph_data(tmp_path)
    assert data is not None
    md = build_timeline_markdown(data.storylines, data.events, data.t_map)
    lines = [ln for ln in md.splitlines() if ln.strip().startswith("|")]
    # 表头 + 分隔行 + 4 数据行
    assert len(lines) == 6
    assert lines[0].count("|") == 6  # 五列 + 两侧竖线
    # 事件按 T 升序出现
    order = [ln.split("|")[2].strip() for ln in lines[2:]]
    assert order == ["灭门之夜", "初次相遇", "祭祖大典的闯入", "主线收束"]
    # 交汇行：所属线含两条线名
    cross_line = next(ln for ln in lines if "祭祖大典的闯入" in ln)
    assert "复仇线" in cross_line and "感情线" in cross_line
    # 不含描述
    assert "家破人亡" not in md


def test_generate_writes_timeline_only(tmp_path: Path) -> None:
    """FR-002：派生只产 timeline.md（agent 上下文）；storyline_graph.md 停产。"""
    _write_sample(tmp_path)
    assert is_stale(tmp_path) is True
    generate_storyline_graph(tmp_path)
    assert (tmp_path / "timeline.md").exists()
    assert not (tmp_path / "storyline_graph.md").exists()
    assert is_stale(tmp_path) is False


def test_is_stale_detects_source_update(tmp_path: Path) -> None:
    _write_sample(tmp_path)
    generate_storyline_graph(tmp_path)
    assert is_stale(tmp_path) is False
    src = tmp_path / "storyline.md"
    src.write_text(_SAMPLE + "\n## 新线 · 支线 · 活跃\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(src, (future, future))
    assert is_stale(tmp_path) is True


def test_old_format_returns_none(tmp_path: Path) -> None:
    """旧格式（storyline/ 目录多文件）不再保证解析——降级返回 None，不抛异常。"""
    (tmp_path / "storyline.md").write_text(
        "# 故事线一览表\n\n| ID | 名称 | 类型 | 状态 |\n|----|------|------|------|\n"
        "| S01 | 主线 | 主线 | 活跃 |\n",
        encoding="utf-8",
    )
    sdir = tmp_path / "storyline"
    sdir.mkdir()
    (sdir / "S01-主线.md").write_text("### S01-主线 [主线]\n- 全局走向：旧格式\n", encoding="utf-8")
    assert build_storyline_graph_data(tmp_path) is None


def test_missing_products_returns_none(tmp_path: Path) -> None:
    assert build_storyline_graph_data(tmp_path) is None
    generate_storyline_graph(tmp_path)  # 无产物：不抛、不写
    assert not (tmp_path / "timeline.md").exists()
