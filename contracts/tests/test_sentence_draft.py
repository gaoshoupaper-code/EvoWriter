"""逐段写作的共享规则。"""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from contracts.creative_harness import write_denial
from contracts.sentence_draft import (
    DRAFT, DRAFT_TOOLS, HEADER_TOOL, SENTENCE_TOOLS, DraftState, body_char_count, compose_chapter,
    draft_state, note_flushed, package_uses_sentences, pending_sentences, sentences_after, turn_sentences,
    unresolved_draft_calls, validate_sentence,
)


def _human(call_id="h1"):
    return {"type": "human", "content": "写第2章", "id": call_id}


def _call(name, call_id, text="段落。"):
    return {"type": "ai", "id": f"a-{call_id}", "tool_calls": [{"name": name, "args": {"text": text}, "id": call_id}]}


def _result(call_id, content="第 1 段｜累计约 20 字。", status="success"):
    return {"type": "tool", "tool_call_id": call_id, "content": content, "status": status}


@pytest.mark.parametrize("text", ["", "   ", "\t"])
def test_paragraph_with_no_text_is_rejected(text):
    assert validate_sentence(text)


@pytest.mark.parametrize("text", ["管事把茶杯往账本上一搁。", "「这一页，重算。」", "  三日后，账房先生盘点。  ",
                                  "他站起来。\n走到窗边。", "他站起来。\r\n走到窗边。"])
def test_complete_paragraph_passes_even_with_inner_line_breaks(text):
    assert validate_sentence(text) is None


def test_turn_sentences_keep_call_order_and_skip_rejected_or_unfinished_calls():
    messages = [
        _call("write_info", "c1", "她推门进来。"), _result("c1"),
        _call("write_setup", "c2", "「谁？」"), _result("c2", content="[拒绝] 段落是空的，请写完整的成段正文。"),
        _call("read_artifact", "r1"), _result("r1", content="章纲…"),
        _call("write_causal", "c3", " 热气漫上纸页。 "), _result("c3"),
        _call("write_transition", "c4", "没返回的段落。"),
    ]
    assert turn_sentences(messages) == [("c1", "她推门进来。"), ("c3", "热气漫上纸页。")]


def test_turn_starts_after_the_last_user_message():
    messages = [_call("write_info", "old"), _result("old"), _human(), _call("write_info", "new"), _result("new")]
    assert turn_sentences(messages) == [("new", "段落。")]


def test_turn_sentences_accept_message_objects():
    messages = [
        SimpleNamespace(type="human", id="h1"),
        SimpleNamespace(type="ai", tool_calls=[{"name": "write_transition", "args": {"text": "他喉头一紧。"}, "id": "c9"}]),
        SimpleNamespace(type="tool", tool_call_id="c9", content=[{"type": "text", "text": "第 1 段。"}], status=None),
    ]
    assert turn_sentences(messages) == [("c9", "他喉头一紧。")]


def _written(*ids):
    messages = []
    for index, call_id in enumerate(ids, 1):
        messages += [_call("write_causal", call_id, f"第{index}段。"), _result(call_id)]
    return messages


def test_pending_sentences_follow_the_flushed_marker():
    messages = _written("c1", "c2", "c3")
    assert [text for _, text in pending_sentences(messages)] == ["第1段。", "第2段。", "第3段。"]
    assert [text for _, text in pending_sentences(messages, "c2")] == ["第3段。"]
    assert [text for _, text in pending_sentences(messages, "c3")] == []


def test_marker_from_another_turn_leaves_every_paragraph_pending():
    messages = _written("c1", "c2")
    assert len(pending_sentences(messages, "previous-turn-call")) == 2


def test_unresolved_calls_notice_sibling_draft_calls_only():
    own = {"name": HEADER_TOOL, "args": {"status": "初稿"}, "id": "head"}
    sibling = {"name": "write_causal", "args": {"text": "段落。"}, "id": "c1"}
    reading = {"name": "read_artifact", "args": {}, "id": "r1"}
    last = {"type": "ai", "tool_calls": [sibling, own, reading]}
    assert unresolved_draft_calls([_human(), last], own_call="head") is True
    assert unresolved_draft_calls([_human(), last, _result("c1")], own_call="head") is False
    assert unresolved_draft_calls([_human(), {"type": "ai", "tool_calls": [own, reading]}], own_call="head") is False
    assert unresolved_draft_calls([_human()], own_call="head") is False


def test_compose_writes_a_new_chapter_with_two_header_lines_and_one_call_per_paragraph():
    text = compose_chapter("", 2, ["她推门进来。", "「谁？」"], title="夜探", status="初稿")
    assert text == (
        f"# 第002章 · 夜探\n> 状态：初稿｜{date.today().isoformat()}\n\n她推门进来。\n\n「谁？」\n"
    )


def test_compose_keeps_inner_line_breaks_of_a_causal_paragraph():
    text = compose_chapter("", 2, ["他伸手按住账本。\n「这一页，重算。」"], status="初稿")
    assert text == (
        f"# 第002章\n> 状态：初稿｜{date.today().isoformat()}\n\n他伸手按住账本。\n「这一页，重算。」\n"
    )


def test_compose_keeps_the_existing_body_and_the_original_date_and_fills_the_title():
    existing = "# 第002章\n> 状态：起草中｜2026-11-20\n> 进度：已落 3 批\n\n旧批次的一段。\n\n旧批次的另一段。\n"
    text = compose_chapter(existing, 2, ["新写的一段。"], title="夜探", status="初稿")
    assert text == (
        "# 第002章 · 夜探\n> 状态：初稿｜2026-11-20\n> 进度：已落 3 批\n\n"
        "旧批次的一段。\n\n旧批次的另一段。\n\n新写的一段。\n"
    )


def test_compose_keeps_an_existing_title_and_the_line_break_style():
    existing = "# 第002章 · 旧名\r\n> 状态：初稿｜2026-11-20\r\n\r\n已有正文。\r\n"
    text = compose_chapter(existing, 2, ["续写的一段。"], status="起草中")
    assert text == "# 第002章 · 旧名\r\n> 状态：起草中｜2026-11-20\r\n\r\n已有正文。\r\n\r\n续写的一段。\r\n"


def test_body_char_count_ignores_header_lines_and_blank_lines():
    text = "# 第002章 · 夜探\n> 状态：初稿｜2026-11-25\n\n她推门进来。\n\n「谁？」\n"
    assert body_char_count(text) == len("她推门进来。") + len("「谁？」")


def test_package_uses_sentences_reads_the_manifest(tmp_path):
    assert package_uses_sentences(tmp_path) is False
    (tmp_path / "architecture.json").write_text(json.dumps({"agents": [{"name": "text", "tools": ["save_artifact"]}]}))
    assert package_uses_sentences(tmp_path) is False
    (tmp_path / "architecture.json").write_text(json.dumps({"agents": [{"name": "text", "tools": ["write_info"]}]}))
    assert package_uses_sentences(tmp_path) is True


def _manifest(tmp_path, tools):
    (tmp_path / "architecture.json").write_text(json.dumps({"agents": [{"name": "text", "role": "main", "tools": tools}]}))
    return tmp_path


def test_sentence_package_refuses_batch_drafting_but_keeps_rewrite_and_revision(tmp_path):
    root = _manifest(tmp_path, ["save_artifact", "write_info"])
    draft = {"kind": "chapter_text", "chapter": 2, "mode": "append", "content": "一批正文", "unit_kind": "ordinary"}
    first = {"kind": "chapter_text", "chapter": 2, "mode": "full", "content": "# 第002章 · 夜\n…", "unit_kind": "ordinary"}
    rewrite = {"kind": "chapter_text", "chapter": 2, "mode": "full", "content": "# 第002章 · 夜\n…"}
    revision = {"kind": "chapter_text", "chapter": 2, "mode": "edit", "anchor": "旧句", "replacement": "新句"}
    assert write_denial("text", "save_artifact", draft, 1, 2, package_root=root)
    assert write_denial("text", "save_artifact", first, 1, 2, package_root=root)
    assert write_denial("text", "save_artifact", rewrite, 1, 2, package_root=root) is None
    assert write_denial("text", "save_artifact", revision, 1, 2, package_root=root) is None
    assert write_denial("text", "save_artifact", {**rewrite, "chapter": 3}, 1, 2, package_root=root) == "只能写当前目标章"


def test_old_package_keeps_batch_drafting_unchanged(tmp_path):
    root = _manifest(tmp_path, ["save_artifact"])
    draft = {"kind": "chapter_text", "chapter": 2, "mode": "append", "content": "一批正文", "unit_kind": "ordinary"}
    assert write_denial("text", "save_artifact", draft, 1, 2, package_root=root) is None
    assert write_denial("text", "save_artifact", draft, 1, 2, package_root=None) is None
    assert write_denial("text", "write_info", {"text": "一段。"}, 1, 2, package_root=root)


@pytest.mark.parametrize("tool", sorted(DRAFT_TOOLS))
def test_draft_tools_belong_to_the_text_agent_of_a_sentence_package(tmp_path, tool):
    root = _manifest(tmp_path, ["save_artifact", *SENTENCE_TOOLS, HEADER_TOOL])
    args = {"text": "一段。"} if tool in SENTENCE_TOOLS else {"status": "初稿"}
    assert write_denial("text", tool, args, 1, 2, package_root=root) is None
    assert write_denial("volume", tool, args, 1, 1, package_root=root) == "仅正文可以逐段写作"
    assert write_denial("text", tool, args, 1, 2, package_root=None)


def test_draft_state_defaults_to_an_idle_marker_and_notes_flushes():
    token = DRAFT.set(None)
    try:
        assert draft_state().flushed_through is None
    finally:
        DRAFT.reset(token)
    saved = []
    token = DRAFT.set(DraftState(persist=saved.append))
    try:
        note_flushed("c7")
        assert (draft_state().flushed_through, saved) == ("c7", ["c7"])
    finally:
        DRAFT.reset(token)


def test_sentences_after_follows_the_flushed_marker():
    written = [("c1", "第一段。"), ("c2", "第二段。"), ("c3", "第三段。")]
    assert sentences_after(written) == written
    assert sentences_after(written, "c2") == [("c3", "第三段。")]
    assert sentences_after(written, "c3") == []
    assert sentences_after(written, "earlier-attempt") == written
