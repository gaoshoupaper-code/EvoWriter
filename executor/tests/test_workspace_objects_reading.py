"""object/*.md 物品卡读取链测试（REQ-20261004-221109 FR-007 / AC-008 后端半）。

覆盖 read_workspace_objects / bootstrap 汇入 / SSE _classify_changes 分类：
  - 有卡目录：按文件名排序返回
  - 无卡目录（旧作品）：空列表、不报错
  - workspace 不存在：返回 None（路由层转 404）
  - watch 分类：object/ 下 .md 变化归 "objects"
"""

from __future__ import annotations

from pathlib import Path

from app.platform.state.artifact_store import WritingArtifactStore
from app.routers.workspaces import _classify_changes


def _make_store(tmp_path: Path) -> WritingArtifactStore:
    return WritingArtifactStore(workspace_root=tmp_path, threads=None)  # type: ignore[arg-type]


_CARD_A = """# 青云剑

## 基本信息

- 名称：青云剑
- 类型：武器
- 叙事可见性：明线

## 详情

略

## 轨迹

| 事件 | 变化 | 归属 | 备注 |
|------|------|------|------|
| 少年拾剑 | 登场 | 林岸 | 略 |
"""

_CARD_B = _CARD_A.replace("青云剑", "焚天诀").replace("武器", "功法")


def test_read_objects_sorted(tmp_path: Path) -> None:
    ws = tmp_path / "owner1" / "ws1"
    (ws / "object").mkdir(parents=True)
    (ws / "object" / "焚天诀.md").write_text(_CARD_B, encoding="utf-8")
    (ws / "object" / "青云剑.md").write_text(_CARD_A, encoding="utf-8")

    store = _make_store(tmp_path)
    content = store.read_workspace_objects("owner1", "ws1")
    assert content is not None
    assert [o.name for o in content.objects] == ["焚天诀", "青云剑"]
    assert content.objects[1].markdown == _CARD_A


def test_read_objects_empty_dir_is_empty_list(tmp_path: Path) -> None:
    ws = tmp_path / "owner1" / "ws2"
    ws.mkdir(parents=True)
    (ws / "storyline.md").write_text("# 空", encoding="utf-8")

    store = _make_store(tmp_path)
    content = store.read_workspace_objects("owner1", "ws2")
    assert content is not None
    assert content.objects == []


def test_read_objects_missing_dir_is_empty_list(tmp_path: Path) -> None:
    """旧作品连 object/ 目录都没有：空列表（FR-007 空态，不报错）。"""
    ws = tmp_path / "owner1" / "ws3"
    ws.mkdir(parents=True)

    store = _make_store(tmp_path)
    content = store.read_workspace_objects("owner1", "ws3")
    assert content is not None
    assert content.objects == []


def test_read_objects_workspace_missing_returns_none(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.read_workspace_objects("owner1", "nope") is None


def test_classify_changes_object_md(tmp_path: Path) -> None:
    changes = [(1, str(tmp_path / "object" / "青云剑.md"))]
    assert _classify_changes(changes, tmp_path) == {"objects"}


def test_classify_changes_other_top_level_ignored(tmp_path: Path) -> None:
    changes = [(1, str(tmp_path / "review" / "storybuilding.md"))]
    assert _classify_changes(changes, tmp_path) == set()
