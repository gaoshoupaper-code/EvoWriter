"""正文起草保存守卫：限制普通单元，放行已启用 Skill 的完整场景。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH",
    str(Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"),
))
load_package(_HARNESS_DIR, mod_name="harness_scene_guard")

from harness_scene_guard.tools.read_artifact import build_read_artifact_tool  # noqa: E402
from harness_scene_guard.tools.save_artifact import build_save_artifact_tool  # noqa: E402


HEADER = "# 第001章 · 临时约定\n> 状态：起草中｜2026-11-04\n\n"
REL = "正文/第001章.md"
SKILL = "谈判场景"
CONTEXT = (
    "## 作者启用的写作技能（作者本人设定）\n\n"
    f"### {SKILL}——写足报价、拒绝、交换条件和附带代价的约定\n\n"
    "本场景占3批篇幅。未启用技能这个名字只出现在说明中，不代表已启用。\n"
)
SCENE = "报价遭拒后，沈砚提出修闸换取降价，最终以铜印抵押承担违约代价。" * 40


def _tool(tmp_path, *, context=CONTEXT, previous=None, read=True, enabled_skill_names=None):
    registry = {}
    if previous is not None:
        path = tmp_path / REL
        path.parent.mkdir(parents=True)
        path.write_text(previous, encoding="utf-8")
        if read:
            build_read_artifact_tool(tmp_path, registry).invoke({"kind": "chapter_text", "chapter": 1})
    kwargs = {} if context is None else {
        "writing_context": context,
        "enabled_skill_names": {SKILL} if enabled_skill_names is None else enabled_skill_names,
    }
    return build_save_artifact_tool(tmp_path, registry, {}, **kwargs)


def _save(tool, *, mode="full", content="沈" * 350, **extra):
    return tool.invoke({"kind": "chapter_text", "chapter": 1, "mode": mode,
                        "content": HEADER + content if mode == "full" else content, **extra})


@pytest.mark.parametrize("mode", ["full", "append"])
def test_oversized_ordinary_draft_is_rejected_without_writing(tmp_path, mode):
    previous = HEADER + "已保存前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous)
    path = tmp_path / REL
    before = path.read_bytes() if path.exists() else None

    result = _save(tool, mode=mode, content="沈" * 451, unit_kind="ordinary")

    assert "拒绝" in result
    assert "450" in result
    assert path.read_bytes() == before if before is not None else not path.exists()


@pytest.mark.parametrize("mode", ["full", "append"])
@pytest.mark.parametrize("length", [350, 450])
def test_ordinary_draft_at_or_below_limit_is_saved(tmp_path, mode, length):
    # Existing chapter length must not become the next unit's length limit.
    previous = HEADER + "前" * 1200 + "\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous)
    content = "沈" * length

    result = _save(tool, mode=mode, content=content, unit_kind="ordinary")

    assert "已落盘" in result or "已追加落盘" in result
    expected = HEADER + content if mode == "full" else previous.rstrip("\n") + "\n\n" + content + "\n"
    assert (tmp_path / REL).read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("mode", ["full", "append"])
def test_enabled_skill_scene_is_saved_as_one_long_piece(tmp_path, mode):
    previous = HEADER + "已保存前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous)
    assert len(SCENE) > 1000

    result = _save(tool, mode=mode, content=SCENE, unit_kind="skill_scene", skill_name=SKILL)

    assert "已落盘" in result or "已追加落盘" in result
    expected = HEADER + SCENE if mode == "full" else previous.rstrip("\n") + "\n\n" + SCENE + "\n"
    assert (tmp_path / REL).read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("mode", ["full", "append"])
@pytest.mark.parametrize("name", ["未启用技能", "谈判"])
def test_scene_name_must_match_an_enabled_skill_wrapper(tmp_path, mode, name):
    previous = HEADER + "已保存前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous)
    path = tmp_path / REL
    before = path.read_bytes() if path.exists() else None

    result = _save(tool, mode=mode, content=SCENE, unit_kind="skill_scene", skill_name=name)

    assert "拒绝" in result
    assert path.read_bytes() == before if before is not None else not path.exists()


@pytest.mark.parametrize("name", ["谈判——扩展", "谈判"])
def test_em_dash_skill_name_requires_the_entire_trusted_name(tmp_path, name):
    original_name = "谈判——扩展"
    context = f"### {original_name}——完整的谈判场景\n本场景占3批。"
    tool = _tool(tmp_path, context=context, enabled_skill_names={original_name})

    result = _save(tool, content=SCENE, unit_kind="skill_scene", skill_name=name)

    if name == original_name:
        assert "已落盘" in result
        assert (tmp_path / REL).read_text(encoding="utf-8") == HEADER + SCENE
    else:
        assert "拒绝" in result
        assert not (tmp_path / REL).exists()


@pytest.mark.parametrize("mode", ["full", "append"])
def test_empty_trusted_skill_set_cannot_claim_a_skill_from_prompt_text(tmp_path, mode):
    previous = HEADER + "已保存前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous, enabled_skill_names=set())
    path = tmp_path / REL
    before = path.read_bytes() if path.exists() else None

    result = _save(tool, mode=mode, content=SCENE, unit_kind="skill_scene", skill_name=SKILL)

    assert "拒绝" in result
    assert path.read_bytes() == before if before is not None else not path.exists()


@pytest.mark.parametrize("name", [None, "", "   "])
def test_skill_scene_requires_a_nonempty_original_skill_name(tmp_path, name):
    result = _save(_tool(tmp_path), content=SCENE, unit_kind="skill_scene", skill_name=name)

    assert "拒绝" in result
    assert not (tmp_path / REL).exists()


@pytest.mark.parametrize("context", [CONTEXT, ""])
@pytest.mark.parametrize("mode", ["full", "append"])
def test_production_draft_requires_explicit_unit_kind(tmp_path, context, mode):
    previous = HEADER + "已保存前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, context=context, previous=previous)
    path = tmp_path / REL
    before = path.read_bytes() if path.exists() else None

    result = _save(tool, mode=mode)

    assert "拒绝" in result
    assert "unit_kind" in result
    assert path.read_bytes() == before if before is not None else not path.exists()


def test_non_chapter_artifact_does_not_require_unit_kind_or_obey_draft_limit(tmp_path):
    text = "# 文风\n" + "冷静叙述。" * 200
    result = _tool(tmp_path).invoke({"kind": "style", "mode": "full", "content": text})

    assert "已落盘" in result
    assert (tmp_path / "文风.md").read_text(encoding="utf-8") == text


def test_chapter_edit_can_make_a_long_revision_without_unit_kind(tmp_path):
    previous = HEADER + "待修订句。\n"
    result = _tool(tmp_path, previous=previous).invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "edit",
        "anchor": "待修订句。", "replacement": SCENE,
    })

    assert "已落盘" in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == previous.replace("待修订句。", SCENE)


def test_existing_full_revision_bypasses_draft_guard_after_read(tmp_path):
    tool = _tool(tmp_path, previous=HEADER + "旧稿。\n")

    result = _save(tool, content=SCENE)

    assert "已落盘" in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == HEADER + SCENE


def test_existing_full_revision_still_requires_prior_read(tmp_path):
    previous = HEADER + "不能被未读覆盖的旧稿。\n"
    tool = _tool(tmp_path, previous=previous, read=False)
    before = (tmp_path / REL).read_bytes()

    result = _save(tool, content=SCENE)

    assert "先调 read_artifact" in result
    assert (tmp_path / REL).read_bytes() == before


@pytest.mark.parametrize("mode", ["full", "append"])
def test_unannotated_legacy_calls_without_context_remain_compatible(tmp_path, mode):
    previous = HEADER + "已保存前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, context=None, previous=previous)

    result = _save(tool, mode=mode, content=SCENE)

    assert "已落盘" in result or "已追加落盘" in result
    assert SCENE in (tmp_path / REL).read_text(encoding="utf-8")
