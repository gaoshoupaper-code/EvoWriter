"""A later empty or refused QC attempt cannot erase a completed actual review."""
from __future__ import annotations

from copy import deepcopy

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from contracts.target_tasks import TargetTaskRequest
from app.domains.writing.target_execution import FrozenExecution
from app.domains.writing.target_tasks import TargetTaskManager, TargetTaskStore
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


LIMIT_NOTICE = "已达到本轮审查上限（2 次 / 本轮创作）。审查已完成，请接受当前版本，不要再调用 volume_qc。"


def _request():
    return TargetTaskRequest(task_id="volume-qc-evidence", attempt_id="first", source_commit="a" * 40,
                             target={"task_type": "volume_outlines", "volume_ids": [1, 2, 3]},
                             input={"demand_md": "完整故事需求", "confirmation_rules": "确认通过"})


def _qc(execution, number, output):
    run_id = f"qc-{number}"
    execution.observe_event({"event": "on_tool_start", "name": "task", "run_id": run_id,
                             "data": {"input": {"subagent_type": "volume_qc", "description": "质检第3卷"}}})
    execution.observe_event({"event": "on_tool_end", "name": "task", "run_id": run_id,
                             "data": {"output": output}})


@pytest.mark.parametrize("serialized", [False, True])
def test_actual_qc_survives_real_success_empty_and_limit_event_shapes(tmp_path, serialized):
    execution = FrozenExecution(_request(), object(), tmp_path, object(), tmp_path, volume=3)
    report = "已读第3卷41–60章，逐章核验因果、驱动与爽点。第43章需要补足对手行动。"
    outputs = [Command(update={"messages": [ToolMessage(content=report, tool_call_id="qc-1")]}),
               Command(update={"messages": [ToolMessage(content="", tool_call_id="qc-2")]}),
               ToolMessage(content=LIMIT_NOTICE, tool_call_id="qc-3")]
    if serialized:
        outputs = [{"update": {"messages": [{"type": "tool", "status": "success", "content": report}]}},
                   {"update": {"messages": [{"type": "tool", "status": "success", "content": ""}]}},
                   {"type": "tool", "status": "success", "content": LIMIT_NOTICE}]
    _qc(execution, 1, outputs[0])
    receipt = deepcopy(execution.evidence["volume_qc"]["3"])
    assert receipt == {"success": True, "output": report}
    for number, output in enumerate(outputs[1:], 2):
        _qc(execution, number, output)
        assert execution.evidence["volume_qc"]["3"] == receipt

    # The durable journal must keep the real report when a worker is recreated.
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(execution.request)
    store.journal(execution.request.task_id, metadata={"evidence": execution.evidence})
    restored = FrozenExecution(execution.request, object(), tmp_path, object(), tmp_path, volume=3)
    restored.evidence.update(store.record(execution.request.task_id)["metadata"]["evidence"])
    assert restored.evidence["volume_qc"]["3"] == receipt


@pytest.mark.parametrize("output", ["", LIMIT_NOTICE,
                                    {"type": "tool", "status": "success", "content": LIMIT_NOTICE}])
def test_empty_or_refused_attempt_is_not_actual_volume_qc(tmp_path, output):
    execution = FrozenExecution(_request(), object(), tmp_path, object(), tmp_path, volume=3)
    _qc(execution, 1, output)
    assert not execution.evidence["volume_qc"]["3"]["success"]


def test_parent_replay_rejects_limit_notice_and_keeps_the_actual_qc(tmp_path):
    request = _request()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    recorder = TraceRecorder()
    manager = TargetTaskManager(tmp_path / "jobs", recorder=recorder)
    thread = ThreadSummary(thread_id="qc-replay", workspace_id="qc-replay", session_name="QC",
                           workspace_path=str(workspace), created_at="now", updated_at="now")
    trace = recorder.create_run(thread, "target-test")

    def append(number, content):
        recorder.append_event(trace.trace_id, {"type": "tool_start", "status": "running", "source": "middleware",
            "tool_name": "task", "tool_call_id": f"qc-{number}",
            "tool_args": {"subagent_type": "volume_qc", "description": "质检第3卷"}})
        recorder.append_event(trace.trace_id, {"type": "tool_end", "status": "completed", "source": "middleware",
            "tool_name": "task", "tool_call_id": f"qc-{number}", "tool_output": {"status": "success", "content": content}})
        recorder.flush_sync(trace.trace_id)

    append(1, LIMIT_NOTICE)
    assert not manager._trace_evidence(request, workspace, [trace.trace_id]).get("volume_qc")
    append(2, "已读整卷并完成实际质检，指出具体章节问题。")
    append(3, "")
    append(4, LIMIT_NOTICE)
    assert manager._trace_evidence(request, workspace, [trace.trace_id])["volume_qc"]["3"]["success"]
