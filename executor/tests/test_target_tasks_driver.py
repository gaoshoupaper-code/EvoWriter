"""Target scheduling reuses generate_stream and never generates out-of-scope prose."""
from __future__ import annotations

import asyncio
import json
from importlib import import_module
from pathlib import Path
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


def _text_driver(tmp_path, request=None):
    request = request or TargetTaskRequest(task_id="continue-text", attempt_id="first", source_commit="a" * 40,
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


@pytest.mark.parametrize(("evaluation", "with_character_card"), [(True, False), (True, True), (False, False)],
                         ids=["experiment", "experiment-with-character-card", "ordinary-target"])
def test_first_model_prompt_only_omits_attempt_identifiers_for_experiments(tmp_path, monkeypatch, evaluation,
                                                                         with_character_card):
    from app.domains.writing.agent import MetaAgentService
    from app.domains.writing.pipeline import route

    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    builder = MetaAgentService.__new__(MetaAgentService)
    captures = []
    workspaces = []
    sample_id = f"sample-{'a' * 32}"
    files = {"章纲/第001章.md": _chapter(1)}
    if with_character_card:
        from app.platform.agent.loader import load_package
        from contracts.creative_harness import build_agent_package

        card = "# 辛岚\n用船票换渡船，不说客套话。"
        files["人物/辛岚.md"] = card
        source = Path(__file__).resolve().parents[2] / "evolution/harnesses/repo"
        package = tmp_path / "text-package"
        build_agent_package(source, package, "text")
        package_name = "character_input_text_package"
        load_package(package, package_name)
        read_artifact = import_module(package_name + ".tools.read_artifact").build_read_artifact_tool
        list_characters = import_module(package_name + ".tools.list_characters").build_list_characters_tool

    for task_id in (sample_id, f"{sample_id}-a2"):
        root = tmp_path / task_id
        root.mkdir()
        request = TargetTaskRequest(
            task_id=task_id, attempt_id=task_id, source_commit="a" * 40,
            model_snapshot={"model": "fixture", "base_url": "https://example.test",
                            "api_key_ref": "experiment:fixture:model"} if evaluation else None,
            target={"task_type": "chapter_text", "chapter": 1},
            input={"confirmation_rules": "自动决定并执行，不等待人工", "files": files},
        )
        _, workspace, store, thread, execution, recorder = _text_driver(root, request)
        materialize_input(workspace, request.input)
        if with_character_card:
            assert "辛岚" in list_characters(workspace).invoke({})
            assert read_artifact(workspace, {}).invoke({"kind": "character", "name": "辛岚"}) == card
        thread = thread.model_copy(update={"thread_id": f"target-{task_id}"})
        workspaces.append(workspace)

        class Service:
            trace_recorder = recorder

            async def generate_stream(self, payload, current_thread, **kwargs):
                directive = route(workspace, current_thread.thread_id, user_text=payload.primary_text()).directive
                prompt = builder._build_user_prompt(payload, current_thread, directive=directive)
                captures.append((prompt, current_thread.thread_id, payload.message_id))
                _save_reviewed_text(workspace, execution)
                yield 'event: final\ndata: {}\n\n'

        result = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
        assert result["complete"]

    first, second = captures
    assert first[1] != second[1] and first[2] != second[2]
    assert workspaces[0] != workspaces[1]
    assert all("当前工作目录：/" in prompt and "第1章正文" in prompt for prompt, _, _ in captures)
    if evaluation:
        assert first[0] == second[0]
        assert "当前 session：" not in first[0] and "message_id:" not in first[0]
        assert "章纲/第001章.md" in first[0]
        assert "故事资料只取自题内文件" in first[0]
        assert "按系统技能目录读取匹配的写作技能" in first[0]
        if with_character_card:
            assert "人物/辛岚.md" in first[0]
            assert "本题除章纲外还提供人物卡" in first[0]
            assert "list_characters" in first[0] and "read_artifact" in first[0] and "kind=character" in first[0]
        else:
            assert "本题除章纲外还提供人物卡" not in first[0]
        assert "# 第001章 · <章名>\n> 状态：起草中｜<日期>" in first[0]
        assert all(value in first[0] for value in (
            "unit_kind=ordinary", "300–400", "450", "unit_kind=skill_scene", "skill_name",
            "mode=full", "mode=append", "mode=edit", 'description="审第1章"',
        ))
    else:
        for prompt, thread_id, message_id in captures:
            assert f"当前 session：{thread_id}" in prompt and f"message_id: {message_id}" in prompt
            assert "unit_kind=ordinary" not in prompt


def test_other_assessments_keep_existing_model_prompt(tmp_path):
    from app.domains.writing.agent import MetaAgentService
    from app.schemas.screenplay import ScreenplayGenerateRequest

    request = TargetTaskRequest(
        task_id="outline-sample", attempt_id="first", source_commit="a" * 40,
        model_snapshot={"model": "fixture", "base_url": "https://example.test",
                        "api_key_ref": "experiment:fixture:model"},
        target={"task_type": "chapter_outlines", "volume_ids": [1], "chapter": 1},
        input={"confirmation_rules": "自动确认"},
    )
    payload = ScreenplayGenerateRequest(thread_id="outline-thread", prompt="第1章章纲", message_id="outline-message")
    thread = SimpleNamespace(thread_id=payload.thread_id)
    builder = MetaAgentService.__new__(MetaAgentService)
    with FrozenExecution(request, object(), tmp_path, object(), tmp_path):
        prompt = builder._build_user_prompt(payload, thread)
    assert "当前 session：outline-thread" in prompt and "message_id: outline-message" in prompt


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


def _sentence_package(root):
    """清单里声明了写作工具的正文包（按段写作）。"""
    (root / "architecture.json").write_text(json.dumps({"schema_version": "writer.architecture/1", "agents": [
        {"name": "text", "role": "main", "tools": ["save_artifact", "write_info"]}]}), encoding="utf-8")


@pytest.mark.parametrize("by_paragraph", [True, False], ids=["paragraph-package", "old-package"])
def test_experiment_prompt_follows_the_drafting_flow_of_the_package(tmp_path, monkeypatch, by_paragraph):
    """评测注入的规程随正文包切换，旧包分支保留整批流程。"""
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    request = TargetTaskRequest(
        task_id="sample-" + "d" * 32, attempt_id="first", source_commit="a" * 40,
        model_snapshot={"model": "fixture", "base_url": "https://example.test", "api_key_ref": "experiment:fixture:model"},
        target={"task_type": "chapter_text", "chapter": 1},
        input={"confirmation_rules": "自动决定并执行，不等待人工", "files": {"章纲/第001章.md": _chapter(1)}},
    )
    _, workspace, store, thread, execution, recorder = _text_driver(tmp_path, request)
    if by_paragraph:
        _sentence_package(execution.package_root)
    prompts = []

    class Service:
        trace_recorder = recorder

        async def generate_stream(self, payload, current_thread, **kwargs):
            prompts.append(payload.prompt)
            _save_reviewed_text(workspace, execution)
            yield 'event: final\ndata: {}\n\n'

    assert asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))["complete"]
    (prompt,) = prompts
    batch_wording = ("unit_kind=ordinary", "300–400", "450", "unit_kind=skill_scene", "mode=append", "首批用 mode=full")
    if by_paragraph:
        assert "write_chapter_header" in prompt and "write_causal" in prompt
        assert 'description="审第1章"' in prompt and "mode=edit" in prompt
        assert not any(word in prompt for word in batch_wording)
    else:
        assert "write_chapter_header" not in prompt
        assert all(word in prompt for word in batch_wording)


def test_target_run_keeps_paragraphs_written_before_an_interruption(tmp_path, monkeypatch):
    """评测任务路径：这一轮中途被打断，已写的段落在流结束时补进文件；工作进程重启后不再重复。"""
    from datetime import date

    from app.platform.trace.recorder import TraceRecorder

    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    request, workspace, store, thread, execution, _recorder = _text_driver(tmp_path)
    chapter = workspace / "正文/第001章.md"
    sentences = ["小舟靠岸。", "「船家，渡我。」", "三日后，河开了。"]
    unfinished = f"# 第001章\n> 状态：起草中｜{date.today().isoformat()}\n\n" + "\n\n".join(sentences) + "\n"

    class Run:
        def __init__(self, recorder):
            self.trace_recorder = recorder

        async def write_sentences(self, current_thread):
            trace_id = self.trace_recorder.create_run(
                current_thread.model_copy(update={"workspace_path": str(workspace)}), "screenplay.generate.stream").trace_id
            for index, text in enumerate(sentences):
                for kind in ("tool_start", "tool_end"):
                    self.trace_recorder.append_event(trace_id, {
                        "type": kind, "status": "running" if kind == "tool_start" else "completed", "source": "middleware",
                        "tool_call_id": f"c{index}", "tool_name": "write_causal", "tool_args": {"text": text},
                        **({"tool_output": "第 1 段。"} if kind == "tool_end" else {})})
            yield f'event: status\ndata: {json.dumps({"status": "started", "trace_id": trace_id})}\n\n'

    class Interrupted(Run):
        async def generate_stream(self, payload, current_thread, **kwargs):
            # 三段已写出；事件还在缓冲里没落盘，任务随后被打断。
            async for frame in self.write_sentences(current_thread):
                yield frame
            raise asyncio.CancelledError()

    class Resumed(Run):
        async def generate_stream(self, payload, current_thread, **kwargs):
            assert chapter.read_text(encoding="utf-8") == unfinished
            _save_reviewed_text(workspace, execution)
            yield 'event: final\ndata: {}\n\n'

    recorder = TraceRecorder()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(drive_task(request, workspace, Interrupted(recorder), thread, execution, store))
    assert chapter.read_text(encoding="utf-8") == unfinished
    assert store.record(request.task_id)["metadata"]["draft_markers"] == {"text-thread-1": "c2"}
    assert asyncio.run(drive_task(request, workspace, Resumed(recorder), thread, execution, store))["complete"]
