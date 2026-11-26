"""逐段写作下的正文收工检查：段落没写进文件就不许交审、不许收工。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.platform.agent.loader import load_package
from contracts.sentence_draft import DRAFT, DraftState, note_flushed

_HARNESS_DIR = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH",
    str(Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"),
))
load_package(_HARNESS_DIR)

from harness_current.middleware.text_completion import TextCompletionMiddleware  # noqa: E402
from harness_current.text_completion import (  # noqa: E402
    delivery_block_reason, parallel_writing_unit, review_block_reason,
)


def _written(*calls, reply=True):
    """一条用户指令，加上逐个已返回的段落调用；reply 为真时末尾再加一条停笔的模型消息。"""
    messages = [HumanMessage(content="写第1章", id="turn-1")]
    for call_id, name, text in calls:
        messages.append(AIMessage(content="", tool_calls=[{"name": name, "args": {"text": text}, "id": call_id}]))
        messages.append(ToolMessage(content=f"第 1 段，本章累计约 {len(text)} 字。", tool_call_id=call_id))
    if reply:
        messages.append(AIMessage(content="第1章写完了。"))
    return messages


def test_delivery_is_refused_while_paragraphs_are_not_in_the_file(tmp_path):
    state = {"messages": _written(("c1", "write_info", "她推门进来。"), ("c2", "write_causal", "「谁？」"))}
    reason = delivery_block_reason(state, tmp_path)
    assert reason == "本章还有 2 段没写入文件，先用 write_chapter_header 把状态改为初稿。"
    note_flushed("c2")
    assert delivery_block_reason(state, tmp_path) is None


def test_review_is_refused_while_paragraphs_are_not_in_the_file(tmp_path):
    state = {"messages": _written(("c1", "write_causal", "热气漫上纸页。"))}
    reason = review_block_reason(state, tmp_path, "审第1章")
    assert reason == "本章还有 1 段没写入文件，先用 write_chapter_header 把状态改为初稿。整章写入后再审稿。"
    note_flushed("c1")
    assert review_block_reason(state, tmp_path, "审第1章") is None


def test_paragraphs_from_an_earlier_turn_do_not_block_a_new_chat(tmp_path):
    earlier = _written(("c1", "write_info", "她推门进来。"), reply=False)
    state = {"messages": [*earlier, HumanMessage(content="聊聊后面的情节", id="turn-2"), AIMessage(content="好的。")]}
    assert delivery_block_reason(state, tmp_path) is None
    assert review_block_reason(state, tmp_path, "审第1章") is None


def test_a_stopped_model_is_told_to_finalize_before_the_turn_can_end(tmp_path):
    state = {"messages": _written(("c1", "write_transition", "三日后，账房先生盘点。"))}
    update = TextCompletionMiddleware(tmp_path)._guard(state)
    assert update["jump_to"] == "model"
    assert "本章还有 1 段没写入文件" in update["messages"][-1].content
    assert "write_chapter_header" in update["messages"][-1].content


def test_a_finished_turn_is_left_alone(tmp_path):
    note_flushed("c1")
    state = {"messages": _written(("c1", "write_transition", "三日后，账房先生盘点。"))}
    assert TextCompletionMiddleware(tmp_path)._guard(state) is None


def test_parallel_writing_unit_sees_every_draft_tool(tmp_path):
    def last(calls):
        return {"messages": [HumanMessage(content="写第1章", id="turn-1"), AIMessage(content="", tool_calls=calls)]}

    paragraph = {"name": "write_causal", "args": {"text": "她推门进来。"}, "id": "c1"}
    header = {"name": "write_chapter_header", "args": {"status": "初稿"}, "id": "head"}
    reading = {"name": "read_artifact", "args": {"kind": "chapter_outline", "chapter": 1}, "id": "r1"}
    assert parallel_writing_unit(last([paragraph]), tmp_path) is True
    assert parallel_writing_unit(last([header]), tmp_path) is True
    assert parallel_writing_unit(last([reading]), tmp_path) is False


@pytest.fixture(autouse=True)
def _fresh_draft_marker():
    token = DRAFT.set(DraftState())
    try:
        yield
    finally:
        DRAFT.reset(token)
