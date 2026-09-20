"""active poller diff 事件生产测试（REQ-20260920-193428 FR-001/FR-002）。

poller 轮询 executor 活跃列表的增删 → run_started / run_finished 事件：
- 新 trace 出现 → run_started（携带运行中元数据，FR-003 透传）
- trace 消失 → run_finished 兜底（notify 主路径丢失时 1 个周期内补发）
- 已由 notify 发过 finished 的不重复发
- 轮询失败（executor 不可达）不发误报 finished（FR-004 失败语义）

跑法（在 evolution 目录）：
    python -m pytest tests/test_active_diff_events.py -v
"""

from __future__ import annotations

import asyncio
import unittest


class ActiveDiffEventsTest(unittest.TestCase):

    def setUp(self) -> None:
        from app.view.active import _reset_diff_state_for_test
        from app.view.events import get_event_bus

        _reset_diff_state_for_test()
        # bus 是进程级单例：清缓冲隔离其他测试文件残留的回放帧，
        # 避免 _collect() 计数被污染。
        get_event_bus()._buffer.clear()
        self.bus = get_event_bus()

    def _collect(self) -> list[dict]:
        """同步收集当前缓冲里的事件 envelope（publish 立即入缓冲）。"""
        import json

        envelopes = []
        for _, frame in list(self.bus._buffer):
            for line in frame.splitlines():
                if line.startswith("data:"):
                    envelopes.append(json.loads(line[len("data:"):]))
        return envelopes

    def test_new_run_emits_started_with_metadata(self) -> None:
        from app.view.active import _emit_diff_events

        current = [{
            "trace_id": "trace-new",
            "session_name": "写一部赛博修仙小说",
            "workload": "creation",
            "started_at": "2026-09-20T12:00:00+00:00",
            "status": "running",
        }]
        _emit_diff_events([], current)

        envelopes = self._collect()
        started = [e for e in envelopes if e["type"] == "run_started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["data"]["trace_id"], "trace-new")
        self.assertEqual(started[0]["data"]["source"], "executor")
        run = started[0]["data"]["run"]
        self.assertEqual(run["session_name"], "写一部赛博修仙小说")
        self.assertEqual(run["workload"], "creation")

    def test_vanished_run_emits_finished_fallback(self) -> None:
        from app.view.active import _emit_diff_events

        prev = [{"trace_id": "trace-gone", "session_name": "x", "status": "running"}]
        _emit_diff_events(prev, [])

        envelopes = self._collect()
        finished = [e for e in envelopes if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["data"]["trace_id"], "trace-gone")
        self.assertEqual(finished[0]["data"]["source"], "executor")

    def test_finished_dedup_between_notify_and_diff(self) -> None:
        from app.view.active import _emit_diff_events, _mark_finished_published

        _mark_finished_published("trace-dup")  # notify 主路径已发过
        _emit_diff_events([{"trace_id": "trace-dup"}], [])

        finished = [e for e in self._collect() if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 0)

    def test_poll_failure_emits_nothing(self) -> None:
        """executor 不可达 → 缓存清空但不得误发 run_finished。"""
        import app.view.active as active_mod

        active_mod._active_cache = [{"trace_id": "trace-alive"}]

        class _Boom:  # 模拟 httpx.get 抛网络异常
            def __getattr__(self, name):
                raise ConnectionError("executor unreachable")

        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "httpx":
                return _Boom()
            return real_import(name, *args, **kwargs)

        builtins.__import__ = _fake_import
        try:
            active_mod._poll_once("http://executor:7788")
        finally:
            builtins.__import__ = real_import

        self.assertEqual(active_mod._active_cache, [])  # 保留原语义：显示空
        finished = [e for e in self._collect() if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 0)  # 但不误报结束

    def test_recovery_after_failure_emits_finished_for_lost_runs(self) -> None:
        """review R2（P2）：失败→恢复的 diff 基线用 last-known-good——
        宕机窗口内结束的运行在恢复后仍能收到 run_finished（不悬挂幽灵行）。"""
        import app.view.active as active_mod

        # 建立基线：trace-alive 活跃
        active_mod._last_good_runs = [{"trace_id": "trace-alive", "session_name": "x"}]
        active_mod._active_cache = list(active_mod._last_good_runs)
        # 宕机一轮：失败清空展示缓存，last-good 保留
        self._run_poll_once_with_payload(None)
        self.assertEqual(active_mod._active_cache, [])
        # 恢复：executor 返回空列表（宕机期间 trace-alive 已结束）
        self._run_poll_once_with_payload([])
        finished = [e for e in self._collect() if e["type"] == "run_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["data"]["trace_id"], "trace-alive")

    def _run_poll_once_with_payload(self, payload) -> None:
        """绕过真实 httpx：payload=None 模拟网络失败，list 模拟成功响应。"""
        import app.view.active as active_mod

        if payload is None:
            def _fail(*args, **kwargs):
                raise ConnectionError("executor unreachable")
            payload_get = _fail
        else:
            payload_get = lambda *args, **kwargs: payload  # noqa: E731

        class _FakeHttpx:
            @staticmethod
            def get(url, timeout=None):
                resp = payload_get(url, timeout)
                class _Resp:
                    status_code = 200
                    def raise_for_status(self): pass
                    def json(self_inner): return resp
                return _Resp()

        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "httpx":
                return _FakeHttpx
            return real_import(name, *args, **kwargs)

        builtins.__import__ = _fake_import
        try:
            active_mod._poll_once("http://executor:7788")
        finally:
            builtins.__import__ = real_import


if __name__ == "__main__":
    unittest.main()
