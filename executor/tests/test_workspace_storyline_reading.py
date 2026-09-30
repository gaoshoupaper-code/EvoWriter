"""WorkspaceStorylineContent 双格式读取测试（REQ-20260930-002231 FR-013/014）。

v2（新格式）：storyline.md 单文件含线区块 → markdown 承载全文，entries 按区块拆分。
legacy（旧格式）：storyline/ 目录多文件 → 维持旧读取行为，format="legacy"。
"""

from __future__ import annotations

from pathlib import Path

from app.platform.state.artifact_store import WritingArtifactStore
from app.routers.workspaces import _attach_panorama


def _make_store(tmp_path: Path) -> WritingArtifactStore:
    return WritingArtifactStore(workspace_root=tmp_path, threads=None)  # type: ignore[arg-type]


_V2_MD = """# 故事核心

- Logline：略

## 复仇线 · 主线 · 活跃
- 主要地点：青云宗
- 全局走向：略

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T1 | 灭门之夜 | 冲突 | 发展 | 青云宗 | 林寒 | | 略 |

## 感情线 · 支线 · 活跃
- 主要地点：云岚城
- 全局走向：略
"""


def test_v2_single_file_read(tmp_path: Path) -> None:
    ws = tmp_path / "owner1" / "ws1"
    ws.mkdir(parents=True)
    (ws / "storyline.md").write_text(_V2_MD, encoding="utf-8")

    store = _make_store(tmp_path)
    content = store.read_workspace_storyline("owner1", "ws1")
    assert content is not None
    assert content.format == "v2"
    assert content.markdown == _V2_MD
    assert content.index_markdown == _V2_MD
    # entries 按线区块拆分
    assert [e.title for e in content.entries] == ["复仇线", "感情线"]
    assert content.file_count == 2
    assert "灭门之夜" in content.entries[0].markdown


def test_legacy_directory_read(tmp_path: Path) -> None:
    ws = tmp_path / "owner1" / "ws2"
    (ws / "storyline").mkdir(parents=True)
    (ws / "storyline.md").write_text(
        "# 故事线一览表\n\n| ID | 名称 | 类型 | 状态 |\n|----|------|------|------|\n"
        "| S01 | 主线 | 主线 | 活跃 |\n",
        encoding="utf-8",
    )
    (ws / "storyline" / "S01-主线.md").write_text("### S01-主线 [主线]\n旧格式内容", encoding="utf-8")
    (ws / "storyline" / "timeline.md").write_text("# 全局事件时间线\n| 序 | 事件 |\n", encoding="utf-8")

    store = _make_store(tmp_path)
    content = store.read_workspace_storyline("owner1", "ws2")
    assert content is not None
    assert content.format == "legacy"
    assert "一览表" in content.index_markdown
    # 旧读取行为：storyline/*.md 全部进 entries（含 timeline.md，与旧行为一致）
    names = [e.filename for e in content.entries]
    assert "S01-主线.md" in names
    assert "timeline.md" in names
    assert content.file_count == 2


def test_missing_storyline_returns_empty_not_none(tmp_path: Path) -> None:
    """workspace 存在但无 storyline 产物 → 返回空内容对象（不 500、不误判格式）。"""
    ws = tmp_path / "owner1" / "ws3"
    ws.mkdir(parents=True)

    store = _make_store(tmp_path)
    content = store.read_workspace_storyline("owner1", "ws3")
    assert content is not None
    assert content.format == "v2"
    assert content.markdown == ""
    assert content.entries == []


def test_missing_workspace_returns_none(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.read_workspace_storyline("nobody", "nope") is None


def test_v2_panorama_events(tmp_path: Path) -> None:
    """FR-003（REQ-20260930-163019）：v2 storyline 路由层补全景事件（按时序排序、原 T 号）。

    panorama 组装在路由层（_attach_panorama）——platform 层禁止依赖 domains 解析器
    （分层规则 R1）。
    """
    ws = tmp_path / "owner1" / "ws3"
    ws.mkdir(parents=True)
    (ws / "storyline.md").write_text(_V2_MD, encoding="utf-8")

    store = _make_store(tmp_path)
    content = store.read_workspace_storyline("owner1", "ws3")
    assert content is not None
    content = _attach_panorama(content, ws)
    assert len(content.panorama) == 1
    ev = content.panorama[0]
    assert ev.t == "T1"
    assert ev.name == "灭门之夜"
    assert ev.type == "冲突"
    assert ev.storylines == ["复仇线"]
    assert ev.characters == "林寒"
    assert ev.location == "青云宗"


def test_legacy_panorama_empty(tmp_path: Path) -> None:
    """FR-003：legacy 旧格式不解析全景——panorama 保持空列表，前端降级为按线分区块视图。"""
    ws = tmp_path / "owner1" / "ws4"
    (ws / "storyline").mkdir(parents=True)
    (ws / "storyline.md").write_text("# 旧索引\n", encoding="utf-8")
    (ws / "storyline" / "S01-主线.md").write_text("### S01-主线 [主线]\n", encoding="utf-8")

    store = _make_store(tmp_path)
    content = store.read_workspace_storyline("owner1", "ws4")
    assert content is not None
    assert content.format == "legacy"
    assert _attach_panorama(content, ws).panorama == []
