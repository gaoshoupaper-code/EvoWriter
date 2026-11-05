"""Normal autonomous writing keeps the existing v2 billing boundary."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.auth import current_user
from app.platform.core.db import Database, UserRepository, init_database
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.credits.ledger import BillingError, BillingLedger, init_billing
from app.platform.credits.runtime import current_billing, init_billing_runs
from app.platform.trace.recorder import TraceRecorder
from app.routers import target_tasks as api
from app.schemas.screenplay import ThreadSummary
from app.domains.writing import target_tasks as tasks


@pytest_asyncio.fixture
async def target_billing(tmp_path, monkeypatch):
    from app.domains.writing import agent, pipeline
    from app.platform.agent import loader
    from app.platform.agent.middleware.trace_middleware import TraceMiddleware
    from app.platform.core import db as core_db
    from app.platform.core import settings as settings_module
    from app.platform.credits import api as credits_api
    from app.platform.memory import store as memory
    from contracts.platform import LlmConfigSnapshot
    database = Database(tmp_path / "billing.db", load_master_key(generate_master_key()))
    init_database(database)
    users = UserRepository(database)
    owner = users.create(username="target-owner", password="password123")["user_id"]
    admin = users.create(username="target-admin", password="password123", is_super_admin=True)["user_id"]
    users.adjust_credits(owner, 100)
    ledger = BillingLedger(database)
    ledger.publish_price("model", "100", "200", "10", admin_id=admin)
    ledger.configure(enabled=True, default_budgets={"default": 20}, admin_id=admin)
    init_billing(ledger)
    runs = init_billing_runs(ledger)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = tmp_path / "production"
    package.mkdir()
    (package / ".digest").write_text("2" * 64, encoding="utf-8")
    thread = ThreadSummary(thread_id="target-thread", workspace_id="book", session_name="target",
                           workspace_path=str(workspace), user_id=owner, created_at="now", updated_at="now")
    store = SimpleNamespace(get_workspace=lambda user, key: SimpleNamespace(workspace_path=str(workspace))
                            if user == owner and key == "book" else None,
                            get_thread=lambda user, key: thread if user == owner and key == thread.thread_id else None,
                            create_thread=lambda *args: thread)
    manager = tasks.TargetTaskManager(tmp_path / "tasks", recorder=TraceRecorder())
    env = SimpleNamespace(ledger=ledger, runs=runs, manager=manager, owner=owner, admin=admin, store=store, thread=thread,
                          workspace=workspace, calls=[], contexts=[], ready=asyncio.Event(), mode="complete")
    monkeypatch.setattr(api, "get_thread_store", lambda: store)
    monkeypatch.setattr(api, "get_trace_recorder", lambda: SimpleNamespace(list_active_runs=lambda: []))
    monkeypatch.setattr(api, "get_manager", lambda: manager)
    monkeypatch.setattr(pipeline, "classify_workspace", lambda root: "five")
    monkeypatch.setattr(core_db, "WritingSkillRepository", lambda database: SimpleNamespace(
        get_global_state=lambda owner: {"pending_stages": []}, list_enabled_for_owner=lambda owner: []))
    monkeypatch.setattr(loader, "load_current_package", lambda: object())
    monkeypatch.setattr(loader, "production_commit", lambda: "a" * 40)
    monkeypatch.setattr(loader, "production_checkout", lambda: package)
    monkeypatch.setattr(loader, "load_package", lambda *args: object())
    monkeypatch.setattr(agent, "build_llm_snapshot", lambda: LlmConfigSnapshot(model="model", base_url="https://model.invalid"))
    monkeypatch.setattr(settings_module, "get_settings", lambda: SimpleNamespace(evolution_notify_url=""))
    monkeypatch.setattr(tasks, "build_frozen_model", lambda *args: object())
    monkeypatch.setattr(agent, "MetaAgentService", lambda settings, workspace, recorder, *args:
                        SimpleNamespace(trace_recorder=recorder))
    monkeypatch.setattr(memory, "get_memory_store_pool", lambda: object())
    monkeypatch.setattr(credits_api, "active_model", lambda: "model")
    monkeypatch.setattr(manager, "ensure_package", lambda task_id: package)
    monkeypatch.setattr(manager, "_register_traces", lambda task_id: None)
    monkeypatch.setattr(tasks.mp, "get_context", lambda *args:
                        (_ for _ in ()).throw(AssertionError("normal billing cannot leave the owning coroutine")))

    async def drive(request, root, service, current_thread, execution, journal):
        env.contexts.append(current_billing())
        recorder = service.trace_recorder
        trace_id = recorder.create_run(current_thread, "target-billing").trace_id
        journal.update(request.task_id, trace_ids=[trace_id])
        middleware = TraceMiddleware(recorder, trace_id, "author")
        middleware._record_model_start = lambda *args: None
        middleware._record_model_end = lambda *args: None
        middleware._record_model_error = lambda *args: None
        async def handler(_request):
            env.calls.append("model")
            return SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 1000})
        model_request = SimpleNamespace(model=SimpleNamespace(model_name="model"))
        await middleware.awrap_model_call(model_request, handler)
        (root / "主题.md").write_text("> 状态：定稿\n" + "\n".join(
            f"## {heading}\n具体故事内容" for heading in ["一句话主题", "核心爽点", "核心矛盾", "核心爽点剧情"]), encoding="utf-8")
        (root / "总纲.md").write_text("> 状态：定稿\n## 核心纲\n| 卷 | 内容 |\n|---|---|\n| 1 | 具体故事内容 |", encoding="utf-8")
        (root / "卷纲").mkdir(exist_ok=True)
        (root / "卷纲/第01卷.md").write_text("> 状态：定稿\n本卷章号范围：1-1\n具体卷纲内容", encoding="utf-8")
        env.ready.set()
        if env.mode == "budget":
            await middleware.awrap_model_call(model_request, handler)
        elif env.mode in {"stop", "swallowed-stop"}:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if env.mode == "swallowed-stop":
                    raise ValueError("generation returned cancelled without a final frame")
                raise
        elif env.mode == "failure":
            raise RuntimeError("generation failed")
        recorder.append_event(trace_id, {"type": "tool_start", "status": "running", "source": "middleware", "tool_name": "task",
            "tool_call_id": "quality", "tool_args": {"subagent_type": "volume_qc", "description": "质检第1卷"}})
        recorder.append_event(trace_id, {"type": "tool_end", "status": "completed", "source": "middleware", "tool_name": "task",
            "tool_call_id": "quality", "tool_output": {"content": "质检通过。"}})
        recorder.complete_run(current_thread, trace_id)
        return {"complete": True, "missing": []}
    monkeypatch.setattr(tasks, "drive_task", drive)
    app = FastAPI()
    env.app = app
    app.include_router(api.router)
    app.include_router(api.internal_router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id=owner)
    @app.exception_handler(BillingError)
    async def billing_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": {"code": exc.code, "message": str(exc)}})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        env.client = client
        yield env
    await manager.close()
    await runs.aclose()
    database.close()


def payload(**changes):
    return {"target": {"task_type": "volume_outlines", "volume_ids": [1]}, "thread_id": "target-thread",
            "demand_md": "完整故事需求；整本一卷。", "confirmation_rules": "自行决定并确认通过",
            "message_id": "same-operation", "billing_protocol": 2, "billing_budget": 20, **changes}


async def finish(env, task_id):
    monitor = env.manager.monitors.get(task_id)
    if monitor is not None:
        await asyncio.wait_for(asyncio.shield(monitor), timeout=10)
    return env.manager.result(task_id)


@pytest.mark.asyncio
async def test_enabled_target_rejects_old_protocol_missing_budget_and_insufficient_balance(target_billing):
    env = target_billing
    old = payload()
    for key in ("message_id", "billing_protocol", "billing_budget"):
        old.pop(key)
    response = await env.client.post("/api/workspaces/book/target-tasks", json=old)
    assert response.status_code == 426
    missing = payload()
    missing.pop("billing_budget")
    response = await env.client.post("/api/workspaces/book/target-tasks", json=missing)
    assert response.status_code == 426
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload(billing_budget=101))
    assert response.status_code == 402
    assert env.calls == [] and env.ledger.operations(env.owner) == []


@pytest.mark.asyncio
async def test_normal_target_uses_real_billing_context_and_retransmission_is_one_task(target_billing):
    env = target_billing
    first = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert first.status_code == 202, first.text
    task_id = first.json()["task_id"]
    result = await finish(env, task_id)
    assert result.status == "completed", result.error
    repeated = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert repeated.status_code == 202 and repeated.json()["task_id"] == task_id
    assert env.calls == ["model"] and env.contexts[0] is not None
    operations = env.ledger.operations(env.owner)
    assert len(operations) == 1 and operations[0]["charged"] == .3
    assert env.ledger.balance(env.owner)["balance"] == 99.7
    changed = await env.client.post("/api/workspaces/book/target-tasks", json=payload(billing_budget=21))
    assert changed.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_same_message_never_refunds_the_active_target(target_billing, monkeypatch):
    import threading
    from app.platform.credits import api as credits_api
    env = target_billing
    env.mode = "stop"
    rendezvous = threading.Barrier(2)
    def active_model():
        rendezvous.wait(timeout=5)
        return "model"
    monkeypatch.setattr(credits_api, "active_model", active_model)
    first, second = await asyncio.gather(*[
        env.client.post("/api/workspaces/book/target-tasks", json=payload()) for _ in range(2)])
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    await asyncio.wait_for(env.ready.wait(), timeout=10)
    assert len(env.ledger.operations(env.owner)) == 1
    assert env.ledger.operations(env.owner)[0]["status"] == "running"
    assert env.ledger.balance(env.owner)["reserved"] == 20
    await env.manager.stop(first.json()["task_id"])
    assert env.calls == ["model"]


@pytest.mark.asyncio
async def test_target_budget_exhaustion_blocks_the_next_model_call_and_settles_once(target_billing):
    env = target_billing
    env.mode = "budget"
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload(billing_budget=.3))
    assert response.status_code == 202, response.text
    result = await finish(env, response.json()["task_id"])
    assert result.status == "stopped" and not result.completed
    operation = env.ledger.operations(env.owner)[0]
    assert operation["status"] == "budget_exhausted" and operation["charged"] == .3
    assert env.calls == ["model"]
    assert env.ledger.balance(env.owner)["reserved"] == 0


@pytest.mark.asyncio
async def test_target_stop_charges_used_tokens_and_refunds_the_unused_reservation(target_billing):
    env = target_billing
    env.mode = "stop"
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert response.status_code == 202, response.text
    await asyncio.wait_for(env.ready.wait(), timeout=10)
    task_id = response.json()["task_id"]
    stopped = await env.client.post(f"/api/workspaces/book/target-tasks/{task_id}/stop", json={})
    assert stopped.status_code == 200
    result = await finish(env, task_id)
    assert result.status == "stopped"
    operation = env.ledger.operations(env.owner)[0]
    assert operation["status"] == "stopped" and operation["charged"] == .3
    assert env.ledger.balance(env.owner)["reserved"] == 0
    assert env.calls == ["model"]
    assert (env.workspace / "主题.md").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stop", "swallowed-stop"])
async def test_existing_screenplay_stop_settles_target_usage_without_a_target_stop_file(target_billing, monkeypatch, mode):
    from app.routers import screenplay
    env = target_billing
    env.mode = mode
    env.app.include_router(screenplay.router, prefix="/api")
    monkeypatch.setattr(screenplay, "get_thread_store", lambda: env.store)
    monkeypatch.setattr(screenplay, "get_trace_recorder", lambda: env.manager.recorder)
    monkeypatch.setattr(screenplay, "_clear_conversation_queue", lambda thread: 0)
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert response.status_code == 202, response.text
    await asyncio.wait_for(env.ready.wait(), timeout=10)
    stopped = await env.client.post("/api/screenplay/stop", json={"thread_id": env.thread.thread_id, "trace_id": ""})
    assert stopped.status_code == 200 and stopped.json()["task_cancelled"]
    result = await finish(env, response.json()["task_id"])
    assert result.status == "stopped"
    assert not (env.manager.store.directory(result.task_id) / "stop").exists()
    operation = env.ledger.operations(env.owner)[0]
    assert operation["status"] == "stopped" and operation["charged"] == .3
    assert env.ledger.balance(env.owner)["reserved"] == 0
    assert env.calls == ["model"]


@pytest.mark.asyncio
async def test_server_shutdown_refunds_instead_of_classifying_as_a_user_stop(target_billing):
    env = target_billing
    env.mode = "stop"
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert response.status_code == 202, response.text
    await asyncio.wait_for(env.ready.wait(), timeout=10)
    await env.runs.aclose()
    result = await finish(env, response.json()["task_id"])
    assert result.status == "interrupted"
    operation = env.ledger.operations(env.owner)[0]
    assert operation["status"] == "failed" and operation["charged"] == 0
    assert env.ledger.balance(env.owner)["balance"] == 100


@pytest.mark.asyncio
async def test_rejected_admission_replays_a_clear_conflict_without_another_reservation(target_billing, monkeypatch):
    env = target_billing
    monkeypatch.setattr(env.manager, "submit", lambda *args, **kwargs:
                        (_ for _ in ()).throw(ValueError("workspace already has a target")))
    for _ in range(2):
        response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
        assert response.status_code == 409
    assert len(env.ledger.operations(env.owner)) == 1
    assert env.ledger.operations(env.owner)[0]["status"] == "failed"
    assert env.ledger.balance(env.owner)["reserved"] == 0
    assert env.calls == []


@pytest.mark.asyncio
async def test_target_failure_refunds_and_disabled_billing_accepts_the_old_body(target_billing):
    env = target_billing
    env.mode = "failure"
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert response.status_code == 202, response.text
    result = await finish(env, response.json()["task_id"])
    assert result.status == "failed"
    assert env.ledger.operations(env.owner)[0]["status"] == "failed"
    assert env.ledger.balance(env.owner)["balance"] == 100
    env.ledger.configure(enabled=False, default_budgets={"default": 20}, admin_id=env.admin)
    env.mode = "complete"
    old = payload()
    for key in ("message_id", "billing_protocol", "billing_budget"):
        old.pop(key)
    response = await env.client.post("/api/workspaces/book/target-tasks", json=old)
    assert response.status_code == 202, response.text
    result = await finish(env, response.json()["task_id"])
    assert result.status == "completed", result.error
    assert env.contexts[-1] is None and len(env.ledger.operations(env.owner)) == 1
    first = await env.client.post("/api/workspaces/book/target-tasks", json=payload(message_id="disabled-operation"))
    assert first.status_code == 202, first.text
    await finish(env, first.json()["task_id"])
    before = list(env.calls)
    repeated = await env.client.post("/api/workspaces/book/target-tasks", json=payload(message_id="disabled-operation"))
    assert repeated.status_code == 202 and repeated.json()["task_id"] == first.json()["task_id"]
    assert env.calls == before
    assert len(env.ledger.operations(env.owner)) == 1


@pytest.mark.asyncio
async def test_billing_recovery_keeps_registered_target_operation_reserved(target_billing):
    env = target_billing
    env.mode = "stop"
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert response.status_code == 202, response.text
    await asyncio.wait_for(env.ready.wait(), timeout=10)
    env.runs.start_recovery()
    operation = env.ledger.operations(env.owner)[0]
    assert operation["status"] == "running" and env.ledger.balance(env.owner)["reserved"] == 20
    task_id = response.json()["task_id"]
    await env.manager.stop(task_id)
    await finish(env, task_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_kind", ["receipt", "intent"])
@pytest.mark.parametrize("replay_before_source", [False, True])
async def test_recovery_never_runs_a_target_with_prior_settlement(target_billing, pending_kind, replay_before_source):
    from unittest.mock import patch
    env = target_billing
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert response.status_code == 202, response.text
    task_id = response.json()["task_id"]
    operation = env.ledger.find_request(env.owner, "same-operation")
    operation_id = operation["operation_id"]
    assert env.calls == []
    env.ledger.record_call(operation_id, "saved-before-restart", "model", input_tokens=1000, output_tokens=1000)
    receipt = env.ledger.receipts / f"{operation_id}.json"
    if pending_kind == "receipt":
        with patch.object(env.ledger.db, "transaction", side_effect=OSError("database temporarily unavailable")):
            env.ledger.finish(operation_id, "failed")
    else:
        with patch.object(env.ledger, "settle", side_effect=OSError("settlement temporarily unavailable")):
            env.ledger.finish(operation_id, "failed")
        receipt.unlink()
    if replay_before_source:
        env.ledger.recover({operation_id})
    result = await finish(env, task_id)
    assert env.calls == []
    assert result.task_id == task_id and result.status == "stopped" and not result.completed
    assert env.manager.store.trusted(task_id)["metadata"]["owner_id"] == env.owner
    if receipt.exists():
        assert json.loads(receipt.read_text(encoding="utf-8"))["outcome"] == "failed"
    env.ledger.recover(set())
    operation = env.ledger.operation(operation_id, env.owner)
    assert operation["status"] == "failed" and operation["charged"] == 0
    assert env.ledger.balance(env.owner)["balance"] == 100


@pytest.mark.asyncio
async def test_recovery_settlement_during_package_preparation_blocks_the_first_model_call(target_billing, monkeypatch):
    import threading
    env = target_billing
    entered, release = threading.Event(), threading.Event()
    def preparation(task_id):
        entered.set()
        release.wait(timeout=10)
    monkeypatch.setattr(env.manager, "ensure_package", preparation)
    response = await env.client.post("/api/workspaces/book/target-tasks", json=payload())
    assert response.status_code == 202, response.text
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        operation_id = env.ledger.find_request(env.owner, "same-operation")["operation_id"]
        env.ledger.finish(operation_id, "failed")
    finally:
        release.set()
    result = await finish(env, response.json()["task_id"])
    assert result.status == "stopped" and env.calls == []
    assert env.ledger.operation(operation_id, env.owner)["status"] == "failed"


@pytest.mark.asyncio
async def test_internal_target_submission_does_not_reserve_user_credits(target_billing, monkeypatch):
    env = target_billing
    monkeypatch.setattr(env.manager, "start", lambda task_id: None)
    response = await env.client.post("/internal/target-tasks", json={
        "task_id": "internal-sample", "attempt_id": "first", "source_commit": "a" * 40,
        "target": {"task_type": "volume_outlines", "volume_ids": [1]},
        "input": {"demand_md": "完整内部测试需求", "confirmation_rules": "自行确认"}})
    assert response.status_code == 202
    assert env.ledger.operations(env.owner) == []
    assert env.ledger.balance(env.owner)["balance"] == 100
