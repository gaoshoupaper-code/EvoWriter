"""活跃 trace 的 token 实时透传（REQ-20261027-012631 FR-005 / DEC-010）。

list_active_runs 必须携带 owner_user_id 与 usage_total：evolution 用户层/作品层
的 token 累计需要实时计入运行中 trace（数字随创作增长），而运行中 trace 尚未
摄入 evolution 库——executor 内存是唯一数据源。

跑法（在 executor 目录）：
    .venv/Scripts/python.exe -m pytest tests/test_active_runs_usage.py -v
"""

from __future__ import annotations

import tempfile
import unittest

from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


def _make_thread(workspace_path: str, user_id: str = "user-42") -> ThreadSummary:
    now = "2026-10-27T00:00:00+00:00"
    return ThreadSummary(
        thread_id="usage-thread",
        workspace_id="usage-ws",
        session_name="观测用量测试",
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
        user_id=user_id,
    )


class ActiveRunsUsageTest(unittest.TestCase):
    """运行中 trace 的 usage 契约：owner 与 token 累计随活跃行透传。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="active_usage_")
        self.workspace = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_active_run_carries_owner_and_usage(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"input_tokens": 700, "output_tokens": 300, "total_tokens": 1000},
        })
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"input_tokens": 2000, "output_tokens": 500, "total_tokens": 2500},
        })

        runs = recorder.list_active_runs()

        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["owner_user_id"], "user-42")
        self.assertEqual(run["usage_total"], 3500)

    def test_usage_accumulates_without_explicit_total(self) -> None:
        """usage 缺 total_tokens 时按 input+output 累加；无 usage 事件不计。"""
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
        recorder.append_event(handle.trace_id, {
            "type": "tool_end",
            "status": "completed",
            "source": "runtime",
        })

        runs = recorder.list_active_runs()

        self.assertEqual(runs[0]["usage_total"], 150)

    def test_usage_survives_index_loss_and_cleared_on_finalize(self) -> None:
        """累加器不依赖 index（内存直读）；终态清出活跃列表后不再残留。"""
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        recorder.append_event(handle.trace_id, {
            "type": "llm_end",
            "status": "completed",
            "source": "runtime",
            "usage": {"total_tokens": 800},
        })
        # 模拟 index 丢失：usage/owner 降级仍可用（累加器在内存，owner 退 None）。
        recorder._trace_workspace.pop(handle.trace_id)
        recorder._disk_miss_until[handle.trace_id] = float("inf")

        degraded = recorder.list_active_runs()
        self.assertEqual(degraded[0]["usage_total"], 800)
        self.assertIsNone(degraded[0]["owner_user_id"])

        # 终态收尾：清出活跃列表 + 累加器无泄漏。
        recorder._finalize_run(thread, handle.trace_id, "completed", 1000, None)
        self.assertEqual(recorder.list_active_runs(), [])
        self.assertNotIn(handle.trace_id, recorder._usage_totals)


if __name__ == "__main__":
    unittest.main()
