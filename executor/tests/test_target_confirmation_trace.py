"""Automatic confirmation must persist valid events through the real recorder."""
from __future__ import annotations

import asyncio
import json

import pytest

from contracts.target_tasks import TargetTaskRequest
from app.domains.writing.target_execution import FrozenExecution, current_execution
from app.domains.writing.target_tasks import TargetTaskStore, drive_task, materialize_input
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import ThreadSummary


@pytest.mark.parametrize("review_kind", ["stage", "batch"])
def test_four_volume_confirmations_are_recorded_and_continue(tmp_path, review_kind):
    request = TargetTaskRequest(
        task_id="confirm-four", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [1, 2, 3, 4]},
        input={"confirmation_rules": "按既定需求确认，继续下一卷", "files": {
            "主题.md": "主题", "总纲.md": "总纲", "文风.md": "文风",
            **{f"卷纲/第{n:02d}卷.md": f"# 卷纲\n> 状态：定稿\n本卷章号范围：{n}-{n}" for n in range(1, 5)},
        }},
    )
    workspace = tmp_path / "book"
    materialize_input(workspace, request.input)
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(request)
    thread = ThreadSummary(thread_id="confirm", workspace_id="book", session_name="test",
                           workspace_path=str(workspace), created_at="now", updated_at="now")
    recorder = TraceRecorder()

    class Service:
        trace_recorder = recorder

        async def generate_stream(self, payload, current_thread, **kwargs):
            volume = current_execution().volume
            path = workspace / "章纲" / f"第{volume:03d}章.md"
            path.parent.mkdir(exist_ok=True)
            fields = ("章节概括", "矛盾驱动", "主角状态", "供给点", "章末钩子", "开头方案", "修法承接", "伏笔操作")
            path.write_text(f"# 第{volume}章\n> 状态：定稿\n" + "\n".join(
                f"{field}：具体内容" for field in fields), encoding="utf-8")
            handle = recorder.create_run(current_thread, "screenplay.generate.stream")
            yield f'event: status\ndata: {json.dumps({"trace_id": handle.trace_id})}\n\n'
            yield f'event: interrupt\ndata: {json.dumps({"kind": "review", "review_kind": review_kind})}\n\n'

    execution = FrozenExecution(request, object(), tmp_path / "package", object(), workspace)
    checks = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert checks["complete"]
    trace_ids = store.get(request.task_id).trace_ids
    assert len(trace_ids) == 4
    for trace_id in trace_ids:
        events = recorder.read_trace_events(trace_id, hydrate_payloads=True)
        confirmations = [event for event in events if event.type == "run_meta"
                         and (event.input or {}).get("target_confirmation")]
        assert len(confirmations) == 1
        assert confirmations[0].status == "completed"
        assert confirmations[0].input["target_confirmation"]["review"]["review_kind"] == review_kind
        assert events[-1].type == "run_end"
