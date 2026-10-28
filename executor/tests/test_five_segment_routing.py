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


def _route(ws: Path, **kwargs):
    return pipeline.route(ws, "thread-1", **kwargs)


def test_route_sticky_thread_within_conversation(tmp_path: Path) -> None:
    """会话粘性（REQ-20261019-222403 FR-001/DEC-001，AC-001）。

    同一客户端会话（base thread）的多条普通消息复用同一 langgraph 线程；
    不同会话各开各的线程；丢弃重开递增派生号。
    """
    d1 = _route(tmp_path)
    pipeline.commit_route(tmp_path, d1, "trace-1")
    d2 = _route(tmp_path)
    assert d2.thread_id == d1.thread_id  # 粘性：不换线程
    assert d2.new_session is False
    assert d2.conversation_id == "thread-1"

    # 另一会话（阶段切换后客户端新开的 thread）→ 不同线程
    d_other = pipeline.route(tmp_path, "thread-2")
    assert d_other.thread_id.startswith("thread-2:author:")
    assert d_other.thread_id != d1.thread_id

    # 丢弃重开：派生号递增，开新线程
    d3 = _route(tmp_path, fresh_session=True)
    pipeline.commit_route(tmp_path, d3, "trace-3")
    assert d3.thread_id != d1.thread_id
    assert d3.thread_id.endswith(":2")
    state = load_writing_state(tmp_path)
    assert state.conversation_forks["thread-1"] == 2
    # fork 后普通消息粘在新线程上
    d4 = _route(tmp_path)
    assert d4.thread_id == d3.thread_id


def test_route_resume_degrades_on_stale_trace(tmp_path: Path) -> None:
    """resume trace 失配（跨会话/已被清理）→ 降级普通消息续跑粘性线程（AC-002 边界）。"""
    d1 = _route(tmp_path)
    pipeline.commit_route(tmp_path, d1, "trace-1")
    stale = _route(tmp_path, resume_trace="trace-gone")
    assert stale.resume_degraded is True
    assert stale.thread_id == d1.thread_id  # 仍在原会话线程
    # 命中活跃 trace 的 resume 不降级
    state = load_writing_state(tmp_path)
    state.active_traces["author"] = "trace-live"
    save_writing_state(tmp_path, state)
    live = _route(tmp_path, resume_trace="trace-live")
    assert live.resume_degraded is False and live.new_session is False


def test_route_empty_workspace_starts_theme(tmp_path: Path) -> None:
    d = _route(tmp_path)
    assert d.agent_name == "author"
    assert d.phase == STAGE_THEME
    assert "题材" in d.directive and "进度摘要" in d.directive
    assert "主题：未落盘" in d.directive


def test_route_stage_progression(tmp_path: Path) -> None:
    """产物推进不再自动切段（FR-002 AC-002）：指针钉住后产物齐全仍留原段。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    d = _route(tmp_path)  # 未迁移工作区：扫描兜底（FR-005 升级语义）
    assert d.phase == "outline"
    # 人工段指针钉在题材段：产物再齐全也不切段
    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_THEME
    save_writing_state(tmp_path, state)
    assert _route(tmp_path).phase == STAGE_THEME
    (tmp_path / "总纲.md").write_text(MASTER_FINAL, encoding="utf-8")
    d = _route(tmp_path)
    assert d.phase == STAGE_THEME  # AC-002：打字发送留在原段
    # 人工确认进总纲后才切段
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "outline"
    save_writing_state(tmp_path, state)
    d = _route(tmp_path)
    assert d.phase == "outline"


def test_route_volume_directive_targets_latest_volume(tmp_path: Path) -> None:
    """卷纲段 directive 报活卷不报下一卷（2026-10-28 线上打回事故回归）。

    第 1 卷卷纲已落盘（定稿等审 / 被打回在改）时，directive 不得报
    「当前目标：第 2 卷」——降级 resume / 重启后新 run 会照 directive 把
    打回读成重做还没开工的下一卷。
    """
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    (tmp_path / "总纲.md").write_text(MASTER_FINAL, encoding="utf-8")
    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_VOLUME
    save_writing_state(tmp_path, state)

    # 无卷纲文件：开第 1 卷
    d = _route(tmp_path)
    assert "当前目标：第 1 卷卷纲" in d.directive

    # 第 1 卷定稿落盘（等审/在改）：活卷是第 1 卷，不出现第 2 卷
    vol_dir = tmp_path / "卷纲"
    vol_dir.mkdir()
    (vol_dir / "第1卷.md").write_text(VOLUME_FINAL, encoding="utf-8")
    d = _route(tmp_path)
    assert "当前活卷：第 1 卷" in d.directive
    assert "第 2 卷" not in d.directive

    # 起草中的卷文件同样算活卷（四步未走完，本卷还在写）
    (vol_dir / "第1卷.md").write_text(
        VOLUME_FINAL.replace("定稿", "起草中"), encoding="utf-8"
    )
    d = _route(tmp_path)
    assert "当前活卷：第 1 卷" in d.directive


def test_route_pointer_wins_and_persists(tmp_path: Path) -> None:
    """人工段指针优先于扫描；run 生命周期不清指针（FR-002，AC-004 镜像）。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_CHAPTER
    save_writing_state(tmp_path, state)

    d = _route(tmp_path)
    assert d.phase == STAGE_CHAPTER
    pipeline.commit_route(tmp_path, d, "trace-9")
    assert load_writing_state(tmp_path).confirmed_stage == STAGE_CHAPTER  # 不清

    # resume 同口径：指针段 + 原 会话线程
    state = load_writing_state(tmp_path)
    state.active_threads["author"] = "thread-1:author:1"
    state.active_traces["author"] = "trace-1"
    save_writing_state(tmp_path, state)
    d = _route(tmp_path, resume_trace="trace-1")
    assert d.new_session is False and d.thread_id == "thread-1:author:1"
    assert d.phase == STAGE_CHAPTER


def test_commit_route_pins_pointer_migration(tmp_path: Path) -> None:
    """FR-005 迁移：无指针工作区首 run 冻结当前扫描段（升级前在哪停哪）。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    assert load_writing_state(tmp_path).confirmed_stage is None
    d = _route(tmp_path)
    pipeline.commit_route(tmp_path, d, "trace-1")
    assert load_writing_state(tmp_path).confirmed_stage == "outline"


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


def test_pipeline_status_phase_live_derived(tmp_path: Path) -> None:
    """REQ-20261019-183150：phase 活推导——stale state.stage 不得压过目录事实。

    事故镜像：总纲只有起草中草稿，旧规则把 state.stage 写成 volume；
    定稿闸后 pipeline_status 须按目录回显 outline（AC-007 前置）。
    """
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    draft = MASTER_FINAL.replace("> 状态：定稿｜2026-10-18", "> 状态：起草中｜2026-10-19")
    (tmp_path / "总纲.md").write_text(draft, encoding="utf-8")
    state = load_writing_state(tmp_path)
    state.stage = STAGE_VOLUME  # 旧规则留下的 stale 快照
    save_writing_state(tmp_path, state)
    status = pipeline.pipeline_status(tmp_path)
    assert status["phase"] == "outline"


def test_next_unit_labels_five_stages(tmp_path: Path) -> None:
    """五段标签齐全（FR-011 阶段条同名对齐）。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    (tmp_path / "总纲.md").write_text(MASTER_FINAL, encoding="utf-8")
    (tmp_path / "卷纲").mkdir()
    (tmp_path / "卷纲" / "第01卷.md").write_text(VOLUME_FINAL, encoding="utf-8")
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


def test_current_stage_honors_pointer(tmp_path: Path) -> None:
    """装配用段解析与 route 同口径——人工段指针优先于扫描（FR-002）。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    assert pipeline.current_stage(tmp_path) == "outline"  # 未迁移：扫描兜底
    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_CHAPTER
    save_writing_state(tmp_path, state)
    assert pipeline.current_stage(tmp_path) == STAGE_CHAPTER


# ── 就绪判定与通知信号（REQ-20261019-232617 FR-001，AC-001/004/009）──

INCIDENT_THEME_DRAFT = """# 主题 · 书名待定
> 状态：起草中（核心爽点已定稿）｜会话 d70d052b

## 一句话主题
（待三件套齐后压成）

## 约束级（定稿，下游必须遵守）

### 核心爽点（已定稿）
卡
### 核心矛盾
（未定稿，待第 2 步）
### 核心爽点剧情
（未定稿，待第 3 步）
"""


def _pin(ws: Path, stage: str) -> None:
    state = load_writing_state(ws)
    state.confirmed_stage = stage
    save_writing_state(ws, state)


def test_pipeline_status_ready_edges(tmp_path: Path) -> None:
    # 题材定稿 + 指针题材 + 无未决拍板 → 就绪，下一站总纲
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    _pin(tmp_path, STAGE_THEME)
    status = pipeline.pipeline_status(tmp_path)
    assert status["ready"] == {"ready": True, "next": "outline"}

    # 事故占位草稿：不就绪（AC-001）
    (tmp_path / "主题.md").write_text(INCIDENT_THEME_DRAFT, encoding="utf-8")
    status = pipeline.pipeline_status(tmp_path)
    assert status["ready"]["ready"] is False and status["ready"]["next"] is None

    # 有未决拍板（访谈没收尾）：产物齐全也不算就绪（FR-001 附加条件）
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    state = load_writing_state(tmp_path)
    state.active_traces["author"] = "trace-pending"
    save_writing_state(tmp_path, state)
    status = pipeline.pipeline_status(tmp_path)
    assert status["ready"]["ready"] is False

    # 正文末段：无下一站
    _pin(tmp_path, "text")
    status = pipeline.pipeline_status(tmp_path)
    assert status["ready"] == {"ready": False, "next": None}


def test_pipeline_status_ready_no_fallback_behind_pointer(tmp_path: Path) -> None:
    """AC-009：指针在总纲、题材被打回草稿 → 阶段不回落、不亮就绪。"""
    (tmp_path / "主题.md").write_text(THEME_FINAL, encoding="utf-8")
    (tmp_path / "总纲.md").write_text(MASTER_FINAL, encoding="utf-8")
    _pin(tmp_path, "outline")
    (tmp_path / "主题.md").write_text(INCIDENT_THEME_DRAFT, encoding="utf-8")
    status = pipeline.pipeline_status(tmp_path)
    assert status["phase"] == "outline"
    assert status["ready"]["ready"] is False
