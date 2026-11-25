"""逐句写作工具：五个写作工具、文件头工具与整章写入（REQ-20261125-154544）。

锁定 FR-001/FR-002/FR-003/FR-005/FR-006/FR-007 与 AC-001、AC-002、AC-005、AC-008，
以及 AC-003 的拼装部分（已有半章之后接上新句子、已写入的句子不再写第二遍）。
"""
from __future__ import annotations

import importlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.domains.writing import target_execution
from app.platform.agent.loader import load_package
from contracts.creative_harness import HEADER_TOOL, SENTENCE_TOOLS, build_agent_package
from contracts.sentence_draft import DRAFT, DraftState, draft_state, pending_sentences

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
load_package(_HARNESS_DIR)

from harness_current.text_completion import completion_snapshot  # noqa: E402
from harness_current.tools import save_artifact as save_module  # noqa: E402
from harness_current.tools.write_chapter_header import build as build_header  # noqa: E402
from harness_current.tools.write_sensory import build as build_sensory  # noqa: E402

SENTENCES = [("c1", "write_action", "她推门进来。"), ("c2", "write_dialogue", "「谁？」"),
             ("c3", "write_sensory", "热气漫上纸页。")]
TODAY = date.today().isoformat()


@pytest.fixture
def chapter(tmp_path):
    """按评测任务的方式装配：当前目标章来自冻结的执行目标，直接写工作区，没有父进程。"""
    execution = SimpleNamespace(request=SimpleNamespace(target=SimpleNamespace(chapter=2)), chapter=2, volume=None)
    execution_token = target_execution._execution.set(execution)
    draft_token = DRAFT.set(DraftState())
    try:
        yield SimpleNamespace(root=tmp_path, path=tmp_path / "正文" / "第002章.md")
    finally:
        DRAFT.reset(draft_token)
        target_execution._execution.reset(execution_token)


def _abc(root, package=None, writing_context=None):
    styles = {"author": writing_context} if writing_context else {}
    return SimpleNamespace(ctx=SimpleNamespace(trace_recorder=None, trace_id=None, styles=styles),
                           workspace_path=root, pkg_dir=package or _HARNESS_DIR, trace_name="text-subagent")


def _runtime(messages, call_id):
    return SimpleNamespace(state={"messages": messages}, tool_call_id=call_id)


def _turn(calls=SENTENCES, answered=True):
    """本轮对话：一条用户指令，加上逐个已返回的句子调用。"""
    messages = [HumanMessage(content="写第2章", id="h1")]
    for call_id, name, text in calls:
        messages.append(AIMessage(content="", id=f"a-{call_id}",
                                  tool_calls=[{"name": name, "args": {"text": text}, "id": call_id}]))
        if answered:
            messages.append(ToolMessage(content=f"第 1 句，本章累计约 {len(text)} 字。", tool_call_id=call_id))
    return messages


def _header(chapter, messages, status="初稿", title="夜探"):
    return build_header(_abc(chapter.root)).func(status=status, title=title, runtime=_runtime(messages, "head"))


# ── 写作工具：只收一句，只回短回执（FR-001、FR-002、FR-003）──────────────


def test_sentence_tool_takes_one_sentence_and_writes_nothing_to_disk(chapter):
    receipt = build_sensory(_abc(chapter.root)).func(
        "从端茶的动作接到纸页上的热气", "热气漫上纸页。", runtime=_runtime(_turn([]), "c1"),
    )
    assert receipt == "第 1 句，本章累计约 7 字。"
    assert not chapter.path.exists()


@pytest.mark.parametrize("text", ["   ", "他站起来。\n走到窗边。"])
def test_sentence_tool_rejects_an_empty_sentence_or_a_line_break(chapter, text):
    receipt = build_sensory(_abc(chapter.root)).func(
        "从端茶的动作接到纸页上的热气", text, runtime=_runtime(_turn(), "c4"),
    )
    assert receipt.startswith("[拒绝] ")
    assert chapter.path.exists() is False


def test_receipt_counts_this_turn_and_the_existing_body(chapter):
    chapter.path.parent.mkdir(parents=True)
    chapter.path.write_text("# 第002章\n> 状态：起草中｜2026-11-20\n\n旧批次的一段。\n", encoding="utf-8")
    written = [("c1", "write_action", "她推门进来。"), ("c2", "write_dialogue", "「谁？」")]
    receipt = build_sensory(_abc(chapter.root)).func(
        "从端茶的动作接到纸页上的热气", "热气漫上纸页。", runtime=_runtime(_turn(written), "c3"),
    )
    assert receipt == "第 3 句，本章累计约 24 字。"


@pytest.mark.parametrize("intent", ["   ", "接上。\n往下写。", "字" * 61])
def test_sentence_tool_rejects_an_invalid_writing_intent(chapter, intent):
    receipt = build_sensory(_abc(chapter.root)).func(
        intent, "热气漫上纸页。", runtime=_runtime(_turn([]), "c1"),
    )
    assert receipt.startswith("[拒绝] 请先用一行短语写下本句的具体写法")


def test_sentence_tool_waits_for_its_own_model_reply(chapter):
    tool = build_sensory(_abc(chapter.root))
    args = {"writing_intent": "从端茶的动作接到纸页上的热气", "text": "热气漫上纸页。"}
    write_call = {"name": "write_sensory", "args": args, "id": "c2"}
    read_call = {"name": "read_artifact", "args": {}, "id": "r1"}
    messages = [*_turn([]), AIMessage(content="", tool_calls=[read_call, write_call])]
    refused = tool.func(**args, runtime=_runtime(messages, "c2"))
    assert refused.startswith("[拒绝] 写句子时一次只调用一个工具")
    messages.extend([
        ToolMessage(content="已读章纲。", tool_call_id="r1"),
        ToolMessage(content=refused, tool_call_id="c2"),
        AIMessage(content="", tool_calls=[{**write_call, "id": "c3"}]),
    ])
    receipt = tool.func(**args, runtime=_runtime(messages, "c3"))
    assert receipt == "第 1 句，本章累计约 7 字。"
    messages.append(ToolMessage(content=receipt, tool_call_id="c3"))
    _header(chapter, messages)
    assert chapter.path.read_text(encoding="utf-8").endswith("\n\n热气漫上纸页。\n")
    assert args["writing_intent"] not in chapter.path.read_text(encoding="utf-8")


# ── 文件头工具：转「初稿」时整章一次写入（FR-005、FR-006）────────────────


def test_header_tool_writes_the_whole_chapter_when_the_status_becomes_draft(chapter):
    content, _meta = _header(chapter, _turn())
    assert content == f"文件头已改为「初稿」，写入本章 3 句。"
    assert chapter.path.read_text(encoding="utf-8") == (
        f"# 第002章 · 夜探\n> 状态：初稿｜{TODAY}\n\n她推门进来。\n\n「谁？」\n\n热气漫上纸页。\n"
    )
    assert pending_sentences(_turn(), draft_state().flushed_through) == []
    assert draft_state().flushed_through == "c3"


def test_header_tool_joins_new_sentences_after_an_existing_draft_and_keeps_its_date(chapter):
    chapter.path.parent.mkdir(parents=True)
    chapter.path.write_text("# 第002章\n> 状态：起草中｜2026-11-20\n\n旧批次的一段。\n", encoding="utf-8")
    _header(chapter, _turn([("c9", "write_narration", "新写的一句。")]))
    assert chapter.path.read_text(encoding="utf-8") == (
        "# 第002章 · 夜探\n> 状态：初稿｜2026-11-20\n\n旧批次的一段。\n\n新写的一句。\n"
    )


def test_header_flush_tells_the_run_where_the_file_stops_only_when_sentences_were_written(chapter):
    saved = []
    token = DRAFT.set(DraftState(persist=saved.append))
    try:
        _header(chapter, _turn([("c1", "write_action", "她推门进来。")]))
        _header(chapter, _turn([("c1", "write_action", "她推门进来。")]))
    finally:
        DRAFT.reset(token)
    assert saved == ["c1"]


def test_header_flush_leaves_an_artifact_revision_for_the_whole_chapter(chapter):
    revisions = []
    abc = _abc(chapter.root, writing_context="起草规程")
    abc.ctx.trace_recorder = SimpleNamespace(record_artifact_revision=lambda *args, **kwargs: revisions.append(kwargs))
    abc.ctx.trace_id = "trace-x"
    build_header(abc).func(status="初稿", title="夜探", runtime=_runtime(_turn(), "head"))
    assert [item["content"] for item in revisions] == [chapter.path.read_text(encoding="utf-8")]
    assert (revisions[0]["tool_name"], revisions[0]["file_path"]) == (HEADER_TOOL, "正文/第002章.md")


def test_sentences_after_a_flush_are_not_written_twice(chapter):
    _header(chapter, _turn())
    before = chapter.path.read_text(encoding="utf-8")
    content, _meta = _header(chapter, _turn())
    assert content == "文件头已改为「初稿」。"
    assert chapter.path.read_text(encoding="utf-8") == before


def test_header_tool_refuses_to_finalize_a_chapter_without_any_text(chapter):
    content, meta = _header(chapter, _turn([]))
    assert (content, meta) == ("[拒绝] 本章还没有正文，先写正文再改文件头。", None)
    assert chapter.path.exists() is False


def test_header_tool_still_rewrites_the_header_of_an_existing_draft(chapter):
    chapter.path.parent.mkdir(parents=True)
    chapter.path.write_text("# 第002章\n> 状态：起草中｜2026-11-20\n\n旧批次的一段。\n", encoding="utf-8")
    _header(chapter, _turn([]), status="起草中", title="旧名新改")
    assert chapter.path.read_text(encoding="utf-8").startswith("# 第002章 · 旧名新改\n> 状态：起草中｜2026-11-20\n\n")


def test_header_tool_waits_for_sibling_calls_of_the_same_reply(chapter):
    calls = [{"name": "write_action", "args": {"text": "她推门进来。"}, "id": "c1"},
             {"name": HEADER_TOOL, "args": {"status": "初稿"}, "id": "head"}]
    pending = [HumanMessage(content="写第2章", id="h1"), AIMessage(content="", tool_calls=calls, id="a-1")]
    content, meta = _header(chapter, pending)
    assert content.startswith("[拒绝] 同一轮里还有句子没返回")
    assert (meta, chapter.path.exists()) == (None, False)
    answered = [*pending, ToolMessage(content="第 1 句，本章累计约 6 字。", tool_call_id="c1")]
    _header(chapter, answered)
    assert chapter.path.read_text(encoding="utf-8").endswith("\n\n她推门进来。\n")


def test_rejected_sentences_are_not_part_of_the_chapter(chapter):
    messages = _turn([("c1", "write_action", "她推门进来。"), ("c2", "write_dialogue", "「谁？」")])
    messages[-1] = ToolMessage(content="[拒绝] 句子是空的，请写一个完整的句子。", tool_call_id="c2")
    _header(chapter, messages)
    assert chapter.path.read_text(encoding="utf-8") == f"# 第002章 · 夜探\n> 状态：初稿｜{TODAY}\n\n她推门进来。\n"


@pytest.mark.parametrize("title", ["夜\n探", "名" * 41])
def test_header_tool_refuses_a_title_that_is_not_one_short_line(chapter, title):
    content, _meta = _header(chapter, _turn(), title=title)
    assert content.startswith("[拒绝] 章名须是一行简短文字。")
    assert chapter.path.exists() is False


def test_historical_successful_sentence_calls_are_kept_in_call_order(chapter):
    calls = [{"name": "write_action", "args": {"text": "她推门进来。"}, "id": "c1"},
             {"name": "write_dialogue", "args": {"text": "「谁？」"}, "id": "c2"}]
    messages = [HumanMessage(content="写第2章", id="h1"), AIMessage(content="", tool_calls=calls, id="a-1"),
                ToolMessage(content="第 1 句，本章累计约 6 字。", tool_call_id="c1"),
                ToolMessage(content="第 2 句，本章累计约 10 字。", tool_call_id="c2")]
    _header(chapter, messages)
    assert chapter.path.read_text(encoding="utf-8") == f"# 第002章 · 夜探\n> 状态：初稿｜{TODAY}\n\n她推门进来。\n\n「谁？」\n"


def test_header_flush_is_a_real_save_for_the_delivery_gate(chapter):
    abc = _abc(chapter.root, writing_context="起草规程")
    content, meta = build_header(abc).func(status="初稿", title="夜探", runtime=_runtime(_turn(), "head"))
    assert meta["writer_text"]["event"] == "save"
    assert meta["writer_text"]["is_writing_unit"] is True
    assert (meta["writer_text"]["chapter"], meta["writer_text"]["status"]) == (2, "初稿")
    messages = [
        HumanMessage(content="写第2章", id="h1"),
        AIMessage(content="", id="a-head", tool_calls=[
            {"name": HEADER_TOOL, "args": {"status": "初稿", "title": "夜探"}, "id": "head"}]),
        ToolMessage(content=content, name=HEADER_TOOL, tool_call_id="head", response_metadata=meta),
    ]
    snapshot = completion_snapshot({"messages": messages}, chapter.root)
    assert snapshot["chapters"]["2"] == {"unit": "head", "status": "初稿", "review": None, "review_turn": None, "order": 1}


# ── 落盘工具随包切换；正文包带齐六个工具（FR-007、FR-008）────────────────


def _package(root: Path, tools: list[str]) -> Path:
    root.mkdir(parents=True)
    (root / "architecture.json").write_text(
        json.dumps({"schema_version": "writer.architecture/1", "agents": [{"name": "text", "role": "main", "tools": tools}]}))
    return root


def test_save_tool_description_follows_the_package(chapter, tmp_path):
    fresh = _package(tmp_path / "fresh", ["save_artifact", *SENTENCE_TOOLS, HEADER_TOOL])
    legacy = _package(tmp_path / "legacy", ["save_artifact"])
    new_description = save_module.build(_abc(chapter.root, package=fresh)).description
    old_description = save_module.build(_abc(chapter.root, package=legacy)).description
    assert "append" not in new_description and "unit_kind" not in new_description and "批" not in new_description
    assert HEADER_TOOL in new_description and 'mode="edit"' in new_description and 'mode="full"' in new_description
    assert "append" in old_description and "unit_kind" in old_description


def test_save_tool_is_built_once_per_assembly(chapter):
    abc = _abc(chapter.root)
    assert save_module.build(abc) is save_module.build(abc)


def test_text_package_ships_the_sentence_tools_and_their_shared_module(tmp_path):
    build_agent_package(_HARNESS_DIR, tmp_path / "text", "text")
    tools = json.loads((tmp_path / "text" / "architecture.json").read_text(encoding="utf-8"))["agents"][0]["tools"]
    assert tools[-6:] == [*SENTENCE_TOOLS, HEADER_TOOL]
    assert all((tmp_path / "text" / "tools" / f"{name}.py").is_file() for name in tools)
    assert (tmp_path / "text" / "tools" / "sentence_common.py").is_file()

    build_agent_package(_HARNESS_DIR, tmp_path / "volume", "volume")
    volume_tools = json.loads((tmp_path / "volume" / "architecture.json").read_text(encoding="utf-8"))["agents"][0]["tools"]
    assert not set(SENTENCE_TOOLS) & set(volume_tools)
    assert not (tmp_path / "volume" / "tools" / "sentence_common.py").exists()


def test_every_tool_of_the_text_package_builds(tmp_path):
    build_agent_package(_HARNESS_DIR, tmp_path / "text", "text")
    load_package(tmp_path / "text", "harness_sentence_package")
    tools = json.loads((tmp_path / "text" / "architecture.json").read_text(encoding="utf-8"))["agents"][0]["tools"]
    abc = _abc(tmp_path / "workspace", package=tmp_path / "text")
    abc.agent = SimpleNamespace(name="text")
    for name in tools:
        tool = importlib.import_module(f"harness_sentence_package.tools.{name}").build(abc)
        assert tool.name == name
