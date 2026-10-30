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
    next_stage_on_confirm,
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

VOLUME_FINAL = """# 卷纲 · 第 1 卷 · 活下去
> 状态：定稿｜2026-10-18

| 卷末大爽点 | 捡到首件神器 |
|---|---|

## 发展阶段
阶段表
## 爽点节奏表
节奏表
## 爽点质检表
质检表
## 卷末钩子
本卷收尾埋什么
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
    # 第 1 卷卷纲定稿 → 章纲段（草稿卷不切段，见下方定稿闸回归组）
    _touch(tmp_path, "卷纲/第01卷.md", VOLUME_FINAL)
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
    _touch(tmp_path, "卷纲/第02卷.md", VOLUME_FINAL)
    assert derive_stage(scan_writing_progress(tmp_path)) == STAGE_CHAPTER


def test_stage_outline_when_master_final_but_incomplete(tmp_path: Path) -> None:
    """AC-002：状态行写了定稿但结构残缺（无卷表）→ 结构校验拦下，段停留总纲。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    _touch(tmp_path, "总纲.md", "# 总纲 · 书\n> 状态：定稿\n\n## 核心纲\n（无表格）\n")
    prog = scan_writing_progress(tmp_path)
    assert prog.outline_done is False
    assert derive_stage(prog) == STAGE_OUTLINE


# ── next_stage_on_confirm（段审确认循环推进，2026-10-27 卷纲连写 bug）──

MASTER_FINAL_3V = MASTER_FINAL.replace(
    "| 二 | 保住小队 | 城区 | 连长 | 反杀 |",
    "| 二 | 保住小队 | 城区 | 连长 | 反杀 |\n| 三 | 夺回矿区 | 矿区 | 矿主 | 破局 |",
)


def _seed_loop_book(ws: Path) -> None:
    """3 卷计划：第 1 卷 3 章全部写完，第 2 卷卷纲刚定稿（待进章纲）。

    该形态下 derive_stage 推回 STAGE_VOLUME（outlined==written 盲区）——
    正是段审确认后 agent 连写下一卷的线上 bug 场景。
    """
    _touch(ws, "主题.md", THEME_FINAL)
    _touch(ws, "总纲.md", MASTER_FINAL_3V)
    _touch(ws, "卷纲/第01卷.md", VOLUME_FINAL.replace("\n节奏表\n", "\n本卷章号范围：1-3\n节奏表\n"))
    _touch(ws, "卷纲/第02卷.md", VOLUME_FINAL.replace("\n节奏表\n", "\n本卷章号范围：4-6\n节奏表\n"))
    for n in (1, 2, 3):
        _touch(ws, f"章纲/第{n:03d}章.md")
        _touch(ws, f"正文/第{n:03d}章完.md", f"# 第{n:03d}章 · 完\n> 状态：人修定稿\n")


def test_confirm_advance_volume_goes_chapter_not_next_volume(tmp_path: Path) -> None:
    """回归锚：第 2 卷卷纲确认 → 进章纲，不按 derive_stage 留在卷纲连写第 3 卷。"""
    _seed_loop_book(tmp_path)
    assert derive_stage(scan_writing_progress(tmp_path)) == STAGE_VOLUME  # 盲区复现
    assert next_stage_on_confirm(tmp_path, STAGE_VOLUME) == STAGE_CHAPTER


def test_confirm_advance_volume_without_final_volume_stays(tmp_path: Path) -> None:
    """防御：卷纲段无定稿卷（提前交审）→ 留在卷纲。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    _touch(tmp_path, "总纲.md", MASTER_FINAL)
    assert next_stage_on_confirm(tmp_path, STAGE_VOLUME) == STAGE_VOLUME


def test_confirm_advance_chapter_with_stock_goes_text(tmp_path: Path) -> None:
    """一批章纲确认（库存>0）→ 进正文。"""
    _seed_loop_book(tmp_path)
    for n in (4, 5):
        _touch(tmp_path, f"章纲/第{n:03d}章.md")
    assert next_stage_on_confirm(tmp_path, STAGE_CHAPTER) == STAGE_TEXT


def test_confirm_advance_chapter_stock_out_goes_next_volume(tmp_path: Path) -> None:
    """本卷章纲齐确认（库存=0、还有卷）→ 回卷纲做下一卷。"""
    _seed_loop_book(tmp_path)
    for n in (4, 5, 6):
        _touch(tmp_path, f"章纲/第{n:03d}章.md")
        _touch(tmp_path, f"正文/第{n:03d}章.md")
    assert next_stage_on_confirm(tmp_path, STAGE_CHAPTER) == STAGE_VOLUME


def test_confirm_advance_chapter_stock_out_all_volumes_stays(tmp_path: Path) -> None:
    """末卷章纲齐确认（库存=0、卷全齐）→ 留章纲（全书收尾滚动）。"""
    _seed_loop_book(tmp_path)
    _touch(tmp_path, "卷纲/第03卷.md", VOLUME_FINAL)
    assert next_stage_on_confirm(tmp_path, STAGE_CHAPTER) == STAGE_CHAPTER


def test_confirm_advance_text_stock_out_back_to_chapter(tmp_path: Path) -> None:
    """正文库存尽确认 → 回章纲补纲。"""
    _seed_loop_book(tmp_path)
    assert next_stage_on_confirm(tmp_path, STAGE_TEXT) == STAGE_CHAPTER


def test_confirm_advance_text_with_stock_stays(tmp_path: Path) -> None:
    """正文段停审但库存仍足（卡点上报类）→ 留正文。"""
    _seed_loop_book(tmp_path)
    for n in (4, 5):
        _touch(tmp_path, f"章纲/第{n:03d}章.md")
    assert next_stage_on_confirm(tmp_path, STAGE_TEXT) == STAGE_TEXT


def test_confirm_advance_theme_outline_passthrough(tmp_path: Path) -> None:
    """题材/总纲不经段审通道 → 原样返回。"""
    assert next_stage_on_confirm(tmp_path, STAGE_THEME) == STAGE_THEME
    assert next_stage_on_confirm(tmp_path, STAGE_OUTLINE) == STAGE_OUTLINE


# ── 定稿闸（REQ-20261019-183150 FR-001/002；主线脉络事故回归锚）──


def test_stage_outline_when_master_draft(tmp_path: Path) -> None:
    """AC-001：总纲草稿（起草中）不切段——2026-10-19 事故复现锚。

    事故：主线脉络一写进总纲.md，旧规则按「文件存在且非空」切段到卷纲，
    四段表被阶段闸拦截。定稿闸后草稿留段。
    """
    _touch(tmp_path, "主题.md", THEME_FINAL)
    draft = MASTER_FINAL.replace("> 状态：定稿｜2026-10-18", "> 状态：起草中｜2026-10-19")
    _touch(tmp_path, "总纲.md", draft)
    prog = scan_writing_progress(tmp_path)
    assert prog.outline_done is False
    assert derive_stage(prog) == STAGE_OUTLINE


def test_stage_outline_when_master_status_missing(tmp_path: Path) -> None:
    """DEC-003 严格口径：无状态行的总纲不算定稿（忘写状态行只卡段、不误切段）。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    lines = MASTER_FINAL.splitlines()
    no_status = "\n".join([lines[0], *lines[2:]])
    _touch(tmp_path, "总纲.md", no_status)
    prog = scan_writing_progress(tmp_path)
    assert prog.outline_done is False
    assert derive_stage(prog) == STAGE_OUTLINE


def test_stage_outline_when_master_status_negated(tmp_path: Path) -> None:
    """状态行「未定稿」不得因含「定稿」子串误判为定稿。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    draft = MASTER_FINAL.replace("> 状态：定稿｜2026-10-18", "> 状态：未定稿｜2026-10-19")
    _touch(tmp_path, "总纲.md", draft)
    assert scan_writing_progress(tmp_path).outline_done is False


def test_final_status_line_window(tmp_path: Path) -> None:
    """状态行须在文件头 8 行内（与正文 parse_manuscript_status 同窗口）。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    late = "# 总纲 · 书\n" + "\n" * 9 + "> 状态：定稿｜2026-10-18\n\n## 核心纲\n\n| 卷 |\n|---|\n| 一 |\n"
    _touch(tmp_path, "总纲.md", late)
    assert scan_writing_progress(tmp_path).outline_done is False


def test_stage_volume_when_volume_draft(tmp_path: Path) -> None:
    """AC-003：卷纲草稿（起草中）不计入 volumes，段停留卷纲可继续编辑。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    _touch(tmp_path, "总纲.md", MASTER_FINAL)
    draft = VOLUME_FINAL.replace("> 状态：定稿｜2026-10-18", "> 状态：起草中｜2026-10-19")
    _touch(tmp_path, "卷纲/第01卷.md", draft)
    prog = scan_writing_progress(tmp_path)
    assert prog.volumes == []
    assert derive_stage(prog) == STAGE_VOLUME


def test_stage_volume_final_status_alone(tmp_path: Path) -> None:
    """REQ-20261030-200030：定稿状态行即计入 volumes（小节结构校验已随 payoff 防线移除）。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    _touch(tmp_path, "总纲.md", MASTER_FINAL)
    _touch(tmp_path, "卷纲/第01卷.md", "# 卷纲 · 第 1 卷\n> 状态：定稿｜2026-10-18\n\n## 发展阶段\n仅一节\n")
    prog = scan_writing_progress(tmp_path)
    assert prog.volumes == [1]
    assert derive_stage(prog) == STAGE_CHAPTER


def test_stage_volumes_mixed_finality(tmp_path: Path) -> None:
    """混合：第 1 卷定稿、第 2 卷起草中 → volumes 只含定稿卷。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    _touch(tmp_path, "总纲.md", MASTER_FINAL)
    _touch(tmp_path, "卷纲/第01卷.md", VOLUME_FINAL)
    draft = VOLUME_FINAL.replace("> 状态：定稿｜2026-10-18", "> 状态：起草中｜2026-10-19")
    _touch(tmp_path, "卷纲/第02卷.md", draft)
    assert scan_writing_progress(tmp_path).volumes == [1]


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


# ── WritingState（人工段指针载体，REQ-20261019-232617 FR-002）──────


def test_writing_state_roundtrip(tmp_path: Path) -> None:
    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_CHAPTER
    state.active_threads["author"] = "t1"
    save_writing_state(tmp_path, state)
    loaded = load_writing_state(tmp_path)
    assert loaded.confirmed_stage == STAGE_CHAPTER
    assert loaded.active_threads["author"] == "t1"


def test_writing_state_corruption_tolerant(tmp_path: Path) -> None:
    _touch(tmp_path, ".writing/state.json", "{ 不是 json")
    state = load_writing_state(tmp_path)
    assert state.confirmed_stage is None  # 回默认，不阻塞主流程


# ── 阶段闸依据（FR-006 / AC-008）──────────────────────────────────


def test_stage_allowed_kinds() -> None:
    assert stage_allowed_kinds(STAGE_THEME) == {KIND_THEME}
    assert stage_allowed_kinds(STAGE_OUTLINE) == {KIND_OUTLINE}
    assert stage_allowed_kinds(STAGE_VOLUME) == {KIND_VOLUME}
    # 文风卡在章纲段起草（DEC-009）
    assert stage_allowed_kinds(STAGE_CHAPTER) == {KIND_CHAPTER, KIND_STYLE}
    assert stage_allowed_kinds(STAGE_TEXT) == {KIND_TEXT}


# ── 阶段切换人工闸（REQ-20261019-232617 FR-001/002/005）────────────


# 2026-10-19 线上事故样本（工作区 5b09…848 主题.md 节选）：小节标题齐全但
# 正文全是占位、状态行「起草中（核心爽点已定稿）」——旧判定误判 theme_done
INCIDENT_THEME_DRAFT = """# 主题 · 书名待定
> 状态：起草中（核心爽点已定稿）｜会话 d70d052b

## 一句话主题
（待三件套齐后压成）

## 约束级（定稿，下游必须遵守）

### 核心爽点（已定稿）
**【核心爽点 · 定稿卡片】**
一句话：测试爽点

### 核心矛盾
（未定稿，待第 2 步）

### 核心爽点剧情
（未定稿，待第 3 步）
"""


def test_final_status_rejects_drafting_marker() -> None:
    """DEC-003：状态行含「起草中/进行中」不算定稿——事故样本里
    「起草中（核心爽点已定稿）」含「定稿」二字但整体是起草态。"""
    from contracts.writing_artifacts import is_final_status

    assert not is_final_status("> 状态：起草中（核心爽点已定稿）｜会话 d70d052b")
    assert not is_final_status("> 状态：进行中｜2026-10-19")
    assert is_final_status("> 状态：定稿｜2026-10-18")


def test_theme_incident_draft_not_done(tmp_path: Path) -> None:
    """AC-001 锚：事故占位草稿不得判 theme_done（不切段、不通知）。"""
    _touch(tmp_path, "主题.md", INCIDENT_THEME_DRAFT)
    prog = scan_writing_progress(tmp_path)
    assert prog.theme_done is False
    assert derive_stage(prog) == STAGE_THEME


def test_final_rejects_placeholder_sections(tmp_path: Path) -> None:
    """占位辅助检测（DEC-003）：必备小节正文是整行括号占位不算定稿。"""
    draft = THEME_FINAL.replace("谁靠什么在什么拉扯里走什么路。", "（待三件套齐后压成）")
    _touch(tmp_path, "主题.md", draft)
    assert scan_writing_progress(tmp_path).theme_done is False
    # 正常内容里的括号短句不误伤
    _touch(tmp_path, "主题.md", THEME_FINAL.replace("谁靠什么在什么拉扯里走什么路。", "谁靠什么（见第 3 卷）走什么路。"))
    assert scan_writing_progress(tmp_path).theme_done is True


def test_theme_final_regression_still_done(tmp_path: Path) -> None:
    """收紧回归：正常定稿照常 theme_done。"""
    _touch(tmp_path, "主题.md", THEME_FINAL)
    assert scan_writing_progress(tmp_path).theme_done is True


def test_confirmed_stage_roundtrip(tmp_path: Path) -> None:
    """人工段指针持久化（FR-002 切段唯一真相载体）。"""
    from contracts.writing_artifacts import save_writing_state

    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_OUTLINE
    save_writing_state(tmp_path, state)
    assert load_writing_state(tmp_path).confirmed_stage == STAGE_OUTLINE


def test_resolve_confirmed_stage_fallback_then_sticky(tmp_path: Path) -> None:
    """指针解析：缺失→目录扫描兜底（FR-005 迁移）；已设→产物变化不动摇（DEC-006/AC-009）。"""
    from contracts.writing_artifacts import resolve_confirmed_stage, save_writing_state

    # 空工作区：无指针，扫描兜底 theme
    assert resolve_confirmed_stage(tmp_path) == STAGE_THEME
    # 题材定稿落盘（产物推进）但无人确认：仍按扫描兜底推进（迁移语义）
    _touch(tmp_path, "主题.md", THEME_FINAL)
    assert resolve_confirmed_stage(tmp_path) == STAGE_OUTLINE
    # 人工确认进总纲后，题材被打回草稿：指针不动摇（AC-009）
    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_OUTLINE
    save_writing_state(tmp_path, state)
    _touch(tmp_path, "主题.md", INCIDENT_THEME_DRAFT)
    assert resolve_confirmed_stage(tmp_path) == STAGE_OUTLINE


def test_stage_labels() -> None:
    """阶段标签与 desktop 阶段条同名对齐（FR-011 5 段）。"""
    from contracts.writing_artifacts import STAGE_LABELS

    assert STAGE_LABELS[STAGE_THEME] == LABEL_THEME
    assert len(STAGE_LABELS) == 5
    assert LABEL_THEME == "题材"
    assert LABEL_CHAPTER == "章纲"
