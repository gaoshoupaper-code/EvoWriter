"""记忆系统 trace 埋点端到端测试（REQ-20260803-120434）。

FR-004 / AC-004：覆盖**真实** ``TraceRecorder.append_event`` 写入路径，验证召回侧
（quality_callback）和写入侧（publish_callback）的 run_meta 事件都通过真实 recorder
落盘可读。该测试在未修复代码（baseline_commit）上必须失败——证明其有效性（EVD-004：
现有合成事件测试用 SimpleNamespace 绕过真实写入路径，测不出 status 漏传的 KeyError）。

修复点：
  - FR-001：``_make_quality_callback`` 的 append_event dict 必须含 ``status``（CON-001 必填）。
  - FR-002：``_make_ingestion_publish_callback`` 接线到生产/A/B 触发链，事件写进 trace run_meta。
  - FR-003：埋点失败改为可观测 warning（不再静默吞掉），但不阻断主流程（CON-002）。

设计依据：.claude/md/20260803_120434_记忆系统trace埋点不可见根治.md
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary

# v7 架构切换（REQ-20260920-150149）：harness 包的记忆挂载胶水
# （_make_quality_callback 等）随 DEC-003 冻结记忆移除，本文件只保留
# 写入侧（platform 层 ingestion 埋点）的测试。


def _thread(workspace: Path) -> ThreadSummary:
    return ThreadSummary(
        thread_id="thread-mem-trace", workspace_id="ws-mem", session_name="s",
        workspace_path=str(workspace), created_at="2026-08-03T00:00:00+00:00",
        updated_at="2026-08-03T00:00:00+00:00",
    )


def _run_meta_events(recorder: TraceRecorder, thread: ThreadSummary, trace_id: str) -> list:
    """读 trace jsonl，返回所有 type=run_meta 的事件（走真实落盘路径）。"""
    detail = recorder.read_run(thread, trace_id)
    assert detail is not None, "trace detail 应可读（事件已落盘）"
    return [ev for ev in detail.events if ev.type == "run_meta"]


class IngestionPublishCallbackRealRecorderTest(unittest.TestCase):
    """FR-002 / AC-002：写入侧 publish_callback 经真实 append_event 写入 trace。

    baseline 上 _make_ingestion_publish_callback 不存在（设施建好但从未接线，EVD-003）。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._workspace = Path(self._tmp.name)
        self._thread = _thread(self._workspace)
        self._recorder = TraceRecorder()
        self._handle = self._recorder.create_run(self._thread, "screenplay.generate.stream")
        self._trace_id = self._handle.trace_id

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_successful_ingestion_writes_memory_ingestion_event(self) -> None:
        """章节抽取入库成功 → trace 含 input.memory_ingestion、ok=True 事件。"""
        from app.domains.writing.events import _make_ingestion_publish_callback

        callback = _make_ingestion_publish_callback(self._recorder, self._trace_id)
        self.assertIsNotNone(callback)

        callback({
            "chapter_index": 1,
            "stats": {"scene": 3, "character_state": 2},
            "total_records": 5,
            "duration_ms": 1200,
            "ok": True,
            "error": None,
        })

        events = _run_meta_events(self._recorder, self._thread, self._trace_id)
        mi_events = [
            ev for ev in events
            if ev.input and isinstance(ev.input, dict) and "memory_ingestion" in ev.input
        ]
        self.assertEqual(len(mi_events), 1, "应写一条 memory_ingestion run_meta 事件")
        self.assertTrue(mi_events[0].input["memory_ingestion"]["ok"])
        self.assertEqual(mi_events[0].input["memory_ingestion"]["chapter_index"], 1)
        self.assertEqual(mi_events[0].input["memory_ingestion"]["total_records"], 5)

    def test_none_recorder_returns_none_no_callback(self) -> None:
        """recorder 为 None 时返回 None（向后兼容）。"""
        from app.domains.writing.events import _make_ingestion_publish_callback

        self.assertIsNone(_make_ingestion_publish_callback(None, self._trace_id))
        self.assertIsNone(_make_ingestion_publish_callback(self._recorder, None))


class TelemetryFailureIsObservableTest(unittest.TestCase):
    """FR-003 / AC-003 / AC-006：埋点写入失败时输出 warning，不静默、不阻断主流程。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._workspace = Path(self._tmp.name)
        self._thread = _thread(self._workspace)
        self._recorder = TraceRecorder()
        self._handle = self._recorder.create_run(self._thread, "screenplay.generate.stream")
        self._trace_id = self._handle.trace_id

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ingestion_callback_failure_logs_warning_and_does_not_raise(self) -> None:
        """写入侧埋点失败同样 warning 不静默（DEC-002 两处都治）。"""
        from app.domains.writing.events import _make_ingestion_publish_callback

        callback = _make_ingestion_publish_callback(self._recorder, self._trace_id)
        with (
            patch.object(self._recorder, "append_event", side_effect=RuntimeError("seq failed")),
            self.assertLogs("app.domains.writing.events", level="WARNING") as cm,
        ):
            callback({"chapter_index": 1, "ok": True})  # 不应抛异常

        self.assertTrue(
            any("memory_ingestion 埋点写入失败" in msg for msg in cm.output),
            "应输出 warning 日志",
        )


class IngestionCallbackWiredThroughTriggerTest(unittest.TestCase):
    """FR-002 接线验证：trigger_chapter_ingestion → extract_and_publish_sync 传递 publish_callback。"""

    def test_trigger_forwards_publish_callback(self) -> None:
        """trigger_chapter_ingestion 把 publish_callback 透传到 extract_and_publish_sync。"""
        from app.domains.writing.events import trigger_chapter_ingestion

        sentinel_cb = MagicMock()
        with patch(
            "app.domains.writing.events.extract_and_publish_sync",
            return_value={},
        ) as mock_sync:
            trigger_chapter_ingestion(
                Path("/tmp/ws"), "ws-id", 3, publish_callback=sentinel_cb,
            )
            mock_sync.assert_called_once()
            self.assertIs(
                mock_sync.call_args.kwargs.get("publish_callback"), sentinel_cb,
                "publish_callback 必须透传到 extract_and_publish_sync",
            )


if __name__ == "__main__":
    unittest.main()
