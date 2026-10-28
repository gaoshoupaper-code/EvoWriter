"""重启后审查停等 resume 不断线（2026-10-28 线上打回到下一卷事故回归）。

事故链：卷纲段段审挂起（awaiting_input）→ executor 重启（发版）→ 内存
trace 索引丢失 → 僵尸清理把 state.json 里活着的 active_traces 误判成僵尸
删掉 → 打回 resume 路由失配降级普通消息 → 新 run 的 directive 按文件树
报「当前目标：第 2 卷」→ agent 把打回第 1 卷读成重做还没开工的第 2 卷。

本测试锁三道闸：
  1. find_run_by_trace_id 带工作区提示：重启后能从工作区 traces/index.json 复活。
  2. resume_run 路径 2：重启后 resume 复用原 trace（is_new=False），不降级新开。
  3. _sweep_stale_active_traces：重启后不误杀 awaiting_input 的 active_traces。

跑法（在 executor 目录）：
    python -m pytest tests/test_restart_review_resume.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domains.writing.agent import MetaAgentService
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


def _make_thread(workspace_path: str) -> ThreadSummary:
    now = "2026-10-28T00:00:00+00:00"
    return ThreadSummary(
        thread_id="restart-thread",
        workspace_id="restart-ws",
        session_name="restart-test",
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
    )


@patch("app.platform.trace.recorder._notify_evolution")
class RestartReviewResumeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="restart_ws_")
        self.thread = _make_thread(self.tmp.name)
        self.recorder = TraceRecorder()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_awaiting_run(self) -> str:
        """造一个挂起等审查的 trace（run_awaiting 落 index.json）。"""
        handle = self.recorder.create_run(self.thread, "screenplay.generate.stream")
        self.recorder.await_input_run(self.thread, handle.trace_id)
        return handle.trace_id

    def test_find_run_by_workspace_hint_after_restart(self, _mock_notify) -> None:
        """闸1：内存索引丢失后，工作区提示直读 index.json 命中活 trace。"""
        trace_id = self._create_awaiting_run()
        restarted = TraceRecorder()  # 模拟重启：全新内存索引
        assert restarted.find_run_by_trace_id(trace_id) is None  # 无提示时扫不到
        run = restarted.find_run_by_trace_id(
            trace_id, workspace_path=self.thread.workspace_path
        )
        assert run is not None and run.status == "awaiting_input"
        # 命中回填后，无提示的后续查询也不再 404
        assert restarted.find_run_by_trace_id(trace_id) is not None

    def test_resume_run_reuses_trace_after_restart(self, _mock_notify) -> None:
        """闸2：重启后 resume 同一条 trace（is_new=False），不降级 create_run。"""
        trace_id = self._create_awaiting_run()
        restarted = TraceRecorder()
        _handle, is_new = restarted.resume_run(self.thread, trace_id)
        assert is_new is False

    def test_sweeper_keeps_awaiting_trace_after_restart(self, _mock_notify) -> None:
        """闸3：僵尸清理重启后不误杀 awaiting_input 的 active_traces。"""
        from contracts.writing_artifacts import (
            load_writing_state, save_writing_state,
        )

        trace_id = self._create_awaiting_run()
        ws = Path(self.thread.workspace_path)
        (ws / ".writing").mkdir(exist_ok=True)
        state = load_writing_state(ws)
        state.active_traces["author"] = trace_id
        save_writing_state(ws, state)

        svc = MetaAgentService.__new__(MetaAgentService)  # 只挂 sweeper 依赖
        svc.trace_recorder = TraceRecorder()  # 重启后内存为空的 recorder
        svc._sweep_stale_active_traces(ws, self.thread)

        assert load_writing_state(ws).active_traces.get("author") == trace_id

    def test_sweeper_still_removes_terminal_trace(self, _mock_notify) -> None:
        """终态 trace 照清（回归不放开僵尸口子）。"""
        from contracts.writing_artifacts import (
            load_writing_state, save_writing_state,
        )

        handle = self.recorder.create_run(self.thread, "screenplay.generate.stream")
        self.recorder.complete_run(self.thread, handle.trace_id)
        ws = Path(self.thread.workspace_path)
        (ws / ".writing").mkdir(exist_ok=True)
        state = load_writing_state(ws)
        state.active_traces["author"] = handle.trace_id
        save_writing_state(ws, state)

        # 同一 recorder（内存命中）与重启后的空 recorder 都应清掉
        for recorder in (self.recorder, TraceRecorder()):
            svc = MetaAgentService.__new__(MetaAgentService)
            svc.trace_recorder = recorder
            state = load_writing_state(ws)
            state.active_traces["author"] = handle.trace_id
            save_writing_state(ws, state)
            svc._sweep_stale_active_traces(ws, self.thread)
            assert load_writing_state(ws).active_traces.get("author") is None
