"""五段式产物契约测试（REQ-20261018-115508 FR-002/003/012）。

锁定 contracts.writing_artifacts 的核心行为：
  - classify_writing_workspace：旧五件套/旧七步/三步制 → legacy；空目录与
    五段结构 → five（DEC-010 三步制也只读）。
  - artifact_relpath：kind+卷号/章号 → 唯一路径；缺参/非法报错（FR-003
    路径映射只活在契约层）。
  - scan_writing_progress / derive_stage：文件即进度（AC-003 阶段推导）。
  - parse_manuscript_status：正文状态行 初稿/人修定稿（FR-009/AC-011）。
  - validate_theme_final / validate_master_final：定稿结构判定（AC-003）。
  - WritingState 状态读写（DEC-002 handoff override 载体）。
  - stage_allowed_kinds：阶段→可写产物集（FR-006 阶段闸依据）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from contracts.writing_artifacts import (
    KIND_CHAPTER,
    KIND_OUTLINE,
    KIND_STYLE,
    KIND_TEXT,
    KIND_THEME,
    KIND_VOLUME,
    LABEL_CHAPTER,
    LABEL_THEME,
    STAGE_CHAPTER,
    STAGE_OUTLINE,
    STAGE_TEXT,
    STAGE_THEME,
    STAGE_VOLUME,
    ArtifactRef,
    WritingProgress,
    artifact_relpath,
    classify_writing_workspace,
    derive_stage,
    parse_manuscript_status,
    parse_relpath,
    load_writing_state,
    save_writing_state,
    scan_writing_progress,
    stage_allowed_kinds,
    validate_master_final,
    validate_theme_final,
)


def _touch(ws: Path, rel: str, text: str = "占位\n") -> None:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


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
| 二 | 保住小队 | 城区 | 连长 | 反杀 |

### 伏笔登记表
| 编号 | 伏笔 | 埋于章 | 收于章 | 状态 |
|---|---|---|---|---|
"""


# ── classify_writing_workspace（AC-014 判定基础）────────────────────


def test_classify_empty_workspace_is_five(tmp_path: Path) -> None:
    assert classify_writing_workspace(tmp_path) == "five"


def test_classify_five_tree(tmp_path: Path) -> None:
    _touch(tmp_path, "主题.md", THEME_FINAL)
    assert classify_writing_workspace(tmp_path) == "five"


def test_classify_legacy_storyline(tmp_path: Path) -> None:
    _touch(tmp_path, "storyline.md")
    assert classify_writing_workspace(tmp_path) == "legacy"


def test_classify_legacy_seven_step(tmp_path: Path) -> None:
    _touch(tmp_path, "outline/00-赛道.md")
    assert classify_writing_workspace(tmp_path) == "legacy"


def test_classify_legacy_three_step(tmp_path: Path) -> None:
    """DEC-010：三步制工作区（outline/世界观.md 等）在新版下判 legacy 只读。"""
    _touch(tmp_path, "outline/世界观.md")
    assert classify_writing_workspace(tmp_path) == "legacy"


# ── artifact_relpath（FR-003 路径映射唯一实现）──────────────────────


def test_relpath_mapping() -> None:
    assert artifact_relpath(KIND_THEME) == "主题.md"
    assert artifact_relpath(KIND_OUTLINE) == "总纲.md"
    assert artifact_relpath(KIND_STYLE) == "文风.md"
    assert artifact_relpath(KIND_VOLUME, volume=3) == "卷纲/第03卷.md"
    assert artifact_relpath(KIND_CHAPTER, chapter=12) == "章纲/第012章.md"
    # C1 修复：正文文件名不带章名（一章一文件不变式；章名活在 H1）
    assert artifact_relpath(KIND_TEXT, chapter=7) == "正文/第007章.md"


def test_relpath_rejects_missing_and_invalid() -> None:
    with pytest.raises(ValueError):
        artifact_relpath(KIND_VOLUME)  # 缺卷号
    with pytest.raises(ValueError):
        artifact_relpath(KIND_CHAPTER)  # 缺章号
    with pytest.raises(ValueError):
        artifact_relpath(KIND_VOLUME, volume=0)  # 非法卷号
    with pytest.raises(ValueError):
        artifact_relpath(KIND_THEME, volume=1)  # 题材不带卷号
    with pytest.raises(ValueError):
        artifact_relpath("不存在的kind")  # type: ignore[arg-type]


def test_parse_relpath_roundtrip(tmp_path: Path) -> None:
    assert parse_relpath("主题.md") == ArtifactRef(kind=KIND_THEME, rel="主题.md")
    assert parse_relpath("卷纲/第02卷.md") == ArtifactRef(kind=KIND_VOLUME, volume=2, rel="卷纲/第02卷.md")
    assert parse_relpath("章纲/第013章.md") == ArtifactRef(kind=KIND_CHAPTER, chapter=13, rel="章纲/第013章.md")
    assert parse_relpath("正文/第007章一百级回家.md") == ArtifactRef(
        kind=KIND_TEXT, chapter=7, title="一百级回家", rel="正文/第007章一百级回家.md"
    )
    assert parse_relpath("outline/总纲.md") is None  # 旧结构不归五段
    assert parse_relpath("随便.md") is None


# ── scan + derive_stage（AC-003 阶段推导）──────────────────────────


def test_stage_theme_when_no_final_theme(tmp_path: Path) -> None:
    _touch(tmp_path, "主题.md", "# 主题 · 书\n> 状态：第 1 步检验中\n")
    prog = scan_writing_progress(tmp_path)
    assert prog.theme_done is False
    assert derive_stage(prog) == STAGE_THEME


def test_stage_progression(tmp_path: Path) -> None:
    # 主题定稿 → 总纲段
    _touch(tmp_path, "主题.md", THEME_FINAL)
    assert derive_stage(scan_writing_progress(tmp_path)) == STAGE_OUTLINE
    # 总纲（2 卷）定稿 → 卷纲段（第 1 卷）
    _touch(tmp_path, "总纲.md", MASTER_FINAL)
    prog = scan_writing_progress(tmp_path)
    assert prog.outline_done and prog.outline_volumes == 2
    assert derive_stage(prog) == STAGE_VOLUME
    # 第 1 卷卷纲落盘 → 章纲段
    _touch(tmp_path, "卷纲/第01卷.md")
    assert derive_stage(scan_writing_progress(tmp_path)) == STAGE_CHAPTER
    # 章纲 3 章、正文 2 章 → 正文段（正文滞后）
    for n in (1, 2, 3):
        _touch(tmp_path, f"章纲/第{n:03d}章.md")
    _touch(tmp_path, "正文/第001章开局.md", "# 第001章 · 开局\n> 状态：人修定稿\n")
    _touch(tmp_path, "正文/第002章发酵.md", "# 第002章 · 发酵\n> 状态：初稿\n")
    prog = scan_writing_progress(tmp_path)
    assert prog.chapter_outlines == [1, 2, 3]
    assert prog.chapter_texts[2].finalized is False
    assert prog.chapter_texts[1].finalized is True
    assert derive_stage(prog) == STAGE_TEXT
    # 正文追平章纲 → 回卷纲段（第 2 卷）
    _touch(tmp_path, "正文/第003章爆发.md", "# 第003章 · 爆发\n> 状态：人修定稿\n")
    assert derive_stage(scan_writing_progress(tmp_path)) == STAGE_VOLUME
    # 全部卷完成 → 回章纲段滚动补纲
    _touch(tmp_path, "卷纲/第02卷.md")
    assert derive_stage(scan_writing_progress(tmp_path)) == STAGE_CHAPTER


def test_stage_unparseable_master_falls_back(tmp_path: Path) -> None:
    """总纲卷数解析失败时按已有卷数+1 推进，不阻塞路由。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    _touch(tmp_path, "总纲.md", "# 总纲 · 书\n> 状态：定稿\n\n## 核心纲\n（无表格）\n")
    _touch(tmp_path, "卷纲/第01卷.md")
    for n in (1,):
        _touch(tmp_path, f"章纲/第{n:03d}章.md")
        _touch(tmp_path, f"正文/第{n:03d}章开局.md", "# 章\n> 状态：人修定稿\n")
    assert derive_stage(scan_writing_progress(tmp_path)) == STAGE_VOLUME


# ── 正文状态行（FR-009 / AC-007 前置）─────────────────────────────


def test_parse_manuscript_status() -> None:
    assert parse_manuscript_status("> 状态：人修定稿｜2026-10-18\n正文") == "finalized"
    assert parse_manuscript_status("> 状态：初稿｜2026-10-18\n正文") == "draft"
    assert parse_manuscript_status("没有任何状态行") == "draft"


# ── 定稿结构校验（AC-003）────────────────────────────────────────


def test_validate_theme_final() -> None:
    assert validate_theme_final(THEME_FINAL) == []
    errs = validate_theme_final("# 主题 · 书\n> 状态：第 2 步\n")
    assert any("一句话主题" in e for e in errs)


def test_validate_master_final() -> None:
    assert validate_master_final(MASTER_FINAL) == []
    errs = validate_master_final("# 总纲 · 书\n")
    assert any("核心纲" in e for e in errs)


# ── WritingState（DEC-002 handoff override 载体）───────────────────


def test_writing_state_roundtrip(tmp_path: Path) -> None:
    state = load_writing_state(tmp_path)
    state.override_stage = STAGE_CHAPTER
    state.active_threads["author"] = "t1"
    save_writing_state(tmp_path, state)
    loaded = load_writing_state(tmp_path)
    assert loaded.override_stage == STAGE_CHAPTER
    assert loaded.active_threads["author"] == "t1"


def test_writing_state_corruption_tolerant(tmp_path: Path) -> None:
    _touch(tmp_path, ".writing/state.json", "{ 不是 json")
    state = load_writing_state(tmp_path)
    assert state.override_stage is None  # 回默认，不阻塞主流程


# ── 阶段闸依据（FR-006 / AC-008）──────────────────────────────────


def test_stage_allowed_kinds() -> None:
    assert stage_allowed_kinds(STAGE_THEME) == {KIND_THEME}
    assert stage_allowed_kinds(STAGE_OUTLINE) == {KIND_OUTLINE}
    assert stage_allowed_kinds(STAGE_VOLUME) == {KIND_VOLUME}
    # 文风卡在章纲段起草（DEC-009）
    assert stage_allowed_kinds(STAGE_CHAPTER) == {KIND_CHAPTER, KIND_STYLE}
    assert stage_allowed_kinds(STAGE_TEXT) == {KIND_TEXT}


def test_stage_labels() -> None:
    """阶段标签与 desktop 阶段条同名对齐（FR-011 5 段）。"""
    from contracts.writing_artifacts import STAGE_LABELS

    assert STAGE_LABELS[STAGE_THEME] == LABEL_THEME
    assert len(STAGE_LABELS) == 5
    assert LABEL_THEME == "题材"
    assert LABEL_CHAPTER == "章纲"
