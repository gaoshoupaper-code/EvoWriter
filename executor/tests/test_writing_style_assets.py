"""REQ-20261102-162419: whole-card assets, isolation and failure recovery."""
from pathlib import Path
import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.platform.core.db import Database, UserRepository, init_database
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.state.thread_store import ThreadStore
from app.routers import writing_styles as routes
from app.domains.writing import style_assets
from app.domains.writing.annotations import create_annotation, load_annotations, save_annotations
from app.schemas.screenplay import AnnotationCreateRequest
from contracts.writing_artifacts import WritingState, save_writing_state

CARD = "# 文风 · 旧书\r\n\r\n冷静短句。\r\n\n## 样章\n原样 **保留**。\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = Database(tmp_path / "meta.db", load_master_key(generate_master_key()))
    init_database(db)
    users = UserRepository(db)
    owner = users.create(username="owner", password="password123")["user_id"]
    other = users.create(username="other", password="password123")["user_id"]
    store = ThreadStore(db, tmp_path / "workspace")
    books = [store.create_workspace(owner, title) for title in ("甲书", "乙书")]
    foreign = store.create_workspace(other, "他人的书")
    for book in [*books, foreign]:
        save_writing_state(Path(book.workspace_path), WritingState(stage="text", confirmed_stage="text"))
    active = []
    monkeypatch.setattr(routes, "get_thread_store", lambda: store)
    monkeypatch.setattr(routes, "get_trace_recorder", lambda: SimpleNamespace(list_active_runs=lambda: active))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id=owner)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(db=db, app=app, client=client, owner=owner, other=other, books=books, foreign=foreign, active=active)
    db.close()


def save(env, *, book=0, name="冷静", target=None):
    payload = {"name": name}
    if target:
        payload["target_style_id"] = target
    return env.client.post(f"/api/workspaces/{env.books[book].workspace_id}/writing-style/save", json=payload)


def import_card(env, style_id, book=0):
    return env.client.post(f"/api/workspaces/{env.books[book].workspace_id}/writing-style/import", json={"style_id": style_id})


def seed(env, content=CARD, name="冷静"):
    (Path(env.books[0].workspace_path) / "文风.md").write_bytes(content.encode())
    response = save(env, name=name)
    assert response.status_code == 200, response.text
    return response.json()


def test_roundtrip_preserves_exact_content_and_snapshots(env):
    asset = seed(env)
    assert asset["content"] == CARD
    assert import_card(env, asset["style_id"], 1).status_code == 200
    book2 = Path(env.books[1].workspace_path) / "文风.md"
    assert book2.read_bytes() == CARD.encode()
    (Path(env.books[0].workspace_path) / "文风.md").write_text("新版", encoding="utf-8")
    assert save(env).status_code == 409
    assert save(env, target=asset["style_id"]).json()["content"] == "新版"
    assert book2.read_bytes() == CARD.encode()
    assert import_card(env, asset["style_id"], 1).status_code == 200
    assert book2.read_text(encoding="utf-8") == "新版"


def test_rename_delete_and_no_content_edit_endpoint(env):
    asset = seed(env)
    seed(env, name="另一份")
    url = f"/api/writing-styles/{asset['style_id']}"
    assert env.client.patch(url, json={"name": "  新名称  "}).json()["content"] == CARD
    assert env.client.patch(url, json={"name": "另一份"}).status_code == 409
    assert env.client.patch(url, json={"name": " "}).status_code == 422
    assert env.client.patch(url, json={"name": "新名称", "content": "不可手改"}).status_code == 422
    assert env.client.post("/api/writing-styles", json={"name": "手建", "content": "x"}).status_code == 405
    assert import_card(env, asset["style_id"], 1).status_code == 200
    assert env.client.delete(url).status_code == 200
    assert (Path(env.books[1].workspace_path) / "文风.md").read_bytes() == CARD.encode()


def test_missing_card_validation_and_duplicate_save(env):
    assert save(env).status_code == 404
    (Path(env.books[0].workspace_path) / "文风.md").write_bytes(CARD.encode())
    assert save(env, name=" ").status_code == 422
    assert save(env, name=" 冷静 ").status_code == 200
    assert save(env).status_code == 409
    assert len(env.client.get("/api/writing-styles").json()) == 1


def test_owner_filters_every_operation(env):
    asset = seed(env)
    asset_id = asset["style_id"]
    foreign_ws = env.foreign.workspace_id
    assert env.client.post(f"/api/workspaces/{foreign_ws}/writing-style/import", json={"style_id": asset_id}).status_code == 404
    (Path(env.foreign.workspace_path) / "文风.md").write_text("本人作品", encoding="utf-8")
    assert env.client.post(f"/api/workspaces/{foreign_ws}/writing-style/save", json={"name": "本人保存", "target_style_id": asset_id}).status_code == 404
    env.app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id=env.other)
    assert env.client.get("/api/writing-styles").json() == []
    assert env.client.patch(f"/api/writing-styles/{asset_id}", json={"name": "偷改"}).status_code == 404
    assert env.client.delete(f"/api/writing-styles/{asset_id}").status_code == 404
    assert import_card(env, asset_id).status_code == 404
    assert save(env, target=asset_id).status_code == 404
    assert env.client.post(f"/api/workspaces/{foreign_ws}/writing-style/import", json={"style_id": asset_id}).status_code == 404


def test_import_preserves_fifty_chapters_and_stage_and_rejects_only_current_run(env):
    asset = seed(env)
    ws = Path(env.books[1].workspace_path)
    for directory in ("正文", "章纲"):
        (ws / directory).mkdir()
        for number in range(1, 51):
            (ws / directory / f"第{number:03}章.md").write_text(f"原稿{number}", encoding="utf-8")
    before = {str(p.relative_to(ws)): p.read_bytes() for p in ws.rglob("*") if p.is_file()}
    env.active.append({"workspace_id": env.books[1].workspace_id, "status": "running"})
    assert import_card(env, asset["style_id"], 1).status_code == 409
    assert not (ws / "文风.md").exists()
    env.active[0]["workspace_id"] = env.books[0].workspace_id
    assert import_card(env, asset["style_id"], 1).status_code == 200
    assert all((ws / rel).read_bytes() == value for rel, value in before.items())


def _annotated(env):
    ws = Path(env.books[1].workspace_path)
    (ws / "文风.md").write_bytes(CARD.encode())
    (ws / "总纲.md").write_text("另一段原文", encoding="utf-8")
    for rel, quote in [("文风.md", "冷静短句。"), ("文风.md", "原样 保留。"), ("文风.md", "冷静短句。"), ("总纲.md", "另一段原文")]:
        create_annotation(ws, AnnotationCreateRequest(artifact_rel=rel, quote=quote, label="custom", custom_label="修改"))
    annotations = load_annotations(ws)
    annotations[1].dispatched = True
    annotations[2].status = "resolved"
    save_annotations(ws, annotations)
    return ws


def test_same_content_import_invalidates_only_old_pending_annotations(env):
    asset = seed(env)
    ws = _annotated(env)
    assert import_card(env, asset["style_id"], 1).status_code == 200
    assert [a.status for a in load_annotations(ws)] == ["invalid", "invalid", "resolved", "pending"]


@pytest.mark.parametrize("failing_replace", [1, 2])
def test_failed_import_rolls_back_card_and_annotations(env, monkeypatch, failing_replace):
    asset = seed(env, content="完全不同的新文风")
    ws = _annotated(env)
    before_card = (ws / "文风.md").read_bytes()
    before_annotations = (ws / ".writing/annotations.json").read_bytes()
    original_replace = style_assets.os.replace
    calls = 0

    def fail_once(source, target):
        nonlocal calls
        calls += 1
        if calls == failing_replace:
            raise OSError("simulated write failure")
        return original_replace(source, target)

    monkeypatch.setattr(style_assets.os, "replace", fail_once)
    assert import_card(env, asset["style_id"], 1).status_code == 500
    assert (ws / "文风.md").read_bytes() == before_card
    assert (ws / ".writing/annotations.json").read_bytes() == before_annotations


def test_assets_survive_new_database_connection(env):
    asset = seed(env)
    from app.platform.core.db import WritingStyleRepository
    reopened = Database(env.db.db_path, env.db.master_key)
    try:
        assert WritingStyleRepository(reopened).get(asset["style_id"], env.owner)["content"] == CARD
    finally:
        reopened.close()


def test_failed_database_writes_preserve_original_asset(env):
    asset = seed(env)
    (Path(env.books[0].workspace_path) / "文风.md").write_text("新版", encoding="utf-8")
    with env.db.transaction() as conn:
        conn.execute("CREATE TRIGGER fail_style_update AFTER UPDATE ON writing_style_assets BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        conn.execute("CREATE TRIGGER fail_style_delete AFTER DELETE ON writing_style_assets BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    assert save(env, target=asset["style_id"]).status_code == 500
    url = f"/api/writing-styles/{asset['style_id']}"
    assert env.client.patch(url, json={"name": "新名字"}).status_code == 500
    assert env.client.delete(url).status_code == 500
    assert env.client.get("/api/writing-styles").json() == [asset]


def test_watch_reverification_waits_for_import_and_cannot_restore_old_pending(env, monkeypatch):
    from app.routers import workspaces
    from app.domains.writing import annotations, run_gate

    ws = _annotated(env)
    requested = threading.Event()
    reverified = threading.Event()
    errors = []
    original_lock = run_gate.get_workspace_mutation_lock
    original_verify = annotations.reverify_annotations

    async def changes(*args, **kwargs):
        yield {(2, str(ws / "文风.md"))}

    def observe_lock(path):
        requested.set()
        return original_lock(path)

    def verify(path):
        result = original_verify(path)
        reverified.set()
        return result

    async def collect():
        generator = workspaces._workspace_watch_generator(env.owner, env.books[1].workspace_id, ws)
        try:
            await anext(generator)
        finally:
            await generator.aclose()

    def watch():
        try:
            asyncio.run(collect())
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(workspaces, "awatch", changes)
    monkeypatch.setattr(workspaces, "get_thread_store", lambda: SimpleNamespace())
    monkeypatch.setattr(workspaces, "get_workspace_mutation_lock", observe_lock)
    monkeypatch.setattr(annotations, "reverify_annotations", verify)
    watcher = threading.Thread(target=watch)
    try:
        with original_lock(ws):
            watcher.start()
            assert requested.wait(3), "watch must acquire the same workspace lock"
            assert not reverified.is_set()
            style_assets.replace_style_card(ws, CARD)
        watcher.join(3)
        assert not watcher.is_alive() and not errors
        assert reverified.is_set()
        assert [a.status for a in load_annotations(ws)] == ["invalid", "invalid", "resolved", "pending"]
    finally:
        watcher.join(3)


def test_old_dispatched_task_cannot_be_replayed_or_resumed_after_import(env, monkeypatch):
    from app.domains.writing.annotations import dispatch_annotations
    from app.domains.writing.agent import MetaAgentService
    from app.platform.trace.recorder import TraceRecorder
    from app.domains.writing.agent import _emit_contract_snapshot
    from app.schemas.screenplay import ScreenplayGenerateRequest

    ws = _annotated(env)
    message, _, _ = dispatch_annotations(ws)
    style_assets.assert_style_annotation_task_current(ws, message)
    thread = SimpleNamespace(thread_id="annotation-run", workspace_id=env.books[1].workspace_id,
                             workspace_path=str(ws), session_name="标注", user_id=env.owner, created_at="now", updated_at="now")
    recorder = TraceRecorder()
    monkeypatch.setattr("app.platform.trace.recorder._notify_evolution", lambda *args, **kwargs: None)
    trace = recorder.create_run(thread, "screenplay.generate.stream")
    _emit_contract_snapshot(recorder, trace.trace_id, ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt=message), thread, "user_generation")
    recorder.flush_sync(trace.trace_id)
    service = MetaAgentService.__new__(MetaAgentService)
    service.trace_recorder = TraceRecorder()
    original_annotations = load_annotations(ws)
    resolved = [annotation.model_copy(update={"status": "resolved"}) if annotation.artifact_rel == "文风.md" else annotation for annotation in original_annotations]
    save_annotations(ws, resolved)
    # 原运行已完成定点修改，允许正常确认；重新发送旧任务仍然拒绝。
    service._assert_style_annotation_task_current(thread, "确认", trace.trace_id)
    with pytest.raises(style_assets.WritingStyleError):
        style_assets.assert_style_annotation_task_current(ws, message)
    save_annotations(ws, original_annotations)
    style_assets.replace_style_card(ws, CARD)
    for text in (message, message.split("<!-- annotation-ids:", 1)[0]):
        with pytest.raises(style_assets.WritingStyleError, match="旧文风标注任务已失效"):
            style_assets.assert_style_annotation_task_current(ws, text)
    # 冷启动仍从磁盘契约读取旧任务，而非仅检查本轮“确认”。
    service.trace_recorder = TraceRecorder()
    with pytest.raises(style_assets.WritingStyleError, match="旧文风标注任务已失效"):
        service._assert_style_annotation_task_current(thread, "确认", trace.trace_id)
    fresh, _, _ = dispatch_annotations(ws)
    assert "｜文风.md｜" not in fresh
    style_assets.assert_style_annotation_task_current(ws, fresh)
    save_annotations(ws, [annotation for annotation in load_annotations(ws) if annotation.status != "invalid"])
    with pytest.raises(style_assets.WritingStyleError, match="旧文风标注任务已失效"):
        service._assert_style_annotation_task_current(thread, "确认", trace.trace_id)


def test_stream_reports_stale_style_task_without_starting_runtime(env, monkeypatch):
    from app.domains.writing.annotations import dispatch_annotations
    from app.domains.writing.agent import MetaAgentService
    from app.schemas.screenplay import ScreenplayGenerateRequest
    from app.domains.writing.conversation_log import ConversationLog

    ws = _annotated(env)
    message, _, _ = dispatch_annotations(ws)
    style_assets.replace_style_card(ws, CARD)
    service = MetaAgentService.__new__(MetaAgentService)
    service.settings = SimpleNamespace(writer_agent_mode="live")
    monkeypatch.setattr(service, "_generation_block_reason", lambda *args, **kwargs: None)
    async def checkpoint(*args):
        return object()
    monkeypatch.setattr(service, "_resolve_checkpointer", checkpoint)
    monkeypatch.setattr(service, "_sweep_stale_active_traces", lambda *args: None)
    monkeypatch.setattr(service, "_assert_style_annotation_task_current", lambda *args: style_assets.assert_style_annotation_task_current(ws, message))
    service.trace_recorder = SimpleNamespace(create_run=lambda *args, **kwargs: pytest.fail("invalid task must not create a run"))
    thread = SimpleNamespace(thread_id="queued-style", workspace_id=env.books[1].workspace_id, workspace_path=str(ws))
    payload = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt=message)
    async def collect():
        return [frame async for frame in service.generate_stream(payload, thread, owner_id=env.owner)]
    frames = asyncio.run(collect())
    assert any('"status": "writing_style_failed"' in frame and "旧文风标注任务已失效" in frame for frame in frames)
    assert ConversationLog(ws).read(thread.thread_id)[0].status == "cancelled"
