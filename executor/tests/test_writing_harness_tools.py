"""五段 harness 工具与阶段闸测试（REQ-20261018-115508 FR-003/004/005/006）。

锁定：
  - save_artifact 校验编辑四道保险（AC-006）：先读后改 / 精确+归一匹配 /
    锚点唯一性 / 失败不落盘；重试上限退化 full（DEC-007）。
  - read_artifact：缺产物指引、小节切片、读取登记。
  - stage_gate（AC-008）：越段写拦截、回写工具阶段限制。
  - append_chapter_deviation 前置（AC-007）：仅人修定稿后可用。
  - handoff：override 落状态（DEC-002 点名跳段）。
  - 工具 schema 无路径参数（AC-005）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
load_package(_HARNESS_DIR)

from harness_current.middleware.stage_gate import StageGateMiddleware  # noqa: E402
from harness_current.tools.append_chapter_deviation import (  # noqa: E402
    build_append_deviation_tool,
)
from harness_current.tools.handoff import build_handoff_tool  # noqa: E402
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


def test_stage_gate_passes_read_confirm_handoff() -> None:
    gate = StageGateMiddleware("theme")
    assert gate._maybe_block(_req("read_artifact", kind="outline")) is None
    assert gate._maybe_block(_req("confirm_with_user")) is None
    assert gate._maybe_block(_req("handoff", target_stage="text")) is None


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
    assert gate._maybe_block(_req("update_foreshadow_table")) is not None
    assert gate._maybe_block(_req("read_artifact", kind="theme")) is None  # 读不拦


def test_stage_gate_build_honors_override(tmp_path: Path) -> None:
    """C3：点名跳段（override）同步切换闸门。"""
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
    state.override_stage = STAGE_CHAPTER
    save_writing_state(tmp_path, state)
    abc = SimpleNamespace(workspace_path=tmp_path)
    gate = sg_mod.build(abc)
    # 扫描段是 outline（总纲未落盘），但 override=chapter：章纲可写、总纲被拦
    assert gate._maybe_block(_req("save_artifact", kind="chapter_outline")) is None
    assert gate._maybe_block(_req("save_artifact", kind="outline")) is not None


# ── append_chapter_deviation 前置（AC-007）────────────────────────


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
    out = _call(tool, volume=1, table="| 章区间 | 爽点 | 级别 |\n|---|---|---|\n| 1-3 | 捡宝 | 小 |")
    assert "已回写" in out
    updated = (vol / "第01卷.md").read_text(encoding="utf-8")
    assert "1-3" in updated and "旧表" not in updated and "保留" in updated


# ── handoff（DEC-002；S3 修复：作者确认 interrupt）────────────────


def _patch_interrupt(monkeypatch, answer: str) -> None:
    import langgraph.types as lgt

    monkeypatch.setattr(lgt, "interrupt", lambda payload: answer, raising=True)


def test_handoff_confirmed_writes_override(tmp_path: Path, monkeypatch) -> None:
    from contracts.writing_artifacts import load_writing_state

    _patch_interrupt(monkeypatch, "确认，进卷纲")
    tool = build_handoff_tool(tmp_path)
    out = _call(tool, target_stage="volume")
    assert "已确认跳段" in out
    assert load_writing_state(tmp_path).override_stage == "volume"


def test_handoff_declined_keeps_current(tmp_path: Path, monkeypatch) -> None:
    from contracts.writing_artifacts import load_writing_state

    _patch_interrupt(monkeypatch, "取消")
    tool = build_handoff_tool(tmp_path)
    out = _call(tool, target_stage="volume")
    assert "未确认" in out
    assert load_writing_state(tmp_path).override_stage is None


def test_handoff_ambiguous_answer_defaults_to_stay(tmp_path: Path, monkeypatch) -> None:
    """round2：模糊自由文本按未确认处理（白名单判定，安全默认不切段）。"""
    from contracts.writing_artifacts import load_writing_state

    for answer in ("再想想", "先别切", "", "嗯嗯"):
        _patch_interrupt(monkeypatch, answer)
        tool = build_handoff_tool(tmp_path)
        out = _call(tool, target_stage="volume")
        assert "未确认" in out, answer
        assert load_writing_state(tmp_path).override_stage is None


def test_handoff_invalid_stage_rejected(tmp_path: Path, monkeypatch) -> None:
    from contracts.writing_artifacts import load_writing_state

    _patch_interrupt(monkeypatch, "确认")
    tool = build_handoff_tool(tmp_path)
    out = _call(tool, target_stage="不存在的段")
    assert "失败" in out
    assert load_writing_state(tmp_path).override_stage is None


# ── 工具 schema 无路径参数（AC-005）───────────────────────────────

_ALLOWED_PROPS = {
    "kind", "volume", "chapter", "title", "mode", "anchor", "replacement",
    "content", "section", "entries", "table", "note", "target_stage",
    "question", "options", "multi_select",
}


def test_tool_schemas_have_no_path_params(tmp_path: Path) -> None:
    tools = [
        *_mk_save(tmp_path)[:1],
        build_read_artifact_tool(tmp_path, {}),
        build_update_foreshadow_tool(tmp_path),
        build_rewrite_rhythm_tool(tmp_path),
        build_append_deviation_tool(tmp_path),
        build_handoff_tool(tmp_path),
    ]
    for tool in tools:
        schema = tool.args_schema.model_json_schema()
        props = set(schema.get("properties", {}).keys())
        assert props <= _ALLOWED_PROPS, f"{tool.name} 出现白名单外参数: {props - _ALLOWED_PROPS}"
        for banned in ("path", "file_path", "rel", "filename", "dir", "directory"):
            assert banned not in props, f"{tool.name} 含路径参数 {banned}"
