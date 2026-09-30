"""generate_stream finally 终态兜底（finalize_orphan_run）验证。

线上样本（trace-c14edb79，2026-09-30）：SSE 断连取消时 CancelledError 已被
LLM 中间件记录为 llm_error，但外层生成器的 except asyncio.CancelledError
分支未执行（第四路：生成器被 close/GC），run 永久停在 running——进化端
一直显示"执行中"。本测试直接验证 finally 兜底契约：
  - 停在 running 的活跃 run → 补写 cancelled 终态
  - 已终态收尾的 run → 不重复写
  - awaiting_input → 保持（等 resume，不补写）

跑法（在 executor 目录）：
    python -m pytest tests/test_orphan_run_finalize.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


def _make_thread(workspace_path: str) -> ThreadSummary:
    now = "2026-09-30T00:00:00+00:00"
    return ThreadSummary(
        thread_id="orphan-thread",
        workspace_id="orphan-ws",
        session_name="orphan-test",
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
    )


@patch("app.platform.trace.recorder._notify_evolution")
class FinalizeOrphanRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="orphan_ws_")
        self.thread = _make_thread(self.tmp.name)
        self.recorder = TraceRecorder()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_running_run(self) -> str:
        """创建 trace 并写一条运行中事件——停在 running（复现孤儿前态）。"""
        handle = self.recorder.create_run(self.thread, "screenplay.generate.stream")
        self.recorder.append_event(
            handle.trace_id,
            {"type": "llm_start", "status": "running", "source": "runtime",
             "input": {"messages": [{"role": "user", "content": "hi"}]}},
        )
        return handle.trace_id

    def test_running_run_gets_cancelled(self, _mock_notify) -> None:
        """第四路孤儿：except 未收尾仍 running → 兜底补写 cancelled。"""
        trace_id = self._create_running_run()
        self.recorder.finalize_orphan_run(self.thread, trace_id)
        run = self.recorder.find_run_by_trace_id(trace_id)
        self.assertEqual(run.status, "cancelled")
        # 终态事件已写盘（jsonl 末尾是 run_cancelled）。
        run = self.recorder.find_run_by_trace_id(trace_id)
        events = self.recorder._read_events(Path(self.thread.workspace_path) / run.path)
        self.assertEqual(events[-1].type, "run_cancelled")

    def test_terminal_run_untouched(self, _mock_notify) -> None:
        """已终态（completed）→ 兜底不重复写、不覆盖状态。"""
        trace_id = self._create_running_run()
        self.recorder.complete_run(self.thread, trace_id)
        self.recorder.finalize_orphan_run(self.thread, trace_id)
        run = self.recorder.find_run_by_trace_id(trace_id)
        self.assertEqual(run.status, "completed")

    def test_awaiting_input_kept(self, _mock_notify) -> None:
        """awaiting_input（HITL 等 resume）→ 保持不补写。"""
        trace_id = self._create_running_run()
        self.recorder.await_input_run(self.thread, trace_id)
        self.recorder.finalize_orphan_run(self.thread, trace_id)
        run = self.recorder.find_run_by_trace_id(trace_id)
        self.assertEqual(run.status, "awaiting_input")
