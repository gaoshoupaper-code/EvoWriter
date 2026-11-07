"""Single-chapter scope, upstream continuity, and fixed book ranges."""
import asyncio
from types import SimpleNamespace

import pytest

from contracts.target_tasks import TargetSpec, TargetTaskRequest, TaskInput
from app.domains.writing.target_tasks import (
    TargetTaskStore, completion_checks, validate_input, _next_step, drive_task, materialize_input,
)


CARD = "\n".join(("> 状态：定稿", "章节概括：送出证据", "矛盾驱动：守门人截信",
    "主角状态：开章藏信，章末失去通行证", "供给点：当众拆穿伪造的手令", "章末钩子：真正的收件人失踪",
    "开头方案：承接上章追兵到门外", "修法承接：无", "伏笔操作：无"))


def _single_input(chapter=25):
    files = {"主题.md": "信使以证据夺回说话权", "总纲.md": "三卷，查出伪造者并送达证据",
        "文风.md": "第三人称，动作清楚", "卷纲/第02卷.md": "> 状态：定稿\n本卷章号范围：21-40\n本章拆穿手令，下章寻找收件人"}
    if chapter > 1:
        files[f"章纲/第{chapter - 1:03d}章.md"] = CARD
        files[f"正文/第{chapter - 1:03d}章.md"] = "# 前章\n> 状态：定稿\n追兵追到门外，主角藏好信件。"
    return TaskInput(files=files, confirmation_rules="仅生成指定章，不增减章号")


def _write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_single_outline_requires_previous_chapter_context():
    target = TargetSpec(task_type="chapter_outlines", volume_ids=[2], chapter=25)
    data = _single_input()
    del data.files["章纲/第024章.md"]
    with pytest.raises(ValueError, match="第024章"):
        validate_input(target, data)


def test_single_outline_rejects_chapter_outside_supplied_volume():
    target = TargetSpec(task_type="chapter_outlines", volume_ids=[2], chapter=50)
    with pytest.raises(ValueError, match="范围"):
        validate_input(target, _single_input(50))


def test_single_outline_completion_does_not_require_other_chapters(tmp_path):
    target = TargetSpec(task_type="chapter_outlines", volume_ids=[2], chapter=25)
    data = _single_input()
    validate_input(target, data)
    for name, text in data.files.items():
        _write(tmp_path, name, text)
    assert _next_step(target, tmp_path) == ("chapter", 2, 25)
    _write(tmp_path, "章纲/第025章.md", CARD)
    assert completion_checks(target, tmp_path)["complete"]
    assert _next_step(target, tmp_path) is None


def test_book_twenty_chapters_rejects_gaps_and_short_volumes(tmp_path):
    target = TargetSpec(task_type="volume_outlines", volume_ids=[1, 2, 3], chapters_per_volume=20)
    for volume in target.volume_ids:
        first, last = (volume - 1) * 20 + 1, volume * 20
        table = "| 章号 | 剧情安排 | 主角想要 | 当前矛盾 | 爽点推进 |\n|---|---|---|---|---|\n"
        table += "\n".join(f"| {chapter} | 因此前往城门→发现伪令 | 送信 | 守门人阻拦 | 当众揭穿伪令 |" for chapter in range(first, last + 1))
        _write(tmp_path, f"卷纲/第{volume:02d}卷.md", f"> 状态：定稿\n本卷章号范围：{first}-{last}\n## 剧情推进表\n" + table)
    assert completion_checks(target, tmp_path)["complete"]
    _write(tmp_path, "卷纲/第02卷.md", "> 状态：定稿\n本卷章号范围：22-41\n规划")
    assert not completion_checks(target, tmp_path)["complete"]


@pytest.mark.parametrize("rows", [[], list(range(1, 20)), [*range(1, 20), 19]])
def test_fixed_book_rejects_missing_or_duplicated_chapter_plans(tmp_path, rows):
    target = TargetSpec(task_type="volume_outlines", volume_ids=[1], chapters_per_volume=20)
    table = "| 章号 | 剧情安排 | 主角想要 | 当前矛盾 | 爽点推进 |\n|---|---|---|---|---|\n"
    table += "\n".join(f"| {chapter} | 因此前往城门→发现伪令 | 送信 | 守门人阻拦 | 当众揭穿伪令 |" for chapter in rows)
    _write(tmp_path, "卷纲/第01卷.md", "> 状态：定稿\n本卷章号范围：1-20\n## 剧情推进表\n" + table)
    assert not completion_checks(target, tmp_path)["complete"]


@pytest.mark.parametrize("changed", ["正文/第025章.md", "正文/第024章.md"])
def test_parent_rejects_native_body_writes_during_single_outline(tmp_path, changed):
    from app.domains.writing.target_tasks import TargetTaskManager
    request = TargetTaskRequest(task_id="outline-body-scope", attempt_id="one", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [2], "chapter": 25}, input=_single_input())
    workspace = tmp_path / "workspace"
    materialize_input(workspace, request.input)
    _write(workspace, "章纲/第025章.md", CARD)
    _write(workspace, changed, "> 状态：初稿\n未经任务授权的新正文。")
    manager = TargetTaskManager(tmp_path / "jobs")
    manager.store.create(request, metadata={"workspace": str(workspace), "owner_id": None})
    trusted = manager.store.trusted(request.task_id)
    manager.store.update(request.task_id, status="completed", completed=True)
    result = manager.verify_result(request.task_id, trusted)
    assert result.status == "failed" and not result.completed
    assert "越界" in result.error


@pytest.mark.parametrize("removed", ["正文/第024章.md", "章纲/第024章.md"])
def test_parent_rejects_deleting_single_outline_context(tmp_path, removed):
    from app.domains.writing.target_tasks import TargetTaskManager
    request = TargetTaskRequest(task_id="outline-context-scope", attempt_id="one", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [2], "chapter": 25}, input=_single_input())
    workspace = tmp_path / "workspace"
    materialize_input(workspace, request.input)
    _write(workspace, "章纲/第025章.md", CARD)
    (workspace / removed).unlink()
    manager = TargetTaskManager(tmp_path / "jobs")
    manager.store.create(request, metadata={"workspace": str(workspace), "owner_id": None})
    trusted = manager.store.trusted(request.task_id)
    manager.store.update(request.task_id, status="completed", completed=True)
    result = manager.verify_result(request.task_id, trusted)
    assert result.status == "failed" and "越界" in result.error


def test_scope_guard_rejects_other_chapters_before_writing(tmp_path):
    from app.domains.writing.target_execution import FrozenExecution, TargetScopeMiddleware
    request = TargetTaskRequest(task_id="single", attempt_id="one", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [2], "chapter": 25},
        input=_single_input())
    execution = FrozenExecution(request, object(), tmp_path, object(), tmp_path)
    middleware = TargetScopeMiddleware(execution)
    called = []
    wrong = SimpleNamespace(tool_call={"id": "wrong", "name": "save_artifact",
        "args": {"kind": "chapter_outline", "chapter": 26, "content": CARD}})
    result = middleware.wrap_tool_call(wrong, lambda request: called.append(request))
    assert result.status == "error" and not called
    right = SimpleNamespace(tool_call={"id": "right", "name": "save_artifact",
        "args": {"kind": "chapter_outline", "chapter": 25, "content": CARD}})
    middleware.wrap_tool_call(right, lambda request: called.append(request))
    assert called == [right]


def test_runtime_injects_domain_scope_guard_without_changing_normal_assembly(tmp_path, monkeypatch):
    from app.platform.agent.runtime import factory
    marker = object()
    monkeypatch.setattr(factory, "_create_deep_agent", lambda *args, **kwargs: kwargs)
    with factory.artifact_capture_scope(recorder=SimpleNamespace(), trace_id="scope", workspace_root=tmp_path,
                                       strict=False, extra_middleware=(marker,)):
        result = factory.create_deep_agent(middleware=[], subagents=[{"name": "review", "description": "review", "system_prompt": "review"}])
    assert marker in result["middleware"]
    assert marker in result["subagents"][0]["middleware"]
    assert factory.create_deep_agent(middleware=[])["middleware"] == []


def test_old_stored_request_without_new_optional_field_is_still_idempotent(tmp_path):
    request = TargetTaskRequest(task_id="old-four-volumes", attempt_id="one", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [1, 2, 3, 4]},
        input={"confirmation_rules": "continue"})
    store = TargetTaskStore(tmp_path)
    store.create(request)
    old = store.record(request.task_id)
    old["request"]["target"].pop("chapters_per_volume", None)
    store._write(request.task_id, old)
    store.approve(request.task_id, {**old, "verified": False})
    result, created = store.create(request)
    assert not created and result.task_id == request.task_id


def test_driver_generates_only_the_requested_chapter_card(tmp_path):
    from app.domains.writing.target_execution import FrozenExecution
    from app.schemas.screenplay import ThreadSummary
    request = TargetTaskRequest(task_id="single-driver", attempt_id="one", source_commit="a" * 40,
        target={"task_type": "chapter_outlines", "volume_ids": [2], "chapter": 25}, input=_single_input())
    workspace = tmp_path / "workspace"
    materialize_input(workspace, request.input)
    store = TargetTaskStore(tmp_path / "jobs")
    store.create(request)
    thread = ThreadSummary(thread_id="single", workspace_id="ws", session_name="test",
        workspace_path=str(workspace), created_at="now", updated_at="now")
    calls = []

    class Service:
        trace_recorder = SimpleNamespace(read_trace_events=lambda *args: [])

        async def generate_stream(self, payload, current_thread, **kwargs):
            calls.append(payload)
            assert "只生成第25章章纲" in payload.prompt
            _write(workspace, "章纲/第025章.md", CARD)
            yield 'event: final\ndata: {"content":"完成指定章纲"}\n\n'

    execution = FrozenExecution(request, object(), tmp_path, object(), workspace)
    checks = asyncio.run(drive_task(request, workspace, Service(), thread, execution, store))
    assert checks["complete"] and len(calls) == 1
    assert {p.name for p in (workspace / "章纲").iterdir()} == {"第024章.md", "第025章.md"}
