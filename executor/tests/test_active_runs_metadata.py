"""活跃大盘运行中元数据透传（REQ-20260920-193428 FR-003）。

list_active_runs 必须携带运行中元数据（session_name/workload/started_at 等），
evolution 大盘据此渲染完整活跃行，不依赖入库 join 降级显示 trace_id 代称。

跑法（在 executor 目录）：
    .venv/Scripts/python.exe -m pytest tests/test_active_runs_metadata.py -v
"""

from __future__ import annotations

import tempfile
import unittest

from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


def _make_thread(workspace_path: str) -> ThreadSummary:
    now = "2026-09-20T00:00:00+00:00"
    return ThreadSummary(
        thread_id="active-meta-thread",
        workspace_id="active-meta-ws",
        session_name="写一部赛博修仙小说",
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
    )


class ActiveRunsMetadataTest(unittest.TestCase):
    """运行中 trace 的元数据契约：index 可读时全量透传，读不到时降级不抛。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="active_meta_")
        self.workspace = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_active_run_carries_runtime_metadata(self) -> None:
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        recorder.create_run(thread, "screenplay.generate.stream")

        runs = recorder.list_active_runs()

        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["session_name"], "写一部赛博修仙小说")
        self.assertEqual(run["workload"], "creation")
        self.assertEqual(run["workspace_id"], "active-meta-ws")
        self.assertEqual(run["thread_id"], "active-meta-thread")
        self.assertTrue(run["started_at"])
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["run_purpose"], "user_generation")
        self.assertEqual(run["service"], "executor")

    def test_active_run_degrades_when_index_missing(self) -> None:
        """index 读不到（find_run_by_trace_id=None）时退回最小字段，不抛异常。"""
        recorder = TraceRecorder()
        thread = _make_thread(self.workspace)
        handle = recorder.create_run(thread, "screenplay.generate.stream")
        # 模拟 index 丢失：清内存 workspace 映射 + 磁盘负缓存，令反查返回 None。
        recorder._trace_workspace.pop(handle.trace_id)
        recorder._disk_miss_until[handle.trace_id] = float("inf")

        runs = recorder.list_active_runs()

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["session_name"], "")
        self.assertIsNone(runs[0]["workload"])
        self.assertIsNone(runs[0]["started_at"])
        self.assertEqual(runs[0]["status"], "running")


if __name__ == "__main__":
    unittest.main()
