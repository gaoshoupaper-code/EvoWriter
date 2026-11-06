"""Target scheduling reuses generate_stream and never generates out-of-scope prose."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import pytest

from contracts.target_tasks import TargetTaskRequest
from app.domains.writing.target_execution import FrozenExecution, current_execution
from app.domains.writing.target_tasks import TargetTaskStore, drive_task, materialize_input
from app.schemas.screenplay import ThreadSummary


def _chapter(number):
    return f"# 章纲 · 第{number:03d}章\n> 状态：定稿\n" + "\n".join(
        f"{field}：实际内容" for field in ("章节概括", "矛盾驱动", "主角状态", "供给点", "章末钩子", "开头方案", "修法承接", "伏笔操作")
    )


def test_four_volumes_run_in_order_without_text_generation(tmp_path):
    request = TargetTaskRequest(
        task_id="four-volumes", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [2, 4, 6, 8]},
        input={"confirmation_rules": "所有确认按输入自行决定", "files": {
            "主题.md": "主题", "总纲.md": "总纲", "文风.md": "风格",
            **{f"卷纲/第{n:02d}卷.md": f"# 卷纲\n> 状态：定稿\n本卷章号范围：{n}-{n}" for n in [2, 4, 6, 8]},
        }},
    )
    workspace = tmp_path / "workspace"
    materialize_input(workspace, request.input)
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(request)
    thread = ThreadSummary(thread_id="test-thread", workspace_id="test-ws", session_name="test",
                           workspace_path=str(workspace), created_at="now", updated_at="now", user_id="test-owner")
    recorder = SimpleNamespace(read_trace_events=lambda *args: [], append_event=lambda *args: None,
                               complete_run=lambda *args: None)
    visited = []

    class Service:
        trace_recorder = recorder

        async def generate_stream(self, payload, current_thread, **kwargs):
            execution = current_execution()
            visited.append((execution.stage, execution.volume))
            assert execution.request.source_commit == "a" * 40
            path = workspace / "章纲" / f"第{execution.volume:03d}章.md"
            path.parent.mkdir(exist_ok=True)
            path.write_text(_chapter(execution.volume), encoding="utf-8")
            yield f'event: status\ndata: {{"status":"started","trace_id":"trace-{execution.volume}"}}\n\n'
            yield 'event: interrupt\ndata: {"kind":"review","review_kind":"stage","question":"本批已完成"}\n\n'

    execution = FrozenExecution(request, object(), tmp_path / "package", object(), workspace)
    result = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert result["complete"]
    assert visited == [("chapter", 2), ("chapter", 4), ("chapter", 6), ("chapter", 8)]
    assert not (workspace / "正文").exists()
    assert len(store.get(request.task_id).trace_ids) == 4


def test_final_chapter_files_with_missing_fields_receive_specific_repair_instructions(tmp_path):
    from app.platform.trace.recorder import TraceRecorder
    import json

    request = TargetTaskRequest(
        task_id="repair-four", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [1, 2, 3, 4]},
        input={"confirmation_rules": "自行补齐并确认，不等待人工", "files": {
            "主题.md": "主题", "总纲.md": "总纲", "文风.md": "文风",
            **{f"卷纲/第{n:02d}卷.md": f"> 状态：定稿\n本卷章号范围：{n}-{n}" for n in range(1, 5)},
        }},
    )
    workspace = tmp_path / "workspace"
    materialize_input(workspace, request.input)
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(request)
    thread = ThreadSummary(thread_id="repair", workspace_id="book", session_name="test",
                           workspace_path=str(workspace), created_at="now", updated_at="now")
    recorder = TraceRecorder()
    calls = []

    class Service:
        trace_recorder = recorder

        async def generate_stream(self, payload, current_thread, **kwargs):
            calls.append(payload.prompt)
            (workspace / "章纲").mkdir(exist_ok=True)
            if len(calls) == 2:
                for number in range(1, 5):
                    assert f"章纲/第{number:03d}章.md" in payload.prompt
                for field in ("章节概括", "矛盾驱动", "主角状态", "供给点", "章末钩子", "开头方案", "修法承接", "伏笔操作"):
                    assert field in payload.prompt
                diagnostic = next(line for line in payload.prompt.splitlines() if line.startswith("逐章结构缺项："))
                issues = json.loads(diagnostic.split("：", 1)[1])
                assert not issues["章纲/第001章.md"]["未定稿"]
                assert "供给点" not in issues["章纲/第001章.md"]["缺少字段"]
                assert "供给点" in issues["章纲/第002章.md"]["缺少字段"]
                assert "章末钩子" not in issues["章纲/第002章.md"]["缺少字段"]
            for number in range(1, 5):
                content = f"# 第{number}章\n> 状态：定稿\n本章事件：起身送信。"
                content += "\n供给点：观察路线。" if number == 1 else "\n章末钩子：出现阻碍。"
                if len(calls) == 2:
                    content = _chapter(number)
                (workspace / "章纲" / f"第{number:03d}章.md").write_text(content, encoding="utf-8")
            trace_id = recorder.create_run(current_thread, "screenplay.generate.stream").trace_id
            yield f'event: status\ndata: {json.dumps({"trace_id": trace_id})}\n\n'
            yield 'event: interrupt\ndata: {"kind":"review","review_kind":"stage"}\n\n'

    execution = FrozenExecution(request, object(), tmp_path / "package", object(), workspace)
    checks = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert checks["complete"] and len(calls) == 2


def test_restored_review_evidence_finishes_without_regeneration(tmp_path):
    request = TargetTaskRequest(
        task_id="saved-text", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "chapter_text", "chapter": 1},
        input={"confirmation_rules": "确认通过"},
    )
    workspace = tmp_path / "workspace"
    (workspace / "正文").mkdir(parents=True)
    (workspace / "正文/第001章.md").write_text("# 第001章\n> 状态：初稿\n已经审好的正文", encoding="utf-8")
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(request)
    store.journal(request.task_id, metadata={"evidence": {"review": "success", "needs_revision": False}})
    thread = ThreadSummary(thread_id="test-thread", workspace_id="test-ws", session_name="test",
                           workspace_path=str(workspace), created_at="now", updated_at="now", user_id="test-owner")

    class Service:
        async def generate_stream(self, *args, **kwargs):
            raise AssertionError("已完成正文不应重新生成")
            yield ""

    execution = FrozenExecution(request, object(), tmp_path, object(), workspace)
    result = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert result["complete"]


def test_restored_final_volume_without_qc_evidence_must_resume_checking(tmp_path, monkeypatch):
    from app.domains.writing import target_tasks
    request = TargetTaskRequest(
        task_id="restore-qc", attempt_id="first", source_commit="a" * 40,
        target={"task_type": "volume_outlines", "volume_ids": [1]},
        input={"demand_md": "全书一卷", "confirmation_rules": "确认通过"},
    )
    workspace = tmp_path / "workspace"
    (workspace / "卷纲").mkdir(parents=True)
    (workspace / "卷纲/第01卷.md").write_text("# 卷纲\n> 状态：定稿\n本卷章号范围：1-2\n内容", encoding="utf-8")
    monkeypatch.setattr(target_tasks, "scan_writing_progress", lambda root: SimpleNamespace(
        theme_done=True, outline_done=True, outline_volumes=1,
    ))
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(request)
    thread = ThreadSummary(thread_id="test", workspace_id="ws", session_name="test", workspace_path=str(workspace),
                           created_at="now", updated_at="now", user_id="owner")

    class Service:
        async def generate_stream(self, *args, **kwargs):
            raise RuntimeError("必须补齐实际质检")
            yield ""

    execution = FrozenExecution(request, object(), tmp_path, object(), workspace)
    with pytest.raises(RuntimeError, match="必须补齐实际质检"):
        asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))


def _text_driver(tmp_path):
    request = TargetTaskRequest(task_id="continue-text", attempt_id="first", source_commit="a" * 40,
                                target={"task_type": "chapter_text", "chapter": 1},
                                input={"confirmation_rules": "自动决定并执行，不等待人工"})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(request)
    thread = ThreadSummary(thread_id="text-thread", workspace_id="ws", session_name="test",
                           workspace_path=str(workspace), created_at="now", updated_at="now")
    execution = FrozenExecution(request, object(), tmp_path, object(), workspace)
    recorder = SimpleNamespace(read_trace_events=lambda *args: [], append_event=lambda *args: None,
                               complete_run=lambda *args: None)
    return request, workspace, store, thread, execution, recorder


def _save_reviewed_text(workspace, execution):
    path = workspace / "正文/第001章.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("# 第001章\n> 状态：初稿\n保存的完整初稿。", encoding="utf-8")
    execution.observe_event({"event": "on_tool_start", "name": "task", "run_id": "real-review",
                             "data": {"input": {"subagent_type": "review", "description": "审稿第1章"}}})
    execution.observe_event({"event": "on_tool_end", "name": "task", "run_id": "real-review",
                             "data": {"output": "补充冲突与行动。"}})
    path.write_text("# 第001章\n> 状态：初稿\n按审稿意见补充了冲突与行动。", encoding="utf-8")
    execution.observe_event({"event": "on_tool_end", "name": "save_artifact", "run_id": "real-revision",
                             "data": {"output": "已实际保存"}})


def test_final_without_saved_artifact_continues_the_same_checkpoint(tmp_path):
    request, workspace, store, thread, execution, recorder = _text_driver(tmp_path)
    calls = []

    class Service:
        trace_recorder = recorder

        async def generate_stream(self, payload, current_thread, **kwargs):
            calls.append(current_thread.thread_id)
            if len(calls) == 2:
                _save_reviewed_text(workspace, execution)
            yield 'event: final\ndata: {"content":"已起草一部分，下一轮继续"}\n\n'

    checks = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert checks["complete"]
    assert len(calls) == 2 and calls[0] == calls[1]


def test_saved_draft_without_review_continues_until_actual_review_and_revision(tmp_path):
    request, workspace, store, thread, execution, recorder = _text_driver(tmp_path)
    calls = []

    class Service:
        trace_recorder = recorder

        async def generate_stream(self, payload, current_thread, **kwargs):
            calls.append(current_thread.thread_id)
            if len(calls) == 1:
                (workspace / "正文").mkdir()
                (workspace / "正文/第001章.md").write_text("# 第001章\n> 状态：初稿\n未审稿的实际初稿。", encoding="utf-8")
            else:
                _save_reviewed_text(workspace, execution)
            yield 'event: final\ndata: {"content":"本轮完成"}\n\n'

    checks = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert checks["complete"] and checks["text_evidence"]["revised"]
    assert len(calls) == 2 and calls[0] == calls[1]


def test_repeated_final_without_durable_progress_stops_after_three_rounds(tmp_path):
    request, workspace, store, thread, execution, recorder = _text_driver(tmp_path)
    calls = []

    class Service:
        trace_recorder = recorder

        async def generate_stream(self, payload, current_thread, **kwargs):
            calls.append(current_thread.thread_id)
            assert len(calls) <= 3, "无持久进展循环没有停止"
            yield 'event: final\ndata: {"content":"没有落盘的重复回复"}\n\n'

    with pytest.raises(ValueError, match="进展"):
        asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert len(calls) == 3 and len(set(calls)) == 1
    assert not store.get(request.task_id).completed
