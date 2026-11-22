"""Frozen chapter targets must not acknowledge competing edits to the same old draft."""
import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.domains.writing.target_execution import FrozenExecution, TargetScopeMiddleware
from app.platform.agent.loader import load_package
from contracts.target_tasks import TargetTaskRequest

PACKAGE = Path(os.environ.get(
    "WRITER_SCENE_HARNESS_PATH", Path(__file__).resolve().parents[2] / "evolution/harnesses/repo",
))
load_package(PACKAGE, mod_name="harness_concurrent_target")
from harness_concurrent_target.tools import save_artifact  # noqa: E402


def _execution(root):
    request = TargetTaskRequest(task_id="test-save", attempt_id="trial", source_commit="a" * 40,
                                target={"task_type": "chapter_text", "chapter": 1},
                                input={"confirmation_rules": "确认"})
    return FrozenExecution(request, object(), PACKAGE, object(), root)


@pytest.mark.parametrize("sync", [False, True], ids=["async-agent", "sync-agent"])
def test_concurrent_builders_keep_each_accepted_edit_and_its_revision(tmp_path, monkeypatch, sync):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    execution = _execution(tmp_path)
    rel = "正文/第001章.md"
    path = tmp_path / rel
    path.parent.mkdir()
    markers = ["甲", "乙", "丙"]
    original = "# 第001章 · 渡口\n> 状态：初稿\n\n" + "".join(mark + "原文。" for mark in markers)
    path.write_text(original, encoding="utf-8")
    recorder = Mock()
    barrier = threading.Barrier(len(markers))
    original_write = save_artifact._atomic_write

    def racing_write(path, content):
        # Concurrent lost-update implementations are all paused after reading the same draft.
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass
        original_write(path, content)

    monkeypatch.setattr(save_artifact, "_atomic_write", racing_write)

    async def submit(marker):
        middleware = TargetScopeMiddleware(execution)
        tool = save_artifact.build_save_artifact_tool(tmp_path, {rel: True}, {}, agent_name="text-subagent",
                                                      writing_context="", recorder=recorder, trace_id="batch")
        args = {"kind": "chapter_text", "chapter": 1, "mode": "edit",
                "anchor": marker + "原文", "replacement": marker + "修订"}
        node = ToolNode([tool], wrap_tool_call=middleware.wrap_tool_call,
                        awrap_tool_call=middleware.awrap_tool_call)
        builder = StateGraph(MessagesState)
        builder.add_node("tools", node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        state = {"messages": [AIMessage(content="", tool_calls=[
            {"id": marker, "name": "save_artifact", "args": args},
        ])]}
        if sync:
            result = await asyncio.to_thread(graph.invoke, state)
        else:
            result = await graph.ainvoke(state)
        return result["messages"][-1]

    async def run():
        return await asyncio.gather(*(submit(marker) for marker in markers))

    for marker, receipt in zip(markers, asyncio.run(run()), strict=True):
        assert isinstance(receipt, ToolMessage) and receipt.tool_call_id == marker
        assert receipt.status == "success" and "已落盘" in receipt.content
    expected = original.replace("原文", "修订")
    assert path.read_text(encoding="utf-8") == expected
    previous = original
    snapshots = recorder.record_artifact_revision.call_args_list
    assert len(snapshots) == len(markers)
    for snapshot in snapshots:
        current = snapshot.kwargs["content"]
        assert any(current == previous.replace(mark + "原文", mark + "修订")
                   for mark in markers if mark + "原文" in previous)
        previous = current
    assert previous == expected


def test_pending_target_save_does_not_block_other_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    guard = TargetScopeMiddleware(_execution(tmp_path))
    save = SimpleNamespace(tool_call={"id": "save", "name": "save_artifact",
                                      "args": {"kind": "chapter_text", "chapter": 1}})
    read = SimpleNamespace(tool_call={"id": "read", "name": "read_artifact",
                                      "args": {"kind": "chapter_outline", "chapter": 1}})

    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        async def hold(_):
            started.set()
            await finish.wait()

        saving = asyncio.create_task(guard.awrap_tool_call(save, hold))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert await asyncio.wait_for(guard.awrap_tool_call(read, lambda _: asyncio.sleep(0, "read")), timeout=2) == "read"
        finally:
            finish.set()
            await saving

    asyncio.run(run())
