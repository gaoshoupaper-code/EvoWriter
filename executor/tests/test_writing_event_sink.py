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
        """on_chat_model_end → model_output 帧。"""
        msg = MagicMock()
        msg.content = "模型输出文本"
        msg.tool_calls = []
        event = {"event": "on_chat_model_end", "name": "", "data": {"output": msg}}
        frames = asyncio.run(self.sink.on_event(event))
        self.assertEqual(len(frames), 1)
        self.assertIn("model_output", frames[0])
        self.assertIn("模型输出文本", frames[0])

    def test_on_chat_model_stream_produces_model_stream(self):
        """on_chat_model_stream → model_stream 帧。"""
        chunk = MagicMock()
        chunk.content = "流式片段"
        # MagicMock 的 additional_kwargs 默认是 MagicMock，需显式设为空 dict
        chunk.additional_kwargs = {}
        event = {"event": "on_chat_model_stream", "name": "", "data": {"chunk": chunk}}
        frames = asyncio.run(self.sink.on_event(event))
        self.assertEqual(len(frames), 1)
        self.assertIn("model_stream", frames[0])

    def test_on_chat_model_stream_with_reasoning_produces_reasoning_stream(self):
        """on_chat_model_stream 的 chunk 带 reasoning_content → 额外产出 reasoning_stream 帧（T20）。"""
        chunk = MagicMock()
        chunk.content = "正文片段"
        chunk.additional_kwargs = {"reasoning_content": "嗯，让我想想这段怎么写"}
        event = {"event": "on_chat_model_stream", "name": "", "data": {"chunk": chunk}}
        frames = asyncio.run(self.sink.on_event(event))
        # 应产出两帧：model_stream + reasoning_stream
        self.assertEqual(len(frames), 2)
        self.assertIn("model_stream", frames[0])
        self.assertIn("正文片段", frames[0])
        self.assertIn("reasoning_stream", frames[1])
        self.assertIn("嗯，让我想想这段怎么写", frames[1])

    def test_on_chat_model_stream_no_reasoning(self):
        """chunk 不带 reasoning_content → 只产出 model_stream，无 reasoning_stream（T20 降级）。"""
        chunk = MagicMock()
        chunk.content = "普通片段"
        chunk.additional_kwargs = {}
        event = {"event": "on_chat_model_stream", "name": "", "data": {"chunk": chunk}}
        frames = asyncio.run(self.sink.on_event(event))
        self.assertEqual(len(frames), 1)
        self.assertIn("model_stream", frames[0])

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
