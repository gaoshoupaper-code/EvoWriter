"""Subagent 实例拆分投影测试。

回归背景：ab_run 等无 main→task 委托边界的 trace，task 栈恒空，
ensure_agent_node 旧写法用 .get() 缺省 None 表示「从未见过该 agent」，
与「上次就在无边界下运行」（current_task=None）混为一谈——同一
subagent 的每个执行事件都拆一个新实例节点（storybuilding #1..#N
全部 0ms，调用链被噪音行淹没）。

跑法（在 evolution 目录）：
    python -m pytest tests/test_agent_instance_projection.py -v
"""

from __future__ import annotations

import unittest

from app.core.models import TraceRunSummary
from app.ingestion.projector import TraceProjector
from contracts.trace import TraceLogEvent

TRACE_ID = "trace-agent-instance-proj"


def _event(sequence: int, event_type: str, *, status: str = "running", **kw) -> TraceLogEvent:
    return TraceLogEvent(
        trace_id=TRACE_ID,
        event_id=f"evt-{sequence}",
        sequence=sequence,
        type=event_type,
        status=status,
        timestamp=f"2026-09-21T02:20:{sequence:02d}+00:00",
        source="runtime",
        schema_version=2,
        **kw,
    )


def _run() -> TraceRunSummary:
    return TraceRunSummary(
        trace_id=TRACE_ID,
        workspace_id="ws", thread_id="th", session_name="s",
        workspace_path="", endpoint="ab", status="completed",
        started_at="2026-09-21T02:20:00+00:00",
        event_count=8, path="", schema_version=2,
        service="executor", workload="creation", purpose="evolution",
        integrity_status="verified", trace_phase="sealed",
    )


def _agents(proj_nodes, agent_name: str):
    return [n for n in proj_nodes if n.kind == "agent" and n.agent_name == agent_name]


class AgentInstanceProjectionTest(unittest.TestCase):
    def test_no_task_boundary_single_instance(self) -> None:
        """无 task 边界（ab_run 直接跑顶层装配）：同一 subagent 的连续事件归并为
        一个实例节点，耗时 = 首尾事件差（回归：旧写法每事件拆一个 0ms 实例）。"""
        events = [
            _event(1, "run_start", status="completed"),
            _event(2, "llm_start", status="running", agent_name="storybuilding-subagent", model_name="m"),
            _event(3, "llm_end", status="completed", agent_name="storybuilding-subagent", model_name="m"),
            _event(4, "tool_start", status="running", agent_name="storybuilding-subagent", tool_name="ls", tool_call_id="call-a"),
            _event(5, "tool_end", status="completed", agent_name="storybuilding-subagent", tool_name="ls", tool_call_id="call-a"),
            _event(6, "tool_start", status="running", agent_name="storybuilding-subagent", tool_name="read_file", tool_call_id="call-b"),
            _event(7, "tool_end", status="completed", agent_name="storybuilding-subagent", tool_name="read_file", tool_call_id="call-b"),
        ]
        proj = TraceProjector().project(_run(), events)
        agents = _agents(proj.nodes, "storybuilding-subagent")
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].node_id, "agent:storybuilding-subagent:1")
        # 首事件 02:20:02 → 末事件 02:20:07 = 5000ms。
        self.assertEqual(agents[0].duration_ms, 5000)
        # llm/tool 节点全部挂在同一实例下。
        for n in proj.nodes:
            if n.kind in ("llm", "tool"):
                self.assertEqual(n.parent_node_id, "agent:storybuilding-subagent:1")

    def test_task_boundary_splits_instances(self) -> None:
        """有 task 委托边界：不同 task 调用仍拆成不同实例（保护原行为）。"""
        events = [
            _event(1, "run_start", status="completed"),
            _event(2, "tool_start", status="running", tool_name="task", tool_call_id="task-1"),
            _event(3, "llm_start", status="running", agent_name="storybuilding-subagent", model_name="m"),
            _event(4, "llm_end", status="completed", agent_name="storybuilding-subagent", model_name="m"),
            _event(5, "tool_end", status="completed", tool_name="task", tool_call_id="task-1"),
            _event(6, "tool_start", status="running", tool_name="task", tool_call_id="task-2"),
            _event(7, "llm_start", status="running", agent_name="storybuilding-subagent", model_name="m"),
            _event(8, "llm_end", status="completed", agent_name="storybuilding-subagent", model_name="m"),
            _event(9, "tool_end", status="completed", tool_name="task", tool_call_id="task-2"),
        ]
        proj = TraceProjector().project(_run(), events)
        agents = _agents(proj.nodes, "storybuilding-subagent")
        self.assertEqual(
            [n.node_id for n in agents],
            ["agent:storybuilding-subagent:1", "agent:storybuilding-subagent:2"],
        )

    def test_nested_review_keeps_single_instance(self) -> None:
        """栈空时段中间穿插一对 task（storybuilding 内嵌 review 委托）：
        调用方实例不被打断，review 独立成实例（线上 ab_run 真实形态，
        review 事件名 storybuilding-review-subagent）。"""
        events = [
            _event(1, "run_start", status="completed"),
            # 栈空：storybuilding 直接跑。
            _event(2, "llm_start", status="running", agent_name="storybuilding-subagent", model_name="m"),
            _event(3, "llm_end", status="completed", agent_name="storybuilding-subagent", model_name="m"),
            # storybuilding → task(review)。
            _event(4, "tool_start", status="running", agent_name="storybuilding-subagent", tool_name="task", tool_call_id="task-r"),
            _event(5, "llm_start", status="running", agent_name="storybuilding-review-subagent", model_name="m"),
            _event(6, "llm_end", status="completed", agent_name="storybuilding-review-subagent", model_name="m"),
            _event(7, "tool_end", status="completed", agent_name="storybuilding-subagent", tool_name="task", tool_call_id="task-r"),
            # 栈回到空：storybuilding 继续，必须仍是原实例。
            _event(8, "llm_start", status="running", agent_name="storybuilding-subagent", model_name="m"),
            _event(9, "llm_end", status="completed", agent_name="storybuilding-subagent", model_name="m"),
        ]
        proj = TraceProjector().project(_run(), events)
        self.assertEqual(
            [n.node_id for n in _agents(proj.nodes, "storybuilding-subagent")],
            ["agent:storybuilding-subagent:1"],
        )
        self.assertEqual(
            [n.node_id for n in _agents(proj.nodes, "storybuilding-review-subagent")],
            ["agent:storybuilding-review-subagent:1"],
        )


if __name__ == "__main__":
    unittest.main()
