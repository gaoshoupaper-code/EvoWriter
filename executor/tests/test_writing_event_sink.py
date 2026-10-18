"""WritingEventSink 测试（M4，D3=③ 新行为补针对性测试）。

验证从 agent.py 抽离的 WritingEventSink：
- 事件转换：on_chat_model_end → model_output，on_tool_error → tool_error 等。
- task 调度元信息：on_chain_start(task) 记录 active_tasks。
- 领域副作用：storybuilding task 结束触发 timeline 派生（FR-004：v8 章节统计链路已退役）。

不测完整 SSE 流（那需要真实 agent），只测 sink 对单个事件的处理产出。
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from app.domains.writing.events import WritingEventSink


def _make_thread(workspace_path: str = "/tmp/ws") -> SimpleNamespace:
    """构造带 workspace_path 的伪 thread 对象。"""
    return SimpleNamespace(workspace_path=workspace_path)


class TestWritingEventSinkBasicEvents(unittest.TestCase):
    """WritingEventSink 基础事件转换。"""

    def setUp(self):
        self.sink = WritingEventSink(_make_thread())

    def test_on_chat_model_end_produces_model_output(self):
        """on_chat_model_end → model_output 帧（DEC-006 双发：row 帧并存）。"""
        msg = MagicMock()
        msg.content = "模型输出文本"
        msg.tool_calls = []
        event = {"event": "on_chat_model_end", "name": "", "data": {"output": msg}}
        frames = asyncio.run(self.sink.on_event(event))
        model_output = [f for f in frames if f.startswith("event: model_output")]
        self.assertEqual(len(model_output), 1)
        self.assertIn("模型输出文本", model_output[0])
        self.assertTrue(any(f.startswith("event: row") for f in frames))

    def test_on_chat_model_stream_produces_model_stream(self):
        """on_chat_model_stream → model_stream 帧（DEC-006 双发：row 帧并存）。"""
        chunk = MagicMock()
        chunk.content = "流式片段"
        # MagicMock 的 additional_kwargs 默认是 MagicMock，需显式设为空 dict
        chunk.additional_kwargs = {}
        event = {"event": "on_chat_model_stream", "name": "", "data": {"chunk": chunk}}
        frames = asyncio.run(self.sink.on_event(event))
        model_stream = [f for f in frames if f.startswith("event: model_stream")]
        self.assertEqual(len(model_stream), 1)
        self.assertIn("流式片段", model_stream[0])

    def test_on_chat_model_stream_with_reasoning_produces_reasoning_stream(self):
        """on_chat_model_stream 的 chunk 带 reasoning_content → 额外产出 reasoning_stream 帧（T20）。"""
        chunk = MagicMock()
        chunk.content = "正文片段"
        chunk.additional_kwargs = {"reasoning_content": "嗯，让我想想这段怎么写"}
        event = {"event": "on_chat_model_stream", "name": "", "data": {"chunk": chunk}}
        frames = asyncio.run(self.sink.on_event(event))
        # 旧帧相对顺序：model_stream 先于 reasoning_stream（row 帧附后，DEC-006）
        self.assertTrue(frames[0].startswith("event: model_stream"))
        self.assertIn("正文片段", frames[0])
        reasoning = [f for f in frames if f.startswith("event: reasoning_stream")]
        self.assertEqual(len(reasoning), 1)
        self.assertIn("嗯，让我想想这段怎么写", reasoning[0])

    def test_on_chat_model_stream_no_reasoning(self):
        """chunk 不带 reasoning_content → 只产出 model_stream，无 reasoning_stream（T20 降级）。"""
        chunk = MagicMock()
        chunk.content = "普通片段"
        chunk.additional_kwargs = {}
        event = {"event": "on_chat_model_stream", "name": "", "data": {"chunk": chunk}}
        frames = asyncio.run(self.sink.on_event(event))
        self.assertTrue(any(f.startswith("event: model_stream") for f in frames))
        self.assertFalse(any(f.startswith("event: reasoning_stream") for f in frames))

    def test_on_tool_error_produces_tool_error(self):
        """on_tool_error → tool_error 帧。"""
        event = {
            "event": "on_tool_error", "name": "write_file",
            "data": {"error": "磁盘满"},
        }
        frames = asyncio.run(self.sink.on_event(event))
        self.assertEqual(len(frames), 1)
        self.assertIn("tool_error", frames[0])
        self.assertIn("磁盘满", frames[0])

    def test_unknown_event_produces_nothing(self):
        """未处理的事件类型 → 空帧列表。"""
        event = {"event": "on_chain_start", "name": "not_tools", "data": {}}
        frames = asyncio.run(self.sink.on_event(event))
        self.assertEqual(frames, [])


class TestWritingEventSinkTaskSideEffects(unittest.TestCase):
    """WritingEventSink task 副作用（D8 B 类）。"""

    def test_storybuilding_task_end_triggers_graph(self):
        """storybuilding task 结束 → 触发 generate_storyline_graph。"""
        sink = WritingEventSink(_make_thread("/tmp/ws"))
        # 模拟 task 开始：注入 active_tasks 元信息
        sink._active_tasks["call-1"] = {"name": "storybuilding"}
        event = {
            "event": "on_tool_end", "name": "task",
            "data": {"output": "done", "input": {"id": "call-1"}},
        }
        with patch("app.domains.writing.events.generate_storyline_graph") as mock_graph:
            frames = asyncio.run(sink.on_event(event))
            mock_graph.assert_called_once_with(Path("/tmp/ws"))

    def test_non_task_tool_end_no_side_effect(self):
        """非 task 工具结束 → 无副作用，仅 tool_output 帧。"""
        sink = WritingEventSink(_make_thread())
        event = {
            "event": "on_tool_end", "name": "read_file",
            "data": {"output": "content"},
        }
        with patch("app.domains.writing.events.generate_storyline_graph") as mock_graph:
            frames = asyncio.run(sink.on_event(event))
            mock_graph.assert_not_called()
            self.assertEqual(len(frames), 1)


if __name__ == "__main__":
    unittest.main()


# ======================================================================
# 行事件装配（REQ-20261018-235653 FR-001/FR-002/FR-005，DEC-003/DEC-006）
# ======================================================================

import json as _json


def _row_payloads(frames: list[str]) -> list[dict]:
    """从帧列表提取全部 row 事件的 data 载荷。"""
    payloads = []
    for frame in frames:
        lines = frame.strip().split("\n")
        if not lines or lines[0] != "event: row":
            continue
        data_line = next((l for l in lines if l.startswith("data: ")), None)
        if data_line:
            payloads.append(_json.loads(data_line[6:]))
    return payloads


def _stream_chunk(content: str = "", reasoning: str = "", run_id: str = "run-1") -> dict:
    chunk = MagicMock()
    chunk.content = content
    chunk.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
    return {"event": "on_chat_model_stream", "name": "", "run_id": run_id, "data": {"chunk": chunk}}


def _model_end(text: str, run_id: str = "run-1") -> dict:
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = []
    return {"event": "on_chat_model_end", "name": "", "run_id": run_id, "data": {"output": msg}}


class TestWritingEventSinkRowAssembly(unittest.TestCase):
    """行事件装配：开行/增量/终态 + 响应归属 + 双发。"""

    def setUp(self):
        self.sink = WritingEventSink(_make_thread())

    def test_stream_opens_reasoning_and_text_rows_sharing_response_id(self):
        """主代理流式：思考与正文各自开行，共享 response_id，seq 单调递增（FR-001）。"""
        frames = asyncio.run(self.sink.on_event(_stream_chunk("你好", "先想想")))
        rows = _row_payloads(frames)
        opens = [r for r in rows if r["op"] == "open"]
        self.assertEqual(len(opens), 2)
        self.assertEqual({o["kind"] for o in opens}, {"reasoning", "text"})
        self.assertEqual(len({o["response_id"] for o in opens}), 1)
        seqs = [o["seq"] for o in opens]
        self.assertEqual(seqs, sorted(seqs))
        deltas = [r for r in rows if r["op"] == "delta"]
        self.assertEqual(len(deltas), 2)

    def test_model_end_replaces_streamed_text_with_full_snapshot(self):
        """end 携带全量 text：客户端整体替换，流式/全量对齐去重（AC-002 边界）。"""
        asyncio.run(self.sink.on_event(_stream_chunk("片段", run_id="run-1")))
        frames = asyncio.run(self.sink.on_event(_model_end("完整正文", run_id="run-1")))
        ends = [r for r in _row_payloads(frames) if r["op"] == "end"]
        text_ends = [e for e in ends if e.get("text")]
        self.assertEqual(len(text_ends), 1)
        self.assertEqual(text_ends[0]["text"], "完整正文")
        self.assertEqual(text_ends[0]["state"], "complete")

    def test_non_streamed_model_end_opens_row_with_full_text(self):
        """非流式调用：仅 on_chat_model_end 到达 → 开行即全量（FR-002 必丢路径修复）。"""
        frames = asyncio.run(self.sink.on_event(_model_end("全量到达的正文", run_id="run-9")))
        rows = _row_payloads(frames)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["op"], "open")
        self.assertEqual(rows[0]["kind"], "text")
        self.assertEqual(rows[1], {"op": "end", "id": rows[0]["id"], "state": "complete", "text": "全量到达的正文"})

    def test_tool_events_open_and_end_tool_row(self):
        """工具调用 → 工具行开行 + 终态（FR-001）。"""
        start = {"event": "on_chain_start", "name": "tools", "data": {"input": [{"name": "write_file", "id": "c1", "args": {}}]}}
        frames = asyncio.run(self.sink.on_event(start))
        opens = [r for r in _row_payloads(frames) if r["op"] == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["kind"], "tool")
        self.assertEqual(opens[0]["tool"], "write_file")

        end = {"event": "on_tool_end", "name": "write_file", "data": {"output": "ok", "input": {"id": "c1"}}}
        frames = asyncio.run(self.sink.on_event(end))
        ends = [r for r in _row_payloads(frames) if r["op"] == "end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["state"], "complete")

    def test_task_opens_subagent_row_and_model_events_suppressed(self):
        """task 工具 → 子代理聚合行；活跃期间的模型事件不产 row 帧（FR-005/AC-004）。"""
        start = {"event": "on_chain_start", "name": "tools", "data": {"input": [{"name": "task", "id": "t1", "args": {"subagent_type": "storybuilding"}}]}}
        frames = asyncio.run(self.sink.on_event(start))
        opens = [r for r in _row_payloads(frames) if r["op"] == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["kind"], "subagent")
        self.assertEqual(opens[0]["subagent_type"], "storybuilding")
        self.assertEqual(opens[0]["iteration"], 1)

        # 子代理内部模型输出（run_id 不同）：不得进主时间线
        frames = asyncio.run(self.sink.on_event(_stream_chunk("子代理正文", "子代理思考", run_id="sub-run")))
        self.assertEqual(_row_payloads(frames), [])

        end = {"event": "on_tool_end", "name": "task", "data": {"output": "子代理产出摘要", "input": {"id": "t1"}}}
        frames = asyncio.run(self.sink.on_event(end))
        ends = [r for r in _row_payloads(frames) if r["op"] == "end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["state"], "complete")
        self.assertEqual(ends[0]["summary"], "子代理产出摘要")

    def test_tool_error_ends_tool_row_failed(self):
        start = {"event": "on_chain_start", "name": "tools", "data": {"input": [{"name": "read_file", "id": "c2", "args": {}}]}}
        asyncio.run(self.sink.on_event(start))
        err = {"event": "on_tool_error", "name": "read_file", "data": {"error": "boom", "input": {"id": "c2"}}}
        frames = asyncio.run(self.sink.on_event(err))
        ends = [r for r in _row_payloads(frames) if r["op"] == "end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["state"], "failed")

    def test_legacy_frames_still_emitted_alongside_row_frames(self):
        """DEC-006 双发过渡：旧帧与 row 帧并存。"""
        frames = asyncio.run(self.sink.on_event(_stream_chunk("正文", "思考")))
        joined = "".join(frames)
        self.assertIn("event: model_stream", joined)
        self.assertIn("event: reasoning_stream", joined)
        self.assertIn("event: row", joined)
