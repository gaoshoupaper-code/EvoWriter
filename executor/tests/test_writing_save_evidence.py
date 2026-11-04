"""正文成功保存的原生 ToolMessage 凭证，不改变旧文字回执。"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH",
    str(Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"),
))
load_package(_HARNESS_DIR, mod_name="harness_save_evidence")

from harness_save_evidence.tools.read_artifact import build_read_artifact_tool  # noqa: E402
from harness_save_evidence.tools.save_artifact import build_save_artifact_tool  # noqa: E402
import harness_save_evidence.tools.save_artifact as save_module  # noqa: E402

HEADER = "# 第001章 · 夜航\n> 状态：起草中｜2026-11-04\n> 进度：已落 2 批\n\n"
REL = "正文/第001章.md"


def _tool(root, *, previous=None, context="正文规程"):
    registry = {}
    if previous is not None:
        path = root / REL
        path.parent.mkdir(parents=True)
        path.write_text(previous, encoding="utf-8")
        build_read_artifact_tool(root, registry).invoke({"kind": "chapter_text", "chapter": 1})
    return build_save_artifact_tool(root, registry, {}, writing_context=context, enabled_skill_names={"谈判"})


def _call(tool, **args):
    return tool.invoke({"type": "tool_call", "name": "save_artifact", "id": "save-1",
                        "args": {"kind": "chapter_text", "chapter": 1, **args}})


@pytest.mark.parametrize("mode", ["full", "append"])
@pytest.mark.parametrize("context", ["正文规程", ""])
def test_successful_writing_unit_carries_native_save_evidence(tmp_path, mode, context):
    previous = HEADER + "前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous, context=context)

    result = _call(tool, mode=mode, content=HEADER + "本次正文。" if mode == "full" else "本次正文。",
                   unit_kind="ordinary")

    assert isinstance(result, ToolMessage)
    assert result.artifact == {"writer_text": {
        "workspace": hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest(),
        "chapter": 1, "event": "save", "is_writing_unit": True, "status": "起草中",
    }}
    assert "本章已保存" in result.content


@pytest.mark.parametrize("mode", ["full", "edit"])
def test_existing_revision_updates_actual_status_without_new_writing_unit(tmp_path, mode):
    previous = HEADER + "前文。\n"
    tool = _tool(tmp_path, previous=previous)
    args = ({"content": previous.replace("起草中", "初稿")} if mode == "full" else
            {"anchor": "状态：起草中", "replacement": "状态：初稿"})

    result = _call(tool, mode=mode, **args)

    assert result.artifact["writer_text"]["status"] == "初稿"
    assert result.artifact["writer_text"]["is_writing_unit"] is False
    assert "> 进度：已落 2 批" in (tmp_path / REL).read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["full", "append"])
def test_business_rejection_emits_no_evidence_and_changes_no_file(tmp_path, mode):
    previous = HEADER + "前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous)
    path = tmp_path / REL
    before = path.read_bytes() if path.exists() else None

    result = _call(tool, mode=mode, content=HEADER + "沈" * 451, unit_kind="ordinary")

    assert "拒绝" in result.content
    assert result.artifact is None
    assert path.read_bytes() == before if before is not None else not path.exists()


def test_write_failure_emits_no_success_evidence(tmp_path, monkeypatch):
    previous = HEADER + "前文。\n"
    tool = _tool(tmp_path, previous=previous)
    before = (tmp_path / REL).read_bytes()

    def fail_write(path, text):
        raise OSError("write failure")

    monkeypatch.setattr(save_module, "_atomic_write", fail_write)
    result = _call(tool, mode="append", content="本次正文。", unit_kind="ordinary")

    assert "写入失败" in result.content
    assert result.artifact is None
    assert (tmp_path / REL).read_bytes() == before


def test_evidence_reads_final_file_instead_of_the_requested_status(tmp_path, monkeypatch):
    actual_write = save_module._atomic_write

    def change_written_status(path, text):
        actual_write(path, text.replace("状态：起草中", "状态：初稿"))

    monkeypatch.setattr(save_module, "_atomic_write", change_written_status)
    result = _call(_tool(tmp_path), mode="full", content=HEADER + "正文。", unit_kind="ordinary")

    assert result.artifact["writer_text"]["status"] == "初稿"
    assert "> 状态：初稿" in (tmp_path / REL).read_text(encoding="utf-8")


def test_plain_dict_invocation_still_returns_the_original_string(tmp_path):
    result = _tool(tmp_path).invoke({"kind": "chapter_text", "chapter": 1, "mode": "full",
                                     "content": HEADER + "正文。", "unit_kind": "ordinary"})

    assert isinstance(result, str)
    assert result.startswith("已落盘 chapter_text（第 1 章）")
    assert "本章已保存 1 个正文单元" in result


def test_legacy_success_and_non_chapter_success_have_no_native_marker(tmp_path):
    legacy = _call(_tool(tmp_path / "legacy", context=None), mode="full", content="旧调用正文。")
    style = _tool(tmp_path / "modern").invoke({"type": "tool_call", "name": "save_artifact", "id": "style-1",
                                               "args": {"kind": "style", "mode": "full", "content": "文风。"}})

    assert legacy.content.startswith("已落盘") and legacy.artifact is None
    assert style.content.startswith("已落盘") and style.artifact is None


def test_async_tool_call_preserves_native_evidence(tmp_path):
    async def run():
        return await _tool(tmp_path).ainvoke({
            "type": "tool_call", "name": "save_artifact", "id": "async-save",
            "args": {"kind": "chapter_text", "chapter": 1, "mode": "full",
                     "content": HEADER + "正文。", "unit_kind": "ordinary"},
        })

    result = asyncio.run(run())

    assert isinstance(result, ToolMessage)
    assert result.artifact["writer_text"]["event"] == "save"
    assert result.tool_call_id == "async-save"
