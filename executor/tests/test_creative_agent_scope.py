from pathlib import Path

import pytest

from contracts.creative_harness import build_agent_package
from contracts.architecture_manifest import load_architecture_manifest, validate_architecture_manifest
from app.domains.writing.creative_scope import Book, entry_missing
from app.domains.writing.creative_scope import CALL_ID


@pytest.mark.parametrize("agent,delegates", [
    ("outline", []), ("volume", ["volume_qc"]), ("chapter", []), ("text", ["review"]),
])
def test_each_package_contains_only_its_agent_and_helpers(tmp_path, monkeypatch, agent, delegates):
    source = Path(__file__).resolve().parents[2] / "evolution/harnesses/repo"
    dest = tmp_path / "package"
    build_agent_package(source, dest, agent)
    manifest = load_architecture_manifest(dest)
    assert validate_architecture_manifest(dest, manifest).ok
    assert [a.name for a in manifest.main_agents] == [agent]
    assert manifest.main_agents[0].delegates == delegates
    assert not (dest / "tools" / "append_chapter_deviation.py").exists()
    assert not (dest / "prompts" / "memory_extraction_guide.md").exists()
    from app.platform.agent.loader import load_package
    from contracts.runtime_context import RuntimeContext
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    class WorkingModel(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self
    ctx = RuntimeContext(model=WorkingModel(responses=["ok"]), backend=object(), checkpointer=None,
                         workspace_path=tmp_path / "book", trace_id=None)
    package = load_package(dest, f"scoped_test_{agent}")
    assert package.assemble(ctx, agent_name=agent)


def test_private_skill_reader_is_scoped_to_the_mounted_files(tmp_path, monkeypatch):
    from app.platform.agent.architecture import AgentBuildCtx, _build_tools
    from contracts.architecture_manifest import AgentSpec
    from contracts.creative_harness import write_denial
    from types import SimpleNamespace

    skill = tmp_path / "skills" / "quiet" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("quiet style; see references/examples.md", encoding="utf-8")
    reference = skill.parent / "references" / "examples.md"
    reference.parent.mkdir()
    reference.write_text("A clock ticks.", encoding="utf-8")
    (reference.parent / "binary.dat").write_bytes(b"data\0more")
    other = skill.parent / "separate" / "SKILL.md"
    other.parent.mkdir()
    other.write_text("another role's skill", encoding="utf-8")
    agent = AgentSpec(name="review", role="sub", prompt="review.md", skills=["quiet"])
    ctx = SimpleNamespace(workspace_path=tmp_path / "book")
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    [reader] = _build_tools(AgentBuildCtx(ctx, None, agent, "text", tmp_path))
    assert reader.invoke({"file_path": "/_skills_0/SKILL.md"}) == skill.read_text(encoding="utf-8")
    assert reader.invoke({"file_path": "/_skills_0/references/examples.md"}) == "A clock ticks."
    assert "examples.md" not in reader.description
    with pytest.raises(ValueError, match="文本"):
        reader.invoke({"file_path": "/_skills_0/references/binary.dat"})
    for unmounted in ("/_skills_1/SKILL.md", "/book.md", "/_skills_0/separate/SKILL.md",
                      "/_skills_0/../separate/SKILL.md"):
        with pytest.raises(ValueError, match="未挂载"):
            reader.invoke({"file_path": unmounted})
    for suite in ("text", "chapter", "volume", "outline"):
        assert write_denial(suite, "read_file", {"file_path": "/book.md"}, 1, 1)
        if suite != "text":
            monkeypatch.setenv("WRITING_AGENT_ID", suite)
            assert _build_tools(AgentBuildCtx(ctx, None, agent, "package", tmp_path)) == []


def test_gates_accept_drafts_and_first_chapter_needs_no_prior_text(tmp_path):
    assert entry_missing(tmp_path, "outline", 1, 1) == []
    assert entry_missing(tmp_path, "volume", 1, 1) == ["总纲.md"]
    (tmp_path / "章纲").mkdir()
    (tmp_path / "章纲/第001章.md").write_text("已保存的草稿", encoding="utf8")
    (tmp_path / "文风.md").write_text("本书文风", encoding="utf8")
    assert entry_missing(tmp_path, "text", 1, 1) == []
    assert "正文/第001章.md" in entry_missing(tmp_path, "text", 1, 2)


@pytest.mark.parametrize("agent,chapter,required", [
    ("outline", 1, []), ("volume", 1, ["总纲.md"]), ("chapter", 7, ["卷纲/第02卷.md"]),
    ("text", 1, ["章纲/第001章.md", "文风.md"]),
    ("text", 7, ["章纲/第007章.md", "文风.md", "正文/第006章.md"]),
])
def test_every_required_draft_opens_its_target_after_restoring(tmp_path, agent, chapter, required):
    book = Book(tmp_path)
    book.register_thread(agent, "t")
    assert entry_missing(tmp_path, agent, 2, chapter) == required
    for rel in required:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("起草中", encoding="utf8")
    for rel in required:
        (tmp_path / rel).unlink()
        assert entry_missing(tmp_path, agent, 2, chapter) == [rel]
        with pytest.raises(ValueError, match="缺少资料"):
            book.start(agent, "t", "msg", {}, 2, chapter, "a" * 40)
        (tmp_path / rel).write_text("已恢复的草稿", encoding="utf8")
    assert book.start(agent, "t", "msg", {}, 2, chapter, "a" * 40).info()["status"] == "new"


@pytest.mark.parametrize("agent,assistant,kind,rel,coordinate", [
    ("volume", "volume_qc", "volume_outline", "卷纲/第01卷.md", {"volume": 1}),
    ("text", "review", "chapter_text", "正文/第001章.md", {"chapter": 1}),
    ("text", "continuity", "chapter_text", "正文/第001章.md", {"chapter": 1}),
])
def test_private_assistant_runs_and_reads_its_draft_within_parent_trace(
    tmp_path, monkeypatch, agent, assistant, kind, rel, coordinate
):
    import asyncio
    from deepagents.backends import FilesystemBackend
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from app.domains.writing.creative_scope import ACTIVE
    from app.domains.writing.creative_tools import InstructionScopeMiddleware
    from app.domains.writing.target_execution import TargetScopeMiddleware
    from app.platform.agent.loader import load_package
    from app.platform.agent.middleware import TraceMiddleware
    from app.platform.agent.runtime import artifact_capture_scope
    from app.platform.trace import TraceRecorder
    from app.platform.trace import recorder as trace_module
    from app.schemas.screenplay import ThreadSummary
    from contracts.runtime_context import RuntimeContext

    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    monkeypatch.setattr(trace_module, "_notify_evolution", lambda *args, **kwargs: None)
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "v"))
    root = tmp_path / "book"
    for path in ["总纲.md", "文风.md", "章纲/第001章.md", rel]:
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("渡口开闸的旧稿。", encoding="utf8")
    book = Book(root)
    book.register_thread(agent, "thread")
    task = book.start(agent, "thread", "request", {}, 1, 1, "a" * 40)
    task.begin()
    src = Path(__file__).resolve().parents[2] / "evolution/harnesses/repo"
    build_agent_package(src, tmp_path / "pkg", agent)
    task.package_root = tmp_path / "pkg"
    if assistant == "continuity":
        import json
        path = task.package_root / "architecture.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        main, review = manifest["agents"]
        main["delegates"].append(assistant)
        main["skills"] = ["quiet"]
        skill = task.package_root / "skills/quiet/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: quiet\ndescription: voice\n---\nShow the silence with a clock.", encoding="utf-8")
        manifest["agents"].append({**review, "name": assistant})
        path.write_text(json.dumps(manifest), encoding="utf-8")
    package = load_package(task.package_root, "test_private_delegate_" + assistant)
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self
    skill_calls = [AIMessage(content="", tool_calls=[{
        "name": "read_writing_skill", "id": "read-skill", "args": {"file_path": "/_skills_0/SKILL.md"},
    }])] if assistant == "continuity" else []
    model = Model(responses=[*skill_calls,
        AIMessage(content="", tool_calls=[{"name": "task", "id": "delegation", "args": {
            "description": "审第1章与卷稿的相应部分", "subagent_type": assistant}}]),
        AIMessage(content="", tool_calls=[{"name": "read_artifact", "id": "read-approved",
                                          "args": {"kind": kind, **coordinate}}]),
        AIMessage(content="已审，渡口开闸的因果完整。"), AIMessage(content="已收到质检结果。"),
    ])
    recorder = TraceRecorder()
    session = book.session_dir(agent, "thread")
    row = ThreadSummary(thread_id="thread", session_name="审阅", user_id="writer", workspace_id="book",
                        workspace_path=str(session), created_at="", updated_at="")
    async def run():
        trace_id = recorder.create_run(row, "generate").trace_id
        token = ACTIVE.set(task)
        try:
            ctx = RuntimeContext(model=model, backend=FilesystemBackend(root_dir=task.view, virtual_mode=True),
                                 checkpointer=InMemorySaver(), workspace_path=task.view,
                                 trace_id=trace_id, trace_recorder=recorder, trace_middleware_cls=TraceMiddleware)
            guards = [InstructionScopeMiddleware(task, recorder, trace_id)]
            if skill_calls:
                from types import SimpleNamespace
                from contracts.target_tasks import TargetSpec
                target = TargetSpec(task_type="chapter_text", volume_ids=[1], chapter=1)
                guards.append(TargetScopeMiddleware(SimpleNamespace(
                    package_root=task.package_root, request=SimpleNamespace(target=target))))
            with artifact_capture_scope(recorder=recorder, trace_id=trace_id, workspace_root=task.view, strict=False,
                                        extra_middleware=tuple(guards)):
                graph = package.assemble(ctx, agent_name=agent)
            result = await graph.ainvoke({"messages": [HumanMessage(content="审现有草稿")]},
                                         {"configurable": {"thread_id": "thread"}, "recursion_limit": 32})
            replies = [m.content for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "delegation"]
            assert replies and "已审" in str(replies), replies
            recorder.complete_run(row, trace_id)
            recorder.flush_sync(trace_id)
            events = recorder.read_trace_events(trace_id)
            helper = assistant.replace("_", "-") + "-subagent"
            model_agents = {e.agent_name for e in events if e.type == "llm_start"}
            assert model_agents == {agent + "-subagent", helper}
            assert any(e.type == "tool_start" and e.agent_name == helper and e.tool_name == "read_artifact" for e in events)
            if skill_calls:
                loaded = [e for e in events if e.type == "skill_activation" and e.status == "completed"]
                assert [(e.agent_name, e.skill_name) for e in loaded] == [("text-subagent", "quiet")]
                assert any(isinstance(m, ToolMessage) and m.tool_call_id == "read-skill"
                           and "Show the silence with a clock." in str(m.content) for m in result["messages"])
        finally:
            ACTIVE.reset(token)
            await recorder.aclose()
    asyncio.run(run())


def test_release_discovery_never_imports_published_python(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.platform.agent import loader
    (tmp_path / "__init__.py").write_text("raise AssertionError('parent imported published code')", encoding="utf8")
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setattr(loader, "_loaded_package", None)
    monkeypatch.setattr(loader, "_current_commit", "")
    monkeypatch.setattr(loader, "_current_checkout", None)
    monkeypatch.setattr(loader, "_artifact_client", lambda: SimpleNamespace(
        current_production=lambda: SimpleNamespace(commit="a" * 40, version=1),
        ensure_artifact=lambda *args, **kwargs: tmp_path))
    loader.load_current_package()
    assert loader.production_commit() == "a" * 40
    loader.reload_current(commit="b" * 40)
    assert loader.production_commit() == "b" * 40 and loader.production_checkout() == tmp_path


def test_private_graph_confirmation_resumes_and_publishes_once(tmp_path, monkeypatch):
    from deepagents.backends import FilesystemBackend
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from app.domains.writing.creative_scope import ACTIVE
    from app.domains.writing.creative_tools import InstructionScopeMiddleware
    from app.platform.agent.loader import load_package
    from app.platform.agent.runtime import artifact_capture_scope
    from contracts.runtime_context import RuntimeContext
    import asyncio

    monkeypatch.setenv("WRITING_AGENT_ID", "outline")
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "private"))
    book = Book(tmp_path / "book")
    book.register_thread("outline", "thread")
    task = book.start("outline", "thread", "msg", {}, 1, 1, "a" * 40)
    task.begin()
    source = Path(__file__).resolve().parents[2] / "evolution/harnesses/repo"
    destination = tmp_path / "package"
    build_agent_package(source, destination, "outline")
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert "task" not in {getattr(tool, "name", None) for tool in tools}
            return self
    model = Model(responses=[
        AIMessage(content="", tool_calls=[{"name": "confirm_with_user", "id": "confirm",
            "args": {"question": "主角在河港开场吗？", "options": [{"label": "采用", "description": "从渡口起笔"}]}}]),
        AIMessage(content="", tool_calls=[{"name": "save_artifact", "id": "save",
            "args": {"kind": "outline", "mode": "full", "content": "# 总纲\n\n## 主题\n主角重建河港。"}}]),
        AIMessage(content="已保存总纲。")])
    ctx = RuntimeContext(model=model, backend=FilesystemBackend(root_dir=task.view, virtual_mode=True),
                         checkpointer=InMemorySaver(), workspace_path=task.view)
    with artifact_capture_scope(recorder=None, trace_id=None, workspace_root=task.view, strict=False,
                                extra_middleware=(InstructionScopeMiddleware(task, None, None),)):
        graph = load_package(destination, "scoped_confirm_test").assemble(ctx, agent_name="outline")
    config = {"configurable": {"thread_id": "thread:outline:1"}}
    async def run():
        token = ACTIVE.set(task)
        try:
            result = await graph.ainvoke({"messages": [HumanMessage(content="写河港主线")]}, config)
            assert result["__interrupt__"][0].value["question"] == "主角在河港开场吗？"
            assert not (book.root / "总纲.md").exists()
            final = await graph.ainvoke(Command(resume="采用"), config)
            assert final["messages"][-1].content == "已保存总纲。"
        finally:
            ACTIVE.reset(token)
    asyncio.run(run())
    assert task.info()["progress"] == ["总纲.md"]
    assert (book.root / "总纲.md").read_text(encoding="utf8").count("主角重建河港。") == 1


def test_parent_publishes_only_its_target_and_collects_deleted_private_books(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.domains.writing.creative_scope import remove_deleted_books
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "vol"))
    monkeypatch.setenv("WRITING_AGENT_ID", "outline")
    active = Book(tmp_path / "existing", owner="writer", workspace="existing")
    removed = Book(tmp_path / "deleted", owner="writer", workspace="deleted")
    removed.register_thread("outline", "thread1")
    task = removed.start("outline", "thread1", "msg", {}, 1, 1, "a" * 40)
    task.begin()
    assert "拒绝" in task.apply_worker_save("denied", {"kind": "chapter_text", "chapter": 1}, "越权的正文")
    assert "拒绝" in task.apply_worker_save("denied-draft", {"kind": "outline", "draft": True, "volume": 1}, "越权的卷纲")
    assert "已落盘" in task.apply_worker_save("allowed", {"kind": "outline"}, "主题与主线草案")
    assert "已落盘" in task.apply_worker_save("allowed", {"kind": "outline"}, "重复执行不同内容")
    assert (removed.root / "总纲.md").read_text(encoding="utf8") == "主题与主线草案"
    assert not (removed.root / "正文").exists() and not (removed.root / "卷纲").exists()
    task.mark("completed")
    store = SimpleNamespace(get_workspace=lambda owner, key: object() if key == "existing" else None)
    checkpoint = removed.session_dir("outline", "thread1") / "checkpoint.sqlite3"
    checkpoint.write_bytes(b"private checkpoint")
    asyncio.run(remove_deleted_books("outline", store, SimpleNamespace()))
    assert active.private.exists() and not removed.private.exists()
    assert not checkpoint.exists()


def test_paid_resume_claims_one_attempt_and_keeps_original_budget(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.routers import creative_agents
    from app.platform.credits import api, ledger
    book = Book(tmp_path)
    book.register_thread("outline", "t")
    payload = creative_agents.Instruction(thread_id="t", prompt="写总纲", message_id="original",
                                         billing_protocol=2, billing_budget=10)
    task = book.start("outline", "t", "original", payload.model_dump(), 1, 1, "a" * 40)
    task.begin()
    task.note_attempt("original", "original", payload.model_dump())
    task.mark("interrupted")
    paid = SimpleNamespace(config=lambda: {"enabled": True},
                           find_request=lambda user, req: {"status": "completed", "charged": 3})
    monkeypatch.setattr(ledger, "get_billing", lambda: paid)
    reserved = []
    async def reserve(billed, owner, thread):
        reserved.append(billed)
    async def execute(task, row, original, owner, traceparent):
        assert original.message_id == "original" and task.row["rev"] == "a" * 40
        yield 'event: final\ndata: {"content":"完成"}\n\n'
    monkeypatch.setattr(api, "prepare_operation", reserve)
    monkeypatch.setattr(creative_agents, "_run_private", execute)
    row = SimpleNamespace(thread_id="t")
    user = SimpleNamespace(user_id="writer")
    request = SimpleNamespace(headers={})
    async def run():
        response = await creative_agents._stream(task, row, payload, user, request, resume=True)
        duplicate = book.task("outline", task_id=task.id)
        with pytest.raises(HTTPException) as exc:
            await creative_agents._stream(duplicate, row, payload, user, request, resume=True)
        assert exc.value.status_code == 409
        assert any("event: final" in frame for frame in [frame async for frame in response.body_iterator])
    asyncio.run(run())
    assert len(reserved) == 1 and reserved[0].message_id != "original"
    from decimal import Decimal
    assert Decimal(reserved[0].billing_budget) == 7
    assert task.info()["status"] == "completed"


def test_task_snapshots_and_scoped_threads(tmp_path):
    book = Book(tmp_path)
    book.register_thread("volume", "v1")
    book.register_thread("text", "t1")
    (tmp_path / "总纲.md").write_text("旧稿", encoding="utf8")
    task = book.start("volume", "v1", "msg", {"prompt": "写第一卷"}, 1, 1, "a" * 40)
    (tmp_path / "总纲.md").write_text("新稿", encoding="utf8")
    task.begin()
    assert (task.view / "总纲.md").read_text(encoding="utf8") == "旧稿"
    assert not (task.view / ".writing/agents.sqlite3").exists()
    with pytest.raises(ValueError):
        book.start("text", "v1", "msg2", {}, 1, 1, "b" * 40)
    assert task.require_write("save_artifact", {"kind": "outline"}) is not None
    assert task.require_write("save_artifact", {"kind": "volume_outline", "volume": 2}) is not None
    assert task.require_write("save_artifact", {"kind": "volume_outline", "volume": 1}) is None


def test_text_reads_three_published_drafts_without_outline_chat_and_next_request_advances(tmp_path, monkeypatch):
    from importlib import import_module
    from app.domains.writing.conversation_log import ConversationLog
    from app.platform.agent.loader import load_package
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path / "private"))
    root = tmp_path / "book"
    monkeypatch.setenv("WRITING_AGENT_ID", "outline")
    outline = Book(root)
    outline.register_thread("outline", "outline-chat")
    session = ConversationLog(outline.session_dir("outline", "outline-chat"))
    session.admit_user("outline-chat", "私密备注：银杏信封")
    first = outline.start("outline", "outline-chat", "write", {}, 1, 1, "a" * 40)
    first.begin()
    draft = "# 总纲\n## 主题\n重开旧河港。"
    first.apply_worker_save("publish", {"kind": "outline"}, draft)
    first.mark("completed")
    for rel, body in {"卷纲/第01卷.md": "开闸", "章纲/第002章.md": "船到", "文风.md": "短句",
                      "正文/第001章.md": "去渡口"}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf8")

    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    text = Book(root)
    text.register_thread("text", "text-chat")
    task = text.start("text", "text-chat", "write-1", {}, 1, 2, "b" * 40)
    task.begin()
    source = Path(__file__).resolve().parents[2] / "evolution/harnesses/repo"
    build_agent_package(source, tmp_path / "text-package", "text")
    load_package(tmp_path / "text-package", "test_private_read")
    read = import_module("test_private_read.tools.read_artifact").build_read_artifact_tool(task.view, {})
    assert read.invoke({"kind": "outline"}) == draft
    assert read.invoke({"kind": "volume_outline", "volume": 1}) == "开闸"
    assert read.invoke({"kind": "chapter_outline", "chapter": 2}) == "船到"
    assert "outline-chat" not in text.threads("text") and text.task("text", task_id=first.id) is None
    with text.tx() as db:
        assert not any("银杏信封" in row[0] for row in db.execute("SELECT text FROM private.files"))
    (root / "总纲.md").write_text("新版主题", encoding="utf8")
    assert read.invoke({"kind": "outline"}) == draft
    task.apply_worker_save("chapter", {"kind": "chapter_text", "chapter": 2}, "旧版总纲写成的正文")
    task.mark("completed")
    again = text.start("text", "text-chat", "write-2", {}, 1, 2, "c" * 40)
    again.begin()
    assert again.row["rev"] == "c" * 40 and (again.view / "总纲.md").read_text(encoding="utf8") == "新版主题"
    assert (root / "正文/第002章.md").read_text(encoding="utf8") == "旧版总纲写成的正文"


def test_character_updates_merge_conflict_resolve_and_replay_once(tmp_path):
    book = Book(tmp_path)
    for path, content in {"人物/主角.md": "出身：渡口人", "章纲/第001章.md": "追兵到渡口", "文风.md": "短句"}.items():
        file = tmp_path / path
        file.parent.mkdir(exist_ok=True)
        file.write_text(content, encoding="utf8")
    tasks = []
    for thread in ("甲", "乙"):
        book.register_thread("text", thread)
        task = book.start("text", thread, thread, {}, 1, 1, "a" * 40)
        task.begin()
        tasks.append(task)
    def change(task, call, field, value):
        token = CALL_ID.set(call)
        try:
            return task.character("主角", field, value, "本章情节")
        finally:
            CALL_ID.reset(token)
    assert "更新" in change(tasks[0], "1", "阵营", "守军")
    assert "更新" in change(tasks[0], "1", "阵营", "守军")
    assert "更新" in change(tasks[1], "2", "伤势", "负伤")
    assert "选择" in change(tasks[1], "3", "阵营", "商队")
    assert book.conflicts("outline") == []
    conflict, = book.conflicts("text")
    assert (conflict["current"], conflict["proposed"]) == ("守军", "商队")
    book.register_thread("text", "丙")
    third = book.start("text", "丙", "丙", {}, 1, 1, "a" * 40)
    third.begin()
    assert "更新" in change(third, "4", "阵营", "河工")
    book.resolve("text", conflict["id"], "proposed")
    refreshed, = book.conflicts("text")
    assert (refreshed["current"], refreshed["proposed"]) == ("河工", "商队")
    book.resolve("text", conflict["id"], "proposed")
    card = (tmp_path / "人物/主角.md").read_text(encoding="utf8")
    assert "出身：渡口人" in card and "- 阵营：商队" in card and "- 伤势：负伤" in card
    assert card.count("第1章 · 阵营：守军") == 1


def test_interrupt_recovery_restores_view_and_deduplicates_write(tmp_path, monkeypatch):
    from app.domains.writing import creative_scope
    book = Book(tmp_path)
    book.register_thread("outline", "first")
    task = book.start("outline", "first", "msg", {}, 1, 1, "a" * 40)
    task.begin()
    calls = []
    def save():
        calls.append(1)
        (task.view / "总纲.md").write_text("草稿一", encoding="utf8")
        return "已保存"
    token = CALL_ID.set("graph-tool-1")
    try:
        task.save({"kind": "outline"}, save)
        (task.view / "总纲.md").write_text("未完成的临时写入", encoding="utf8")
        monkeypatch.setattr(creative_scope, "PROCESS", "重启后的进程")
        resumed = book.task("outline", task_id=task.id)
        assert resumed.info()["status"] == "interrupted"
        resumed.begin(resume=True)
        assert resumed.row["rev"] == "a" * 40 and resumed.recovering
        assert resumed.save({"kind": "outline"}, save) == "已保存"
        assert calls == [1] and (resumed.view / "总纲.md").read_text(encoding="utf8") == "草稿一"
        resumed.mark("stopped")
        resumed.mark("interrupted")
        assert book.task("outline", task_id=resumed.id).info()["status"] == "stopped"
    finally:
        CALL_ID.reset(token)


def test_scoped_http_uses_owned_thread_and_keeps_review_across_requests(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import creative_agents
    from app.auth import current_user
    from app.domains.writing.creative_scope import ACTIVE
    from app.platform.agent import loader
    from app.platform.credits import api as billing_api, ledger
    from app.schemas.screenplay import ThreadSummary

    (tmp_path / ".writing").mkdir()
    monkeypatch.setenv("CREATIVE_PRIVATE_ROOT", str(tmp_path.parent / "private"))
    (tmp_path / ".writing/four-agents").touch()
    threads = {}
    def create(owner, workspace, name):
        row = ThreadSummary(thread_id=str(len(threads) + 1), workspace_id=workspace, session_name=name or "创作",
                            workspace_path=str(tmp_path), created_at="", updated_at="", user_id=owner)
        threads[row.thread_id] = row
        return row
    store = SimpleNamespace(get_workspace=lambda owner, key: SimpleNamespace(workspace_path=str(tmp_path)),
                            create_thread=create, list_threads=lambda *_: list(threads.values()),
                            get_thread=lambda owner, thread: threads.get(thread))
    monkeypatch.setattr(creative_agents, "get_thread_store", lambda: store)
    monkeypatch.setattr(loader, "load_current_package", lambda: None)
    monkeypatch.setattr(loader, "production_commit", lambda: "a" * 40)
    monkeypatch.setattr(ledger, "get_billing", lambda: SimpleNamespace(config=lambda: {"enabled": False}))
    async def free(*_args):
        return None
    monkeypatch.setattr(billing_api, "prepare_operation", free)
    async def model_events(task, thread, payload, owner_id, traceparent):
        assert ACTIVE.get().agent == "outline"
        assert Path(thread.workspace_path) != tmp_path
        if not getattr(payload, "resume", None):
            yield 'event: status\ndata: {"status":"started","trace_id":"traceA"}\n\n'
            yield 'event: interrupt\ndata: {"kind":"choice","question":"采用河港吗？"}\n\n'
        else:
            yield 'event: final\ndata: {"content":"已采用河港"}\n\n'
    monkeypatch.setattr(creative_agents, "_run_private", model_events)
    monkeypatch.setattr(creative_agents, "_owned_trace", lambda row, trace_id: trace_id == "traceA")
    monkeypatch.setattr(creative_agents, "_notify_private_trace", lambda *_: None)
    monkeypatch.setattr(creative_agents, "get_agent_service", lambda: SimpleNamespace(_issue_run_binding=lambda *_: None))
    app = FastAPI()
    app.include_router(creative_agents.router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="writer")
    client = TestClient(app)
    outline = "/api/workspaces/book/agents/outline"
    text = "/api/workspaces/book/agents/text"
    monkeypatch.setenv("WRITING_AGENT_ID", "outline")
    assert client.get(outline + "/entry").json()["missing"] == []
    assert client.get(text + "/entry").status_code == 404
    first = client.post(outline + "/threads", json={}).json()["thread_id"]
    stream = outline + f"/threads/{first}/generate/stream"
    assert "event: interrupt" in client.post(stream, json={"thread_id": first, "prompt": "新书"}).text
    status = client.get(outline + "/entry").json()["task"]
    assert status["status"] == "awaiting_input" and status["awaiting_input"]["question"] == "采用河港吗？"
    assert "event: final" in client.post(stream, json={"thread_id": first, "resume": "采用", "trace_id": "traceA"}).text
    assert client.get(outline + "/tasks/" + status["task_id"]).json()["status"] == "completed"
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    assert client.get(text + f"/threads/{first}/conversation").status_code == 404
    assert client.get(text + "/tasks/" + status["task_id"]).status_code == 404


def _process_worker(root, agent, pipe, task_id=None):
    import os
    from app.domains.writing.creative_scope import ACTIVE
    from app.platform.agent.loader import load_package
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    os.environ["WRITING_AGENT_ID"] = agent
    os.environ["CREATIVE_PRIVATE_ROOT"] = str(root.parent / "private")
    book = Book(root)
    if task_id is None:
        book.register_thread(agent, agent)
        task = book.start(agent, agent, "request", {"prompt": agent}, 1, 1, "a" * 40)
        task.begin()
    else:
        task = book.task(agent, task_id=task_id)
        pipe.send(task.info())
        assert pipe.recv() == "continue"
        task.begin(resume=True)
    token = ACTIVE.set(task)
    pkg = book.private / "package"
    if not pkg.exists():
        build_agent_package(Path(__file__).resolve().parents[2] / "evolution/harnesses/repo", pkg, agent)
    load_package(pkg)
    from harness_current.tools.save_artifact import build_save_artifact_tool
    tool = build_save_artifact_tool(task.view, {}, {}, agent_name=f"{agent}-subagent")

    class State(TypedDict):
        saved: bool

    def save_node(state):
        if agent == "volume":
            pipe.send(task.info())
            assert pipe.recv() == "publish"
            args = {"kind": "volume_outline", "volume": 1, "mode": "full", "content": "卷一重建渡口。"}
        else:
            args = {"kind": "chapter_text", "chapter": 1, "unit_kind": "ordinary", "mode": "full",
                    "content": "# 第一章 · 渡口\n> 状态：草稿\n\n小舟靠岸。"}
        CALL_ID.set("save-1")
        assert "已落盘" in tool.invoke(args)
        if agent == "text":
            CALL_ID.set("save-2")
            receipt = tool.invoke(dict(args, mode="append", content="船上走下一名老人。"))
            assert "已追加落盘" in receipt, receipt
            CALL_ID.set("card-1")
            assert "已更新" in task.character("主角", "去向", "渡口", "船已靠岸")
            if not task.recovering:
                pipe.send(task.info())
                pipe.recv()  # Parent terminates this process before the graph commits its node.
        return {"saved": True}

    try:
        builder = StateGraph(State)
        builder.add_node("publish", save_node)
        builder.add_edge(START, "publish")
        builder.add_edge("publish", END)
        with SqliteSaver.from_conn_string(str(book.private / "graph.sqlite3")) as checkpoints:
            graph = builder.compile(checkpointer=checkpoints)
            config = {"configurable": {"thread_id": task.id}}
            state = graph.invoke(None if task.recovering else {"saved": False}, config, durability="sync")
            assert state["saved"]
        task.mark("completed")
        pipe.send((task.info(), (task.view / "总纲.md").read_text(encoding="utf8")))
    except Exception:
        import traceback
        pipe.send({"worker_error": traceback.format_exc()})
        raise
    finally:
        ACTIVE.reset(token)
        pipe.close()


def test_killed_text_process_preserves_graph_and_other_agent_keeps_publishing(tmp_path):
    import multiprocessing
    import sqlite3
    book = tmp_path / "book"
    for rel, value in {"总纲.md": "初次快照", "章纲/第001章.md": "靠岸", "文风.md": "短句",
                       "人物/主角.md": "船夫"}.items():
        path = book / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf8")
    children = []
    ctx = multiprocessing.get_context("spawn")
    def start(agent, task_id=None):
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=_process_worker, args=(book, agent, child, task_id))
        proc.start()
        child.close()
        children.append((proc, parent))
        return proc, parent
    def receive(pipe):
        assert pipe.poll(45), "worker did not reach the expected step"
        result = pipe.recv()
        if isinstance(result, dict) and "worker_error" in result:
            pytest.fail(result["worker_error"])
        return result
    try:
        volume, vol = start("volume")
        assert receive(vol)["status"] == "running"
        original, first = start("text")
        saved = receive(first)
        assert set(saved["progress"]) == {"正文/第001章.md", "人物/主角.md"}
        original.terminate()
        original.join(10)
        assert original.exitcode
        vol.send("publish")
        assert receive(vol)[0]["status"] == "completed"
        volume.join(10)
        assert volume.exitcode == 0 and (book / "卷纲/第01卷.md").is_file()
        (book / "总纲.md").write_text("后来改稿", encoding="utf8")
        restarted, author = start("text", saved["task_id"])
        paused = receive(author)
        assert paused == saved | {"status": "interrupted"}
        author.send("continue")
        final, snapshot = receive(author)
        restarted.join(10)
        assert restarted.exitcode == 0 and final["status"] == "completed"
        assert final["harness_commit"] == "a" * 40 and snapshot == "初次快照"
        assert (book / "正文/第001章.md").read_text(encoding="utf8").count("船上走下一名老人。") == 1
        assert (book / "人物/主角.md").read_text(encoding="utf8").count("第1章 · 去向") == 1
        with sqlite3.connect(book / ".writing/agents.sqlite3") as db:
            public_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not public_tables.intersection({"sessions", "tasks", "files", "saves", "interrupts", "attempts"})
    finally:
        for proc, pipe in children:
            if proc.is_alive():
                proc.terminate()
            proc.join(10)
            pipe.close()
