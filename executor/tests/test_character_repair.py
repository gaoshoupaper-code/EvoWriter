"""人物卡原地重建与缺卡保护（REQ-20261102-135406 AC-006/007/008）。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.platform.agent.loader import load_package
from app.schemas.screenplay import ScreenplayGenerateRequest
from contracts.runtime_context import RuntimeContext
from contracts.writing_artifacts import load_writing_state, save_writing_state, scan_writing_progress


HARNESS = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"


def test_character_mode_survives_missing_card_and_rebuilt_state(tmp_path: Path):
    from contracts.writing_artifacts import (
        character_generation_block_reason, is_character_mode, mark_character_mode,
    )

    assert not is_character_mode(tmp_path)
    assert character_generation_block_reason(tmp_path) is None
    card = tmp_path / "人物" / "主角.md"
    card.parent.mkdir()
    card.write_text("# 主角", encoding="utf-8")
    assert is_character_mode(tmp_path)
    assert character_generation_block_reason(tmp_path) is None
    mark_character_mode(tmp_path)
    card.unlink()
    assert is_character_mode(tmp_path)
    assert scan_writing_progress(tmp_path).character_mode
    assert "主角卡" in character_generation_block_reason(tmp_path)
    # 路由状态可重建，不能承担人物机制启用意图的唯一存储。
    save_writing_state(tmp_path, load_writing_state(tmp_path))
    (tmp_path / ".writing" / "state.json").unlink()
    assert is_character_mode(tmp_path)
    card.write_text("# 补齐的主角", encoding="utf-8")
    assert character_generation_block_reason(tmp_path) is None


def test_character_repair_request_and_context_are_explicit():
    request = ScreenplayGenerateRequest(thread_id="t")
    assert request.character_repair is False
    assert ScreenplayGenerateRequest(thread_id="t", character_repair=True).character_repair
    assert RuntimeContext.__dataclass_fields__["character_repair"].default is False


@pytest.mark.parametrize("stage", ["theme", "outline", "volume", "chapter", "text"])
def test_character_repair_only_allows_character_writes(tmp_path: Path, stage: str):
    pkg = load_package(HARNESS)
    from harness_current.middleware.stage_gate import build

    state = load_writing_state(tmp_path)
    state.confirmed_stage = stage
    save_writing_state(tmp_path, state)
    before = (tmp_path / ".writing" / "state.json").read_bytes()
    gate = build(SimpleNamespace(
        workspace_path=tmp_path, ctx=SimpleNamespace(character_repair=True),
    ))

    def call(tool_name, **args):
        return SimpleNamespace(tool_call={"name": tool_name, "args": args, "id": "c"})

    assert gate._maybe_block(call("save_artifact", kind="character", name="主角", mode="full")) is None
    for kind in ("theme", "outline", "volume_outline", "chapter_outline", "chapter_text", "style"):
        assert gate._maybe_block(call("save_artifact", kind=kind, mode="full")) is not None
    for tool in ("update_foreshadow_table", "rewrite_rhythm_table", "append_chapter_deviation", "request_review", "task"):
        assert gate._maybe_block(call(tool)) is not None
    for tool in ("read_artifact", "list_characters", "confirm_with_user"):
        assert gate._maybe_block(call(tool)) is None
    assert (tmp_path / ".writing" / "state.json").read_bytes() == before
    assert pkg.character_repair_prompt()


def test_character_repair_keeps_legacy_readonly(tmp_path: Path):
    load_package(HARNESS)
    from harness_current.middleware.stage_gate import build

    (tmp_path / "outline").mkdir()
    gate = build(SimpleNamespace(
        workspace_path=tmp_path, ctx=SimpleNamespace(character_repair=True),
    ))
    request = SimpleNamespace(tool_call={
        "name": "save_artifact", "args": {"kind": "character", "mode": "full"}, "id": "c",
    })
    assert gate._maybe_block(request) is not None


@pytest.mark.parametrize("stage", ["theme", "outline", "volume", "chapter", "text"])
def test_character_repair_writes_card_without_changing_existing_artifacts(tmp_path: Path, stage: str):
    load_package(HARNESS)
    from harness_current.middleware.stage_gate import build
    from harness_current.tools.save_artifact import build_save_artifact_tool

    state = load_writing_state(tmp_path)
    state.confirmed_stage = stage
    save_writing_state(tmp_path, state)
    for rel in ("主题.md", "总纲.md", "文风.md", "卷纲/第01卷.md", "章纲/第001章.md", "正文/第001章.md"):
        path = tmp_path / rel
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"# 保留 {rel}", encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    gate = build(SimpleNamespace(
        workspace_path=tmp_path, ctx=SimpleNamespace(character_repair=True),
    ))
    tool = build_save_artifact_tool(tmp_path, {}, {})

    def invoke(request):
        return tool.invoke(request.tool_call["args"])

    args = {"kind": "character", "name": "主角", "mode": "full", "content": "# 主角\n> 状态：定稿\n新的人物设定"}
    request = SimpleNamespace(tool_call={"name": "save_artifact", "args": args, "id": "repair"})
    result = gate.wrap_tool_call(request, invoke)
    assert "失败" not in result and "拒绝" not in result
    assert (tmp_path / "人物" / "主角.md").read_text(encoding="utf-8") == args["content"]
    assert all((tmp_path / rel).read_bytes() == content for rel, content in before.items())


def test_character_mode_marker_failure_is_atomic(tmp_path: Path, monkeypatch):
    from contracts.writing_artifacts import CHARACTER_MODE_RELPATH, mark_character_mode

    original_replace = Path.replace

    def fail_marker(self, target):
        if Path(target) == tmp_path / CHARACTER_MODE_RELPATH:
            raise OSError("atomic replace failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_marker)
    with pytest.raises(OSError, match="atomic replace failed"):
        mark_character_mode(tmp_path)
    assert not (tmp_path / CHARACTER_MODE_RELPATH).exists()
    assert not list((tmp_path / ".writing").glob("*.tmp"))
