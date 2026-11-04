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
import harness_scene_guard.tools.save_artifact as save_artifact_module  # noqa: E402


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
    expected = _progress_text(1, content) if mode == "full" else previous.rstrip("\n") + "\n\n" + content + "\n"
    assert (tmp_path / REL).read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("mode", ["full", "append"])
def test_enabled_skill_scene_is_saved_as_one_long_piece(tmp_path, mode):
    previous = HEADER + "已保存前文。\n" if mode == "append" else None
    tool = _tool(tmp_path, previous=previous)
    assert len(SCENE) > 1000

    result = _save(tool, mode=mode, content=SCENE, unit_kind="skill_scene", skill_name=SKILL)

    assert "已落盘" in result or "已追加落盘" in result
    expected = _progress_text(1, SCENE) if mode == "full" else previous.rstrip("\n") + "\n\n" + SCENE + "\n"
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
        assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(1, SCENE)
    else:
        assert "拒绝" in result
        assert not (tmp_path / REL).exists()


def test_wrong_heading_name_can_be_corrected_without_splitting_the_scene(tmp_path):
    tool = _tool(tmp_path)
    assert f'"{SKILL}"' in tool.description

    rejected = _save(tool, content=SCENE, unit_kind="skill_scene", skill_name=SKILL + "——场景说明")

    assert "拒绝" in rejected
    assert f'"{SKILL}"' in rejected
    assert "不得改为 ordinary" in rejected
    assert not (tmp_path / REL).exists()

    saved = _save(tool, content=SCENE, unit_kind="skill_scene", skill_name=SKILL)

    assert "已落盘" in saved
    assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(1, SCENE)


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


def _progress_text(count, body="已保存前文。\n"):
    return HEADER.rstrip("\n") + f"\n> 进度：已落 {count} 批\n\n" + body


@pytest.mark.parametrize("unit_kind", ["ordinary", "skill_scene"])
def test_first_full_owns_the_initial_progress_count(tmp_path, unit_kind):
    tool = _tool(tmp_path)
    body = "沈" * 350 if unit_kind == "ordinary" else SCENE

    result = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "full",
        "content": _progress_text(99, body), "unit_kind": unit_kind,
        "skill_name": SKILL if unit_kind == "skill_scene" else None,
    })

    assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(1, body)
    assert "本章已保存 1 个正文单元" in result
    assert "不代表 Skill 篇幅预算" in result


def test_append_uses_persisted_progress_across_builder_restart(tmp_path):
    tool = _tool(tmp_path, previous=_progress_text(3))
    first = _save(tool, mode="append", content=SCENE, unit_kind="skill_scene", skill_name=SKILL)
    registry = {}
    build_read_artifact_tool(tmp_path, registry).invoke({"kind": "chapter_text", "chapter": 1})
    restarted = build_save_artifact_tool(
        tmp_path, registry, {}, writing_context=CONTEXT, enabled_skill_names={SKILL},
    )

    second = _save(restarted, mode="append", content="下一个普通单元。", unit_kind="ordinary")

    expected = _progress_text(5).rstrip("\n") + "\n\n" + SCENE + "\n\n下一个普通单元。\n"
    assert (tmp_path / REL).read_text(encoding="utf-8") == expected
    assert "本章已保存 4 个正文单元" in first
    assert "本章已保存 5 个正文单元" in second


@pytest.mark.parametrize("mode", ["edit", "full"])
@pytest.mark.parametrize("include_progress", [True, False])
def test_revision_preserves_progress_while_updating_status_and_body(tmp_path, mode, include_progress):
    previous = _progress_text(6, "待修订正文。\n")
    revised = _progress_text(99, SCENE).replace("状态：起草中", "状态：初稿")
    if not include_progress:
        revised = revised.replace("> 进度：已落 99 批\n", "")
    tool = _tool(tmp_path, previous=previous)
    arguments = {"content": revised} if mode == "full" else {"anchor": previous, "replacement": revised}

    result = tool.invoke({"kind": "chapter_text", "chapter": 1, "mode": mode, **arguments})

    expected = _progress_text(6, SCENE).replace("状态：起草中", "状态：初稿")
    assert (tmp_path / REL).read_text(encoding="utf-8") == expected
    assert "本章已保存 6 个正文单元" in result


def test_progress_protection_keeps_full_fallback_after_failed_edits(tmp_path):
    tool = _tool(tmp_path, previous=_progress_text(4))
    for _ in range(3):
        result = tool.invoke({
            "kind": "chapter_text", "chapter": 1, "mode": "edit",
            "anchor": "不存在的锚点", "replacement": "新句子。",
        })
        assert "失败" in result

    result = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "full",
        "content": _progress_text(20, SCENE),
    })

    assert "已落盘" in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(4, SCENE)
    result = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "edit",
        "anchor": "状态：起草中", "replacement": "状态：初稿",
    })
    assert "已落盘" in result
    assert "> 进度：已落 4 批" in (tmp_path / REL).read_text(encoding="utf-8")


def test_rejected_and_failed_appends_do_not_advance_progress(tmp_path, monkeypatch):
    previous = _progress_text(2)
    tool = _tool(tmp_path, previous=previous)
    path = tmp_path / REL
    before = path.read_bytes()

    rejected = _save(tool, mode="append", content="沈" * 451, unit_kind="ordinary")
    assert "拒绝" in rejected
    assert path.read_bytes() == before

    pending_writes = []
    def fail_write(path, text):
        pending_writes.append(text)
        raise OSError("acceptance write failure")

    with monkeypatch.context() as patch:
        patch.setattr(save_artifact_module, "_atomic_write", fail_write)
        failed = _save(tool, mode="append", content="本次追加。", unit_kind="ordinary")
    assert "写入失败" in failed
    assert path.read_bytes() == before
    assert pending_writes == [_progress_text(3).rstrip("\n") + "\n\n本次追加。\n"]

    saved = _save(tool, mode="append", content="本次追加。", unit_kind="ordinary")
    assert "本章已保存 3 个正文单元" in saved
    assert "本轮追加第 1 次" in saved


@pytest.mark.parametrize("mode", ["full", "append", "edit"])
def test_historical_chapter_without_standard_progress_does_not_get_a_guessed_count(tmp_path, mode):
    previous = HEADER + "历史正文。\n"
    tool = _tool(tmp_path, previous=previous)
    arguments = {
        "full": {"content": HEADER + "修订正文。\n"},
        "append": {"content": "本次追加。", "unit_kind": "ordinary"},
        "edit": {"anchor": "历史正文。", "replacement": "修订正文。"},
    }[mode]

    result = tool.invoke({"kind": "chapter_text", "chapter": 1, "mode": mode, **arguments})

    saved = (tmp_path / REL).read_text(encoding="utf-8")
    assert "> 进度：" not in saved
    assert "本章已保存" not in result
    if mode == "append":
        assert "本轮追加第 1 次" in result
        assert saved == previous.rstrip("\n") + "\n\n本次追加。\n"


def test_legacy_context_does_not_rewrite_progress(tmp_path):
    tool = _tool(tmp_path, context=None)
    first = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "full",
        "content": _progress_text(99),
    })
    appended = _save(tool, mode="append", content=SCENE)
    revised = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "edit",
        "anchor": "> 进度：已落 99 批", "replacement": "> 进度：已落 12 批",
    })

    assert "> 进度：已落 12 批" in (tmp_path / REL).read_text(encoding="utf-8")
    assert all("本章已保存" not in result for result in (first, appended, revised))


@pytest.mark.parametrize("progress", [None, "三"])
def test_new_chapter_establishes_progress_when_missing_or_nonnumeric(tmp_path, progress):
    body = "本次新建正文。"
    content = HEADER + body if progress is None else _progress_text(progress, body)

    result = _tool(tmp_path).invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "full",
        "content": content, "unit_kind": "ordinary",
    })

    assert "本章已保存 1 个正文单元" in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(1, body)


@pytest.mark.parametrize("mode", ["edit", "full"])
def test_historical_revision_cannot_create_a_false_progress_baseline(tmp_path, mode):
    previous = HEADER + "历史正文。\n"
    tool = _tool(tmp_path, previous=previous)
    revised = _progress_text(80, "修订正文。\n")
    arguments = {"content": revised} if mode == "full" else {"anchor": previous, "replacement": revised}

    result = tool.invoke({"kind": "chapter_text", "chapter": 1, "mode": mode, **arguments})

    assert "拒绝" in result
    assert "历史次数未知" in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == previous
    appended = _save(tool, mode="append", content="本次追加。", unit_kind="ordinary")
    assert "本章已保存" not in appended
    assert (tmp_path / REL).read_text(encoding="utf-8") == previous.rstrip("\n") + "\n\n本次追加。\n"


def test_body_progress_quotes_do_not_supply_a_historical_count(tmp_path):
    body = "纸上写着旧记录：\n\n> 进度：已落 7 批\n\n```text\n> 进度：已落 8 批\n```\n"
    previous = HEADER + body
    tool = _tool(tmp_path, previous=previous)

    result = _save(tool, mode="append", content="沈砚收起记录。", unit_kind="ordinary")

    assert "本章已保存" not in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == previous.rstrip("\n") + "\n\n沈砚收起记录。\n"


@pytest.mark.parametrize("mode", ["edit", "full"])
def test_removed_header_progress_is_restored_without_changing_body_quotes(tmp_path, mode):
    body = "纸上写着旧记录：\n\n> 进度：已落 7 批\n\n```text\n> 进度：已落 8 批\n```\n"
    previous = _progress_text(6, "待修订正文。\n")
    revised = (HEADER + body).replace("状态：起草中", "状态：初稿")
    tool = _tool(tmp_path, previous=previous)
    arguments = {"content": revised} if mode == "full" else {"anchor": previous, "replacement": revised}

    result = tool.invoke({"kind": "chapter_text", "chapter": 1, "mode": mode, **arguments})

    assert "本章已保存 6 个正文单元" in result
    expected = _progress_text(6, body).replace("状态：起草中", "状态：初稿")
    assert (tmp_path / REL).read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("content", [
    "> 状态：起草中｜2026-11-04\n\n本次正文。",
    "# 第001章 · 临时约定\n\n本次正文。",
    "# 第001章 · 临时约定\n\n本次正文。\n> 状态：起草中｜2026-11-04",
    "本次正文。\n# 第001章 · 临时约定\n> 状态：起草中｜2026-11-04",
])
def test_new_modern_chapter_requires_title_and_status_in_the_header(tmp_path, content):
    tool = _tool(tmp_path)

    result = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "full",
        "content": content, "unit_kind": "ordinary",
    })

    assert "拒绝" in result
    assert "标题行" in result and "状态行" in result
    assert not (tmp_path / REL).exists()
    saved = _save(tool, content="补齐文件头后的正文。", unit_kind="ordinary")
    assert "本章已保存 1 个正文单元" in saved


def test_existing_full_revision_restores_metadata_and_remains_countable_after_restart(tmp_path):
    previous = _progress_text(4)
    tool = _tool(tmp_path, previous=previous)
    body = "修订后的正文。\n\n> 进度：已落 7 批\n"

    result = tool.invoke({"kind": "chapter_text", "chapter": 1, "mode": "full", "content": body})

    assert "本章已保存 4 个正文单元" in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(4, body)
    registry = {}
    build_read_artifact_tool(tmp_path, registry).invoke({"kind": "chapter_text", "chapter": 1})
    restarted = build_save_artifact_tool(
        tmp_path, registry, {}, writing_context=CONTEXT, enabled_skill_names={SKILL},
    )
    appended = _save(restarted, mode="append", content="修订后继续。", unit_kind="ordinary")
    assert "本章已保存 5 个正文单元" in appended
    expected = _progress_text(5, body).rstrip("\n") + "\n\n修订后继续。\n"
    assert (tmp_path / REL).read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("mode", ["edit", "full"])
def test_legacy_historical_revision_keeps_its_existing_progress_behavior(tmp_path, mode):
    previous = HEADER + "历史正文。\n"
    tool = _tool(tmp_path, context=None, previous=previous)
    revised = _progress_text(80, "修订正文。\n")
    arguments = {"content": revised} if mode == "full" else {"anchor": previous, "replacement": revised}

    result = tool.invoke({"kind": "chapter_text", "chapter": 1, "mode": mode, **arguments})

    assert "已落盘" in result
    assert "本章已保存" not in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == revised


def test_modern_append_cannot_create_a_chapter_with_a_false_progress_baseline(tmp_path):
    tool = _tool(tmp_path)
    content = _progress_text(80, "正文。")

    rejected = _save(tool, mode="append", content=content, unit_kind="ordinary")

    assert "拒绝" in rejected and "full" in rejected
    assert not (tmp_path / REL).exists()
    first = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "full",
        "content": content, "unit_kind": "ordinary",
    })
    appended = _save(tool, mode="append", content="续写。", unit_kind="ordinary")
    assert "本章已保存 1 个正文单元" in first
    assert "本章已保存 2 个正文单元" in appended
    assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(2, "正文。\n\n续写。\n")


@pytest.mark.parametrize("previous", [None, ""])
def test_legacy_append_can_still_create_an_artifact(tmp_path, previous):
    tool = _tool(tmp_path, context=None, previous=previous)
    content = _progress_text(80, "旧调用正文。")

    result = _save(tool, mode="append", content=content)

    assert "已追加落盘" in result
    assert "本章已保存" not in result
    assert (tmp_path / REL).read_text(encoding="utf-8") == content + "\n"


@pytest.mark.parametrize("previous", ["", " \n"])
def test_empty_modern_chapter_requires_read_then_full_initialization(tmp_path, previous):
    tool = _tool(tmp_path, previous=previous)
    content = _progress_text(80, "首次正文。")
    before = (tmp_path / REL).read_bytes()

    rejected = _save(tool, mode="append", content=content, unit_kind="ordinary")

    assert "拒绝" in rejected and "full" in rejected
    assert (tmp_path / REL).read_bytes() == before
    first = tool.invoke({
        "kind": "chapter_text", "chapter": 1, "mode": "full",
        "content": content, "unit_kind": "ordinary",
    })
    appended = _save(tool, mode="append", content="续写。", unit_kind="ordinary")
    assert "本章已保存 1 个正文单元" in first
    assert "本章已保存 2 个正文单元" in appended
    assert (tmp_path / REL).read_text(encoding="utf-8") == _progress_text(2, "首次正文。\n\n续写。\n")


def test_empty_modern_chapter_full_still_requires_a_prior_read(tmp_path):
    tool = _tool(tmp_path, previous="", read=False)

    result = _save(tool, unit_kind="ordinary")

    assert "先调 read_artifact" in result
    assert (tmp_path / REL).read_bytes() == b""
