"""五段 harness 工具与阶段闸测试（REQ-20261018-115508 FR-003/004/005/006）。

锁定：
  - save_artifact 校验编辑四道保险（AC-006）：先读后改 / 精确+归一匹配 /
    锚点唯一性 / 失败不落盘；重试上限退化 full（DEC-007）。
  - read_artifact：缺产物指引、小节切片、读取登记。
  - stage_gate（AC-008）：越段写拦截、回写工具阶段限制。
  - append_chapter_deviation 前置（AC-007）：仅人修定稿后可用。
  - 工具 schema 无路径参数（AC-005）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
load_package(_HARNESS_DIR)

from harness_current.middleware.stage_gate import StageGateMiddleware  # noqa: E402
from harness_current.tools.append_chapter_deviation import (  # noqa: E402
    build_append_deviation_tool,
)
from harness_current.tools.read_artifact import build_read_artifact_tool  # noqa: E402
from harness_current.tools.rewrite_rhythm_table import (  # noqa: E402
    build_rewrite_rhythm_tool,
)
from harness_current.tools.save_artifact import build_save_artifact_tool  # noqa: E402
from harness_current.tools.update_foreshadow_table import (  # noqa: E402
    build_update_foreshadow_tool,
)

MASTER = """# 总纲 · 测试书
> 状态：定稿｜2026-10-18

## 核心纲

| 卷 | 小目标 | 地图 | BOSS | 关键节点 |
|---|---|---|---|---|
| 一 | 活下去 | 垃圾场 | 工头 | 捡神器 |

### 伏笔登记表
| 编号 | 伏笔 | 埋于章 | 收于章 | 状态 |
|---|---|---|---|---|
| F-01 | 神器来历 | 001 | 待定 | 活跃 |
"""


def _mk_save(ws: Path):
    registry: dict = {}
    failures: dict = {}
    return (
        build_save_artifact_tool(ws, registry, failures),
        registry,
        failures,
    )


def _call(tool, **kwargs) -> str:
    return tool.invoke(kwargs)


# ── save_artifact 四道保险（AC-006）───────────────────────────────


def test_edit_requires_prior_read(tmp_path: Path) -> None:
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    save, _, _ = _mk_save(tmp_path)
    out = _call(save, kind="outline", mode="edit",
                anchor="| 一 | 活下去 |", replacement="| 一 | 活下去改 |")
    assert "先调 read_artifact" in out
    assert (tmp_path / "总纲.md").read_text(encoding="utf-8") == MASTER  # 文件未变


def test_edit_unique_anchor_applies_localized(tmp_path: Path) -> None:
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    save, registry, _ = _mk_save(tmp_path)
    reader = build_read_artifact_tool(tmp_path, registry)
    _call(reader, kind="outline")
    out = _call(save, kind="outline", mode="edit",
                anchor="| 一 | 活下去 | 垃圾场 | 工头 | 捡神器 |",
                replacement="| 一 | 活下去 | 垃圾场 | 工头 | 捡到神器（爽点） |")
    assert "已落盘" in out
    updated = (tmp_path / "总纲.md").read_text(encoding="utf-8")
    assert "捡到神器（爽点）" in updated
    assert "伏笔登记表" in updated and "F-01" in updated  # 分区外内容不动
    assert updated.count("捡到神器（爽点）") == 1  # 只替换目标处（T2：去掉永真断言）
    assert updated.count("| 一 |") == 1  # 表行数不变


def test_edit_ambiguous_anchor_fails_closed(tmp_path: Path) -> None:
    text = MASTER + "\n| 一 | 活下去 |\n"
    (tmp_path / "总纲.md").write_text(text, encoding="utf-8")
    save, registry, _ = _mk_save(tmp_path)
    build_read_artifact_tool(tmp_path, registry).invoke({"kind": "outline"})
    out = _call(save, kind="outline", mode="edit",
                anchor="| 一 | 活下去 |", replacement="X")
    assert "不唯一" in out
    assert (tmp_path / "总纲.md").read_text(encoding="utf-8") == text


def test_edit_normalized_match(tmp_path: Path) -> None:
    """行尾空白差异走归一匹配仍命中（DEC-007 多级归一）。"""
    text = MASTER.replace("捡神器 |", "捡神器 |   ")  # 行尾多空白
    (tmp_path / "总纲.md").write_text(text, encoding="utf-8")
    save, registry, _ = _mk_save(tmp_path)
    build_read_artifact_tool(tmp_path, registry).invoke({"kind": "outline"})
    out = _call(save, kind="outline", mode="edit",
                anchor="| 一 | 活下去 | 垃圾场 | 工头 | 捡神器 |",
                replacement="| 一 | 活下去 | 垃圾场 | 工头 | 捡到神器 |")
    assert "已落盘" in out
    assert "捡到神器" in (tmp_path / "总纲.md").read_text(encoding="utf-8")


def test_edit_retry_cap_forces_full(tmp_path: Path) -> None:
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    save, registry, failures = _mk_save(tmp_path)
    build_read_artifact_tool(tmp_path, registry).invoke({"kind": "outline"})
    for _ in range(3):
        out = _call(save, kind="outline", mode="edit", anchor="不存在的锚点", replacement="X")
        assert "文件未变" in out
    assert failures["总纲.md"] == 3
    out = _call(save, kind="outline", mode="edit", anchor="| 一 |", replacement="X")
    assert "mode=full" in out  # 堵 edit、导流 full
    out = _call(save, kind="outline", mode="full", content=MASTER.replace("捡神器", "捡到神器"))
    assert "已落盘" in out
    assert failures.get("总纲.md") is None  # 成功后计数清零


def test_full_new_file_without_read(tmp_path: Path) -> None:
    save, _, _ = _mk_save(tmp_path)
    out = _call(save, kind="theme", mode="full", content="# 主题 · 书\n> 状态：定稿\n")
    assert "已落盘" in out
    assert (tmp_path / "主题.md").exists()


def test_full_existing_requires_read(tmp_path: Path) -> None:
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    save, _, _ = _mk_save(tmp_path)
    out = _call(save, kind="outline", mode="full", content="覆盖")
    assert "先调 read_artifact" in out
    assert (tmp_path / "总纲.md").read_text(encoding="utf-8") == MASTER


def test_edit_nonexistent_artifact(tmp_path: Path) -> None:
    save, registry, _ = _mk_save(tmp_path)
    build_read_artifact_tool(tmp_path, registry).invoke({"kind": "outline"})
    out = _call(save, kind="volume_outline", volume=1, mode="edit", anchor="a", replacement="b")
    assert "尚不存在" in out


# ── save_artifact 产物取证（REQ-20261019-203500 FR-001/DEC-001/DEC-004）──


class _FakeRecorder:
    """取证桩：记录 record_artifact_revision 调用与降级标记。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.degraded: list[str] = []
        self._fail = fail

    def record_artifact_revision(
        self, trace_id, agent_name, *, file_path, content, tool_name=None,
        parent_revision_id=None, content_hash=None, tool_call_id=None,
    ):
        if self._fail:
            raise RuntimeError("取证故障注入")
        self.calls.append({
            "trace_id": trace_id, "agent_name": agent_name, "file_path": file_path,
            "content": content, "tool_name": tool_name, "tool_call_id": tool_call_id,
        })
        return "artifact-rev-fake"

    def _mark_capture_degraded(self, trace_id, reason):
        self.degraded.append(reason)


def _mk_save_rec(ws: Path, recorder, trace_id: str = "trace-x"):
    registry: dict = {}
    failures: dict = {}
    return build_save_artifact_tool(
        ws, registry, failures, recorder=recorder, trace_id=trace_id,
    )


def test_full_save_records_artifact_revision(tmp_path: Path) -> None:
    rec = _FakeRecorder()
    save = _mk_save_rec(tmp_path, rec)
    content = "# 主题 · 书\n> 状态：定稿\n"
    out = _call(save, kind="theme", mode="full", content=content)
    assert "已落盘" in out
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["trace_id"] == "trace-x"
    assert call["agent_name"] == "author-subagent"
    assert call["file_path"] == "主题.md"
    assert call["content"] == content
    assert call["tool_name"] == "save_artifact"
    # 取证身份为内容寻址（R1/P0）：同内容幂等、异内容不冲突的契约基础
    assert isinstance(call["tool_call_id"], str) and call["tool_call_id"].startswith("save_artifact:")


def test_full_then_edit_chains_revisions(tmp_path: Path) -> None:
    """R1/P0 回归：同 run 内 full→edit（内容不同）必须产生 2 条修订且 parent 链接。

    修复前：取证不传 tool_call_id → 恒定 legacy 键 → recorder call_paths 哈希
    冲突守卫把第二次落盘的取证静默降级（revision_events==1）。
    """
    from app.platform.trace.recorder import TraceRecorder
    from app.schemas.screenplay import ThreadSummary

    thread = ThreadSummary(
        thread_id="thread-forensic-chain", workspace_id="ws-forensic-chain",
        session_name="s", workspace_path=str(tmp_path),
        created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )
    recorder = TraceRecorder()
    run = recorder.create_run(thread, "/screenplay/generate")
    registry: dict = {}
    save = build_save_artifact_tool(
        tmp_path, registry, {}, recorder=recorder, trace_id=run.trace_id,
    )
    reader = build_read_artifact_tool(tmp_path, registry)
    assert "已落盘" in _call(save, kind="theme", mode="full", content="# 主题 · 书\n> 状态：定稿\n")
    _call(reader, kind="theme")
    assert "已落盘" in _call(save, kind="theme", mode="edit", anchor="定稿", replacement="起草中")
    events = [
        event for event in recorder.read_trace_events(run.trace_id) or []
        if event.type == "artifact_revision"
    ]
    assert len(events) == 2
    assert events[1].artifact["parent_revision_id"] == events[0].artifact_revision_id
    assert "起草中" in (tmp_path / "主题.md").read_text(encoding="utf-8")


def test_chapter_text_save_records_revision(tmp_path: Path) -> None:
    rec = _FakeRecorder()
    save = _mk_save_rec(tmp_path, rec)
    out = _call(save, kind="chapter_text", chapter=1, mode="full",
                content="# 第001章 · 开局\n")
    assert "已落盘" in out
    assert rec.calls[0]["file_path"] == "正文/第001章.md"


def test_edit_save_records_full_text_after_write(tmp_path: Path) -> None:
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    rec = _FakeRecorder()
    registry: dict = {}
    save = build_save_artifact_tool(tmp_path, registry, {}, recorder=rec, trace_id="trace-x")
    _call(build_read_artifact_tool(tmp_path, registry), kind="outline")
    out = _call(save, kind="outline", mode="edit",
                anchor="| 一 | 活下去 |", replacement="| 一 | 活下去改 |")
    assert "已落盘" in out
    assert len(rec.calls) == 1
    assert rec.calls[0]["content"] == (tmp_path / "总纲.md").read_text(encoding="utf-8")
    assert "活下去改" in rec.calls[0]["content"]


def test_capture_failure_degrades_without_blocking_write(tmp_path: Path) -> None:
    """DEC-004：取证失败静默降级——落盘照常成功，trace 留降级痕迹。"""
    rec = _FakeRecorder(fail=True)
    save = _mk_save_rec(tmp_path, rec)
    out = _call(save, kind="theme", mode="full", content="# 主题 · 书\n")
    assert "已落盘" in out
    assert (tmp_path / "主题.md").read_text(encoding="utf-8") == "# 主题 · 书\n"
    assert rec.degraded and "save_artifact_capture_failed" in rec.degraded[0]


def test_edit_capture_failure_degrades_without_blocking_write(tmp_path: Path) -> None:
    """R2（AC-002 边界）：mode=edit 注入取证故障——落盘照常成功，留降级痕迹。"""
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    rec = _FakeRecorder(fail=True)
    registry: dict = {}
    save = build_save_artifact_tool(tmp_path, registry, {}, recorder=rec, trace_id="trace-x")
    _call(build_read_artifact_tool(tmp_path, registry), kind="outline")
    out = _call(save, kind="outline", mode="edit",
                anchor="| 一 | 活下去 |", replacement="| 一 | 活下去改 |")
    assert "已落盘" in out
    updated = (tmp_path / "总纲.md").read_text(encoding="utf-8")
    assert "活下去改" in updated and "伏笔登记表" in updated
    assert rec.degraded and "save_artifact_capture_failed" in rec.degraded[0]


def test_recorder_without_trace_id_skips_capture(tmp_path: Path) -> None:
    """R3（FR-001）：recorder 在位但 trace_id 缺失——跳过取证，落盘不受影响。"""
    rec = _FakeRecorder()
    registry: dict = {}
    save = build_save_artifact_tool(tmp_path, registry, {}, recorder=rec, trace_id=None)
    out = _call(save, kind="theme", mode="full", content="# 主题 · 书\n")
    assert "已落盘" in out
    assert (tmp_path / "主题.md").exists()
    assert rec.calls == []
    assert rec.degraded == []


def test_capture_failure_without_usable_degrade_marker_still_writes(tmp_path: Path) -> None:
    """P3 收口：降级标记器缺失或自身故障时，取证失败仍不得阻断落盘（DEC-004 兜底完备性）。"""

    class _MarkerlessRecorder:
        def record_artifact_revision(self, *args, **kwargs):
            raise RuntimeError("取证故障注入")

    class _MarkerBrokenRecorder(_MarkerlessRecorder):
        def _mark_capture_degraded(self, trace_id, reason):
            raise RuntimeError("标记器也炸")

    for name, rec in (("no-marker", _MarkerlessRecorder()), ("broken-marker", _MarkerBrokenRecorder())):
        ws = tmp_path / name
        ws.mkdir()
        save = _mk_save_rec(ws, rec)
        out = _call(save, kind="theme", mode="full", content="# 主题 · 书\n")
        assert "已落盘" in out, name
        assert (ws / "主题.md").exists(), name


def test_no_recorder_still_writes(tmp_path: Path) -> None:
    """无追踪场景（recorder/trace_id 缺失）：落盘不受影响。"""
    save, _, _ = _mk_save(tmp_path)
    out = _call(save, kind="theme", mode="full", content="# 主题 · 书\n")
    assert "已落盘" in out
    assert (tmp_path / "主题.md").exists()


def test_build_hook_wires_recorder_from_ctx(tmp_path: Path) -> None:
    """build(abc) 装配钩子从 ctx 接 recorder/trace_id，agent 名用 trace 埋点名。"""
    from harness_current.tools.save_artifact import build as build_save

    rec = _FakeRecorder()
    abc = SimpleNamespace(
        workspace_path=tmp_path,
        trace_name="author-subagent",
        ctx=SimpleNamespace(trace_recorder=rec, trace_id="trace-y"),
    )
    tool = build_save(abc)
    out = _call(tool, kind="theme", mode="full", content="# 主题 · 书\n")
    assert "已落盘" in out
    assert rec.calls[0]["trace_id"] == "trace-y"
    assert rec.calls[0]["agent_name"] == "author-subagent"


def test_repeated_same_content_save_is_idempotent(tmp_path: Path) -> None:
    """AC-001：同内容二次落盘不产生重复修订（真实 recorder 端到端）。"""
    from app.platform.trace.recorder import TraceRecorder
    from app.schemas.screenplay import ThreadSummary

    thread = ThreadSummary(
        thread_id="thread-forensic", workspace_id="ws-forensic",
        session_name="s", workspace_path=str(tmp_path),
        created_at="2026-10-19T00:00:00+00:00",
        updated_at="2026-10-19T00:00:00+00:00",
    )
    recorder = TraceRecorder()
    run = recorder.create_run(thread, "/screenplay/generate")
    registry: dict = {}
    save = build_save_artifact_tool(
        tmp_path, registry, {}, recorder=recorder, trace_id=run.trace_id,
    )
    reader = build_read_artifact_tool(tmp_path, registry)
    content = "# 主题 · 书\n> 状态：定稿\n"
    assert "已落盘" in _call(save, kind="theme", mode="full", content=content)
    _call(reader, kind="theme")
    assert "已落盘" in _call(save, kind="theme", mode="full", content=content)
    events = [
        event for event in recorder.read_trace_events(run.trace_id) or []
        if event.type == "artifact_revision"
    ]
    assert len(events) == 1
    assert events[0].artifact["logical_key"] == "/主题.md"
    assert "output" in events[0].payload_refs


# ── read_artifact ─────────────────────────────────────────────────


def test_read_missing_artifact_guidance(tmp_path: Path) -> None:
    reader = build_read_artifact_tool(tmp_path, {})
    out = _call(reader, kind="volume_outline", volume=2)
    assert "不存在" in out and "上游" in out


def test_read_section_slice(tmp_path: Path) -> None:
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    reader = build_read_artifact_tool(tmp_path, {})
    out = _call(reader, kind="outline", section="伏笔登记表")
    assert "F-01" in out
    assert "小目标" not in out  # 只取了目标小节，核心纲表头不在切片里


# ── stage_gate（AC-008）───────────────────────────────────────────


def _req(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "t1"})


def test_stage_gate_blocks_cross_stage_save() -> None:
    gate = StageGateMiddleware("chapter")
    blocked = gate._maybe_block(_req("save_artifact", kind="theme"))
    assert blocked is not None and "阶段闸" in blocked.content
    assert gate._maybe_block(_req("save_artifact", kind="chapter_outline")) is None
    assert gate._maybe_block(_req("save_artifact", kind="style")) is None  # DEC-009


def test_stage_gate_writeback_tool_stage_limits() -> None:
    theme_gate = StageGateMiddleware("theme")
    assert theme_gate._maybe_block(_req("update_foreshadow_table")) is not None
    assert theme_gate._maybe_block(_req("rewrite_rhythm_table", volume=1, table="t")) is not None
    chapter_gate = StageGateMiddleware("chapter")
    assert chapter_gate._maybe_block(_req("update_foreshadow_table")) is None
    text_gate = StageGateMiddleware("text")
    assert text_gate._maybe_block(_req("append_chapter_deviation", chapter=1)) is None
    chapter_gate2 = StageGateMiddleware("chapter")
    assert chapter_gate2._maybe_block(_req("append_chapter_deviation", chapter=1)) is not None


def test_stage_gate_passes_read_and_confirm() -> None:
    gate = StageGateMiddleware("theme")
    assert gate._maybe_block(_req("read_artifact", kind="outline")) is None
    assert gate._maybe_block(_req("confirm_with_user")) is None


def test_stage_gate_wrap_blocks_and_keeps_blocking() -> None:
    """T4/S2：wrap_tool_call 真链路拦截；达上限后持续拦截（fail-closed）。"""
    gate = StageGateMiddleware("theme", max_blocks=2)
    calls = []
    def _handler(req):
        calls.append(req)
        return "ok"

    for _ in range(4):
        out = gate.wrap_tool_call(_req("save_artifact", kind="chapter_outline"), _handler)
        assert "阶段闸" in out.content  # 每次都拦，包括超上限之后
    assert calls == []  # handler 从未执行


def test_stage_gate_wrap_passes_legal_call() -> None:
    gate = StageGateMiddleware("theme")
    calls = []
    def _handler(req):
        calls.append(req)
        return "ok"

    out = gate.wrap_tool_call(_req("read_artifact", kind="theme"), _handler)
    assert out == "ok" and len(calls) == 1


def test_stage_gate_build_legacy_denies_all_writes(tmp_path: Path) -> None:
    """S1/C6：legacy 工作区装配出全拒闸（FR-012 只读）。"""
    import harness_current.middleware.stage_gate as sg_mod

    (tmp_path / "outline").mkdir()
    (tmp_path / "outline" / "世界观.md").write_text("三步制", encoding="utf-8")
    abc = SimpleNamespace(workspace_path=tmp_path)
    gate = sg_mod.build(abc)
    for kind in ("theme", "outline", "volume_outline", "chapter_outline", "chapter_text", "style"):
        assert gate._maybe_block(_req("save_artifact", kind=kind)) is not None


# ── stage_gate 标注任务放行（REQ-20261027-213246 FR-005/DEC-004）──


def _write_annotations(ws: Path, items: list[dict]) -> None:
    import json as _json

    ann_dir = ws / ".writing"
    ann_dir.mkdir(parents=True, exist_ok=True)
    (ann_dir / "annotations.json").write_text(
        _json.dumps({"version": 1, "annotations": items}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_annotation_task_kinds_reads_dispatched_pending(tmp_path: Path) -> None:
    """放行集 = 已进任务包且仍待处理的标注所属 kind；其余不计。"""
    from harness_current.middleware.stage_gate import annotation_task_kinds

    _write_annotations(tmp_path, [
        {"artifact_rel": "总纲.md", "status": "pending", "dispatched": True},
        {"artifact_rel": "卷纲/第01卷.md", "status": "pending", "dispatched": True},
        {"artifact_rel": "文风.md", "status": "pending", "dispatched": False},   # 未派发
        {"artifact_rel": "正文/第001章开局.md", "status": "resolved", "dispatched": True},  # 已解决
        {"artifact_rel": "主题.md", "status": "pending", "dispatched": True},    # 范围外（parse None）
    ])
    assert annotation_task_kinds(tmp_path) == {"outline", "volume_outline"}
    # 文件缺失 → 空集（闸门语义不变）
    empty = tmp_path / "empty-ws"
    empty.mkdir()
    assert annotation_task_kinds(empty) == set()


def test_stage_gate_annotation_widening_edit_only(tmp_path: Path) -> None:
    """正文段处理标注任务：任务包内 kind 的锚点 edit 放行，full 仍拦。"""
    import harness_current.middleware.stage_gate as sg_mod

    _write_annotations(tmp_path, [
        {"artifact_rel": "总纲.md", "status": "pending", "dispatched": True},
    ])
    abc = SimpleNamespace(workspace_path=tmp_path)
    gate = sg_mod.build(abc)
    assert gate.stage == "theme"  # 空工作区目录扫描兜底段；不影响断言口径
    # theme 段上：outline 属任务包 → 锚点 edit 放行、full 拦截
    assert gate._maybe_block(
        _req("save_artifact", kind="outline", mode="edit", anchor="a", replacement="b")
    ) is None
    blocked_full = gate._maybe_block(
        _req("save_artifact", kind="outline", mode="full", content="x")
    )
    assert blocked_full is not None and "阶段闸" in blocked_full.content
    # 任务包外 kind（text）照拦
    assert gate._maybe_block(_req("save_artifact", kind="chapter_text")) is not None
    assert gate._maybe_block(_req("update_foreshadow_table")) is not None
    assert gate._maybe_block(_req("read_artifact", kind="theme")) is None  # 读不拦


def test_stage_gate_build_honors_pointer(tmp_path: Path) -> None:
    """FR-002：人工段指针同步切换闸门（contracts.resolve_confirmed_stage 同口径）。"""
    import harness_current.middleware.stage_gate as sg_mod
    from contracts.writing_artifacts import (
        STAGE_CHAPTER,
        load_writing_state,
        save_writing_state,
    )

    theme_final = (
        "# 主题 · 书\n> 状态：定稿\n\n## 一句话主题\nx\n\n## 约束级\n"
        "### 核心爽点\nx\n### 核心矛盾\nx\n### 核心爽点剧情\nx\n"
    )
    (tmp_path / "主题.md").write_text(theme_final, encoding="utf-8")
    state = load_writing_state(tmp_path)
    state.confirmed_stage = STAGE_CHAPTER
    save_writing_state(tmp_path, state)
    abc = SimpleNamespace(workspace_path=tmp_path)
    gate = sg_mod.build(abc)
    # 扫描段是 outline（总纲未落盘），但指针=chapter：章纲可写、总纲被拦
    assert gate._maybe_block(_req("save_artifact", kind="chapter_outline")) is None
    assert gate._maybe_block(_req("save_artifact", kind="outline")) is not None


# ── append_chapter_deviation 前置（AC-007）────────────────────────


def test_harness_has_no_handoff_tool() -> None:
    """AC-005：模型工具箱无跳段工具——harness 包不装配 handoff（FR-004 收权）。"""
    import harness_current.tools as tools_pkg

    assert not hasattr(tools_pkg, "handoff")
    assert not hasattr(tools_pkg, "build_handoff_tool")



def _mk_chapter_ws(tmp_path: Path, *, finalized: bool) -> None:
    (tmp_path / "章纲").mkdir(exist_ok=True)
    (tmp_path / "正文").mkdir(exist_ok=True)
    (tmp_path / "章纲" / "第001章.md").write_text("# 章纲 · 第001章\n> 状态：定稿\n", encoding="utf-8")
    status = "人修定稿" if finalized else "初稿"
    (tmp_path / "正文" / "第001章开局.md").write_text(
        f"# 第001章 · 开局\n> 状态：{status}｜2026-10-18\n\n正文", encoding="utf-8"
    )


def test_deviation_requires_finalized_manuscript(tmp_path: Path) -> None:
    _mk_chapter_ws(tmp_path, finalized=False)
    tool = build_append_deviation_tool(tmp_path)
    out = _call(tool, chapter=1, note="钩子改为悬念收梢")
    assert "拒绝" in out and "初稿" in out
    assert "实际偏差" not in (tmp_path / "章纲" / "第001章.md").read_text(encoding="utf-8")


def test_deviation_appends_after_finalized(tmp_path: Path) -> None:
    _mk_chapter_ws(tmp_path, finalized=True)
    tool = build_append_deviation_tool(tmp_path)
    out = _call(tool, chapter=1, note="钩子改为悬念收梢")
    assert "已追加" in out
    content = (tmp_path / "章纲" / "第001章.md").read_text(encoding="utf-8")
    assert "实际偏差" in content and "钩子改为悬念收梢" in content
    assert content.startswith("# 章纲 · 第001章")  # 卡内原内容不动


# ── 回写工具分区替换（FR-005）─────────────────────────────────────


def test_foreshadow_table_section_replace(tmp_path: Path) -> None:
    (tmp_path / "总纲.md").write_text(MASTER, encoding="utf-8")
    tool = build_update_foreshadow_tool(tmp_path)
    new_table = "| 编号 | 伏笔 | 埋于章 | 收于章 | 状态 |\n|---|---|---|---|---|\n| F-01 | 神器来历 | 001 | 005 | 待收 |"
    out = _call(tool, entries=new_table)
    assert "已更新" in out
    updated = (tmp_path / "总纲.md").read_text(encoding="utf-8")
    assert "待收" in updated and "活跃" not in updated
    assert "核心纲" in updated and "捡神器" in updated  # 其余分区不动


def test_rhythm_table_section_replace(tmp_path: Path) -> None:
    vol = tmp_path / "卷纲"
    vol.mkdir()
    (vol / "第01卷.md").write_text(
        "# 卷纲 · 第 1 卷\n> 状态：定稿\n\n## 爽点节奏\n旧表\n\n## 质检\n保留", encoding="utf-8"
    )
    tool = build_rewrite_rhythm_tool(tmp_path)
    out = _call(tool, volume=1, table="本卷章号范围：1-3\n| 章区间 | 爽点 | 级别 |\n|---|---|---|\n| 1-3 | 捡宝 | 小 |")
    assert "已回写" in out
    updated = (vol / "第01卷.md").read_text(encoding="utf-8")
    assert "1-3" in updated and "旧表" not in updated and "保留" in updated



# ── 工具 schema 无路径参数（AC-005）───────────────────────────────

_ALLOWED_PROPS = {
    "kind", "volume", "chapter", "title", "mode", "anchor", "replacement",
    "content", "section", "entries", "table", "note", "draft",
    "question", "options", "multi_select",
}


def test_tool_schemas_have_no_path_params(tmp_path: Path) -> None:
    tools = [
        *_mk_save(tmp_path)[:1],
        build_read_artifact_tool(tmp_path, {}),
        build_update_foreshadow_tool(tmp_path),
        build_rewrite_rhythm_tool(tmp_path),
        build_append_deviation_tool(tmp_path),
    ]
    for tool in tools:
        schema = tool.args_schema.model_json_schema()
        props = set(schema.get("properties", {}).keys())
        assert props <= _ALLOWED_PROPS, f"{tool.name} 出现白名单外参数: {props - _ALLOWED_PROPS}"
        for banned in ("path", "file_path", "rel", "filename", "dir", "directory"):
            assert banned not in props, f"{tool.name} 含路径参数 {banned}"


# ── REQ-20261029-094930：卷纲写入统一检查、草稿隔离 ────────────────

_VOLUME = """# 卷纲 · 第 1 卷
> 状态：定稿

## 发展阶段
保留阶段安排
## 爽点节奏表
本卷章号范围：1-1
| 章号 | 剧情安排 | 技能条目／组合成分 | 行为档位 | 节奏级别 |
|---|---|---|---|---|
| 1 | 原剧情 | 【爽】条目甲 | 小 | 小 |
## 爽点质检表
保留质检
## 卷末钩子
保留钩子
"""




def test_volume_draft_reads_and_edits_own_baseline(tmp_path: Path) -> None:
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(_VOLUME, encoding="utf-8")
    draft_path = tmp_path / "卷纲/第01卷-修订草稿.md"
    draft_text = _VOLUME.replace("原剧情", "草稿剧情")
    draft_path.write_text(draft_text, encoding="utf-8")
    registry = {}
    reader = build_read_artifact_tool(tmp_path, registry)
    out = _call(reader, kind="volume_outline", volume=1, draft=True)
    assert "草稿剧情" in out
    assert not registry.get("卷纲/第01卷.md")
    assert registry.get("卷纲/第01卷-修订草稿.md")
    save = build_save_artifact_tool(tmp_path, registry, {})
    out = _call(save, kind="volume_outline", volume=1, draft=True,
                anchor="草稿剧情", replacement="修订剧情")
    assert "已落盘" in out
    assert "修订剧情" in draft_path.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8") == _VOLUME


def test_volume_draft_flag_cannot_redirect_other_artifact(tmp_path: Path) -> None:
    (tmp_path / "主题.md").write_text("原主题", encoding="utf-8")
    reader = build_read_artifact_tool(tmp_path, {})
    out = _call(reader, kind="theme", draft=True)
    assert "仅卷纲" in out
    save, _, _ = _mk_save(tmp_path)
    out = _call(save, kind="theme", mode="full", content="新主题", draft=True)
    assert "仅卷纲" in out
    assert (tmp_path / "主题.md").read_text(encoding="utf-8") == "原主题"


def test_volume_write_lands_directly_without_checks(tmp_path: Path) -> None:
    """REQ-20261030-200030：卷纲保存/回写不再做机械校验，直接落盘。"""
    registry = {}
    save = build_save_artifact_tool(tmp_path, registry, {})
    out = _call(save, kind="volume_outline", volume=1, mode="full", content=_VOLUME)
    assert "已落盘" in out
    assert (tmp_path / "卷纲/第01卷.md").read_text(encoding="utf-8") == _VOLUME


@pytest.mark.parametrize("mode", ["full", "edit"])
def test_volume_detects_changes_since_read(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(_VOLUME, encoding="utf-8")
    registry = {}
    _call(build_read_artifact_tool(tmp_path, registry), kind="volume_outline", volume=1)
    latest = _VOLUME.replace("保留钩子", "作者刚修改的钩子")
    path.write_text(latest, encoding="utf-8")
    save = build_save_artifact_tool(tmp_path, registry, {})
    out = _call(save, kind="volume_outline", volume=1, mode=mode,
                content=_VOLUME, anchor="原剧情", replacement="新剧情")
    assert "已变化" in out and "重新" in out
    assert path.read_text(encoding="utf-8") == latest


@pytest.mark.parametrize("old_heading", [False, True])
def test_rhythm_rewrite_supports_new_and_old_section_anchors(tmp_path: Path, old_heading: bool) -> None:
    """AC-004：新节名「剧情推进表」与旧节名「爽点节奏表」都能整节回写，其余节不动。"""
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    text = _VOLUME if old_heading else _VOLUME.replace("爽点节奏表", "剧情推进表")
    path.write_text(text, encoding="utf-8")
    tool = build_rewrite_rhythm_tool(tmp_path)
    out = _call(tool, volume=1, table="本卷章号范围：1-1\n新推进表内容")
    assert "已回写" in out
    final = path.read_text(encoding="utf-8")
    section = "爽点节奏表" if old_heading else "剧情推进表"
    prefix = text.split(f"## {section}\n", 1)[0] + f"## {section}\n"
    suffix = "## 爽点质检表" + text.split("## 爽点质检表", 1)[1]
    assert final.startswith(prefix) and final.endswith(suffix)
    assert "新推进表内容" in final


def test_rhythm_rewrite_missing_section_reports_failure(tmp_path: Path) -> None:
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(_VOLUME.replace("## 爽点节奏表", "## 别的小节"), encoding="utf-8")
    tool = build_rewrite_rhythm_tool(tmp_path)
    out = _call(tool, volume=1, table="新表")
    assert "失败" in out and "剧情推进表" in out
    assert "## 别的小节" in path.read_text(encoding="utf-8")


def test_rhythm_capture_failure_does_not_undo_write(tmp_path: Path) -> None:
    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(_VOLUME, encoding="utf-8")

    def broken_capture(rel, text):
        raise RuntimeError("取证故障")

    tool = build_rewrite_rhythm_tool(tmp_path, capture=broken_capture)
    out = _call(tool, volume=1, table="本卷章号范围：1-1\n新节奏表")
    assert "已回写" in out
    assert "新节奏表" in path.read_text(encoding="utf-8")


def test_rhythm_build_uses_real_snapshot_callback_contract(tmp_path: Path) -> None:
    from app.platform.agent.architecture import AgentBuildCtx
    from harness_current.tools.rewrite_rhythm_table import build as build_rewrite

    path = tmp_path / "卷纲/第01卷.md"
    path.parent.mkdir()
    path.write_text(_VOLUME, encoding="utf-8")
    snapshots = []
    context = SimpleNamespace(
        workspace_path=tmp_path,
        trace_recorder=None, trace_id="snapshot-contract",
        artifact_snapshot_callback=snapshots.append,
    )
    abc = AgentBuildCtx(context, None, SimpleNamespace(name="author"), "harness_current", _HARNESS_DIR)
    out = _call(build_rewrite(abc), volume=1, table="本卷章号范围：1-1\n提交的推进表")
    assert "已回写" in out
    final = path.read_text(encoding="utf-8")
    assert snapshots[0]["file_path"] == "卷纲/第01卷.md"
    assert snapshots[0]["content"] == final
    assert "提交的推进表" in final
    assert snapshots[0]["tool"] == "rewrite_rhythm_table"
