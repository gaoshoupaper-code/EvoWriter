"""步数保险丝兜底（FR-004，REQ-20261002-125538 AC-004）验证。

线上根因（trace-974f05dd，2026-10-01）：recursion_limit=300 打满时
GraphRecursionError 裸抛，用户白等 27 分钟收到原始报错，已写内容不可见。
本测试锁定部分成功收尾契约：

  recorder 层（真实 TraceRecorder）：
    - step_limit_run 写 run_end 事件 + 终态 step_limit_reached
    - 终态单调：step_limit_reached 不被孤儿兜底/僵尸接管覆写为 cancelled
    - contracts 终态集合包含 step_limit_reached（跨服务单调保护对象）

  generate_stream 层（mock agent 接缝）：
    - agent 流抛 GraphRecursionError → 不向上抛
    - SSE 收到 final 帧，content 以降级文案开头（含产物构造）
    - workspace storyline.md 原样保留
    - run 终态 step_limit_reached（非 failed）
    - 极端情况：无 storyline.md 产物 → 纯降级文案响应

跑法（在 executor 目录）：
    python -m pytest tests/test_step_limit_fallback.py -v
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langgraph.errors import GraphRecursionError

from contracts.cancel_state import is_terminal
from app.platform.trace.recorder import TraceRecorder
from app.platform.streaming import StreamResult
from app.schemas.screenplay import (
    ScreenplayGenerateRequest,
    ScreenplayGenerateResponse,
    ThreadSummary,
)

_STORYLINE_CONTENT = "# 故事线\n\n## S01 主线：少女登神\n"


def _make_thread(workspace_path: str) -> ThreadSummary:
    now = "2026-10-02T00:00:00+00:00"
    return ThreadSummary(
        thread_id="step-limit-thread",
        workspace_id="step-limit-ws",
        session_name="step-limit-test",
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
    )


@patch("app.platform.trace.recorder._notify_evolution")
class StepLimitRecorderContractTest(unittest.TestCase):
    """recorder 层：step_limit_run 终态写入与单调保护。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="step_limit_ws_")
        self.thread = _make_thread(self.tmp.name)
        self.recorder = TraceRecorder()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_running_run(self) -> str:
        handle = self.recorder.create_run(self.thread, "screenplay.generate.stream")
        return handle.trace_id

    def test_step_limit_run_writes_terminal(self, _mock_notify) -> None:
        """step_limit_run → run_end 事件 + index 终态 step_limit_reached。"""
        trace_id = self._create_running_run()
        self.recorder.step_limit_run(self.thread, trace_id)
        run = self.recorder.find_run_by_trace_id(trace_id)
        self.assertEqual(run.status, "step_limit_reached")
        events = self.recorder._read_events(Path(self.thread.workspace_path) / run.path)
        self.assertEqual(events[-1].type, "run_end")
        self.assertEqual(events[-1].status, "step_limit_reached")

    def test_orphan_finalize_does_not_overwrite(self, _mock_notify) -> None:
        """终态单调：step_limit_reached 后孤儿兜底不补写 cancelled。"""
        trace_id = self._create_running_run()
        self.recorder.step_limit_run(self.thread, trace_id)
        self.recorder.finalize_orphan_run(self.thread, trace_id)
        run = self.recorder.find_run_by_trace_id(trace_id)
        self.assertEqual(run.status, "step_limit_reached")

    def test_step_limit_in_terminal_states(self, _mock_notify) -> None:
        """contracts 终态集合包含 step_limit_reached（CON-003 保护对象）。"""
        self.assertTrue(is_terminal("step_limit_reached"))


class StepLimitFallbackStreamTest(unittest.TestCase):
    """generate_stream 层：撞保险丝走部分成功收尾（AC-004 四条件）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="step_limit_stream_")
        self.thread = _make_thread(self.tmp.name)
        self.recorder = TraceRecorder()
        # 预写 storyline.md：既验证产物保留，也走 final 响应的产物构造分支
        (Path(self.tmp.name) / "storyline.md").write_text(
            _STORYLINE_CONTENT, encoding="utf-8"
        )
        from app.domains.writing.agent import MetaAgentService

        self.service = MetaAgentService(
            settings=SimpleNamespace(writer_agent_mode="live"),
            workspace_root=Path(self.tmp.name),
            trace_recorder=self.recorder,
            style_store=None,
            checkpointer=None,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _patch_seams(self):
        """mock agent 装配接缝；run_agent_stream 的流首迭代即抛保险丝异常。"""
        async def exploding_stream():
            raise GraphRecursionError(
                "Recursion limit of 1000 reached without hitting a stop condition."
            )
            yield ""  # pragma: no cover - 仅为成为 async generator

        async def fake_run_agent_stream(agent, agent_input, config, **kwargs):
            return exploding_stream(), StreamResult()

        stack = [
            patch.object(
                self.service, "_resolve_checkpointer", AsyncMock(return_value=None),
            ),
            patch.object(self.service, "_issue_run_binding", return_value=(None, None)),
            patch.object(self.service, "_resolve_model", return_value=object()),
            patch.object(self.service, "_agent_for_workspace", return_value=object()),
            patch(
                "app.domains.writing.agent.run_agent_stream",
                side_effect=fake_run_agent_stream,
            ),
        ]
        return stack

    def _drive(self, payload) -> list[str]:
        frames: list[str] = []

        async def drive():
            async for frame in self.service.generate_stream(
                payload, self.thread, owner_id="tester"
            ):
                frames.append(frame)

        asyncio.run(drive())
        return frames

    @staticmethod
    def _frames_by_event(frames: list[str], event_type: str) -> list[dict]:
        prefix = f"event: {event_type}\ndata: "
        return [
            json.loads(frame[len(prefix):])
            for frame in frames
            if frame.startswith(prefix)
        ]

    @patch("app.platform.trace.recorder._notify_evolution")
    @patch("app.domains.writing.agent._emit_contract_snapshot")
    def test_fuse_hit_yields_partial_success(self, _mock_snap, _mock_notify) -> None:
        """AC-004：撞保险丝 → final 降级帧 + 终态 step_limit_reached + 产物保留 + 不抛。"""
        payload = ScreenplayGenerateRequest(thread_id=self.thread.thread_id,
                                            prompt="写一个玄幻故事")
        for p in self._patch_seams():
            p.start()
            self.addCleanup(p.stop)
        try:
            frames = self._drive(payload)
        except GraphRecursionError:
            self.fail("GraphRecursionError 不应向上抛（FR-004 部分成功收尾）")

        # 1. run 终态 step_limit_reached（非 failed）
        started = self._frames_by_event(frames, "status")
        trace_id = next(s["trace_id"] for s in started if s.get("status") == "started")
        run = self.recorder.find_run_by_trace_id(trace_id)
        self.assertEqual(run.status, "step_limit_reached")

        # 2. final 帧带降级文案 + 可解析为响应（产物构造成功）
        finals = self._frames_by_event(frames, "final")
        self.assertEqual(len(finals), 1)
        response = ScreenplayGenerateResponse.model_validate(finals[0])
        self.assertIn("因步数限制提前收尾", response.content)

        # 3. workspace 已写文件原样保留
        self.assertEqual(
            (Path(self.tmp.name) / "storyline.md").read_text(encoding="utf-8"),
            _STORYLINE_CONTENT,
        )

        # 4. 无 error 帧（非通用失败路径）
        self.assertEqual(self._frames_by_event(frames, "error"), [])

    @patch("app.platform.trace.recorder._notify_evolution")
    @patch("app.domains.writing.agent._emit_contract_snapshot")
    def test_fuse_hit_without_artifacts_still_finals(self, _mock_snap, _mock_notify) -> None:
        """极端边界：保险丝烧断时 workspace 尚无产物 → 纯降级文案响应，不 500。"""
        (Path(self.tmp.name) / "storyline.md").unlink()
        payload = ScreenplayGenerateRequest(thread_id=self.thread.thread_id,
                                            prompt="写一个玄幻故事")
        for p in self._patch_seams():
            p.start()
            self.addCleanup(p.stop)
        frames = self._drive(payload)

        finals = self._frames_by_event(frames, "final")
        self.assertEqual(len(finals), 1)
        response = ScreenplayGenerateResponse.model_validate(finals[0])
        self.assertIn("因步数限制提前收尾", response.content)
        started = self._frames_by_event(frames, "status")
        trace_id = next(s["trace_id"] for s in started if s.get("status") == "started")
        self.assertEqual(
            self.recorder.find_run_by_trace_id(trace_id).status, "step_limit_reached"
        )

