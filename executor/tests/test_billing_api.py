from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth import current_user, require_super_admin
from app.platform.core.db import Database, UserRepository, init_database
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.credits.ledger import BillingLedger, BillingError, init_billing
from app.platform.credits.api import router, admin_router
from app.platform.credits.runtime import init_billing_runs, current_billing, sse


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "meta.db", load_master_key(generate_master_key()))
    init_database(db)
    users = UserRepository(db)
    owner = users.create(username="owner", password="password123")["user_id"]
    other = users.create(username="other", password="password123")["user_id"]
    admin = users.create(username="admin", password="password123", is_super_admin=True)["user_id"]
    users.adjust_credits(owner, 100)
    ledger = BillingLedger(db)
    ledger.publish_price("model", "100", "200", "10", admin_id=admin)
    init_billing(ledger)
    init_billing_runs(ledger)
    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id=owner, is_super_admin=False)
    @app.exception_handler(BillingError)
    async def error_handler(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": {"code": exc.code, "message": str(exc)}})
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, ledger=ledger, owner=owner, other=other, admin=admin, app=app)
    db.close()


def test_public_rules_and_account_never_disclose_internal_coefficients(env):
    rules = env.client.get("/api/credits/rules")
    assert rules.status_code == 200
    assert "api_key" not in rules.text
    assert "updated_by" not in rules.text
    assert "credits_per_yuan" not in rules.text
    assert "prices" not in rules.text
    assert "input_per_million" not in rules.text
    account = env.client.get("/api/me/billing").json()
    assert account["balance"] == 100
    assert account["contact_qq"] == "1134559830"
    assert "credits_per_yuan" not in account
    op = env.ledger.reserve(user_id=env.owner, thread_id="thread", request_id="private",
                            budget="1", model="model", fingerprint="private")
    env.ledger.record_call(op["operation_id"], "call", "model", input_tokens=1000, output_tokens=1000)
    detail = env.client.get(f"/api/me/billing/operations/{op['operation_id']}")
    assert "prices" not in detail.json()
    assert "platform_cost" not in detail.text
    assert "input_per_million" not in detail.text


def test_user_cannot_read_another_users_operation_or_edit_prices(env):
    env.ledger.adjust(env.other, "2", "测试账户", admin_id=env.admin, request_id="seed")
    op = env.ledger.reserve(user_id=env.other, thread_id="thread", request_id="other",
                            budget="1", model="model", fingerprint="other")
    env.app.dependency_overrides[require_super_admin] = lambda: SimpleNamespace(user_id=env.owner)
    response = env.client.put("/api/admin/billing/prices/model", json={
        "input_per_million": "1", "output_per_million": "2", "cached_per_million": "0.1"})
    assert response.status_code == 403
    response = env.client.get(f"/api/me/billing/operations/{op['operation_id']}")
    assert response.status_code == 404


def test_admin_prices_and_enablement_are_validated(env):
    env.app.dependency_overrides[require_super_admin] = lambda: SimpleNamespace(user_id=env.admin)
    for value in ("-1", "NaN", "Infinity"):
        response = env.client.put("/api/admin/billing/prices/model", json={
            "input_per_million": value, "output_per_million": "2", "cached_per_million": "0.1"})
        assert response.status_code in (400, 422)
    response = env.client.put("/api/admin/billing/config", json={
        "enabled": True, "default_budgets": {"default": "20"}})
    assert response.status_code == 200
    assert env.client.get("/api/me/billing").json()["default_budgets"]["default"] == 20


def test_actual_generation_route_requires_budget_and_retransmission_is_read_only(env, monkeypatch):
    from app.routers import screenplay
    from app.platform.credits import api
    env.ledger.configure(enabled=True, default_budgets={"default": 20}, admin_id=env.admin)
    thread = SimpleNamespace(thread_id="thread", workspace_id="book", workspace_path="/tmp/billing-book")
    store = SimpleNamespace(get_thread=lambda owner, key: thread if owner == env.owner and key == "thread" else None,
                            artifacts=SimpleNamespace(touch_thread=lambda *args: None))
    calls = []
    async def generate(payload, thread, **kwargs):
        calls.append(1)
        yield sse("status", {"status": "started", "trace_id": "trace"})
        current_billing().record(SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 1000}), "call", "model")
        yield sse("interrupt", {"question": "请确认", "kind": "review"})
    monkeypatch.setattr(screenplay, "get_thread_store", lambda: store)
    monkeypatch.setattr(screenplay, "get_agent_service", lambda: SimpleNamespace(generate_stream=generate))
    monkeypatch.setattr(api, "active_model", lambda: "model")
    env.app.include_router(screenplay.router, prefix="/api")
    old = env.client.post("/api/screenplay/generate/stream", json={"thread_id": "thread", "prompt": "test"})
    assert old.status_code == 426
    assert calls == []
    payload = {"thread_id": "thread", "prompt": "test", "message_id": "stable", "billing_protocol": 2, "billing_budget": 20}
    first = env.client.post("/api/screenplay/generate/stream", json=payload)
    repeated = env.client.post("/api/screenplay/generate/stream", json=payload)
    assert first.status_code == repeated.status_code == 200
    assert "event: interrupt" in first.text
    assert calls == [1]
    assert env.ledger.balance(env.owner)["balance"] == 99.7


@pytest.mark.asyncio
async def test_stop_checks_thread_ownership_before_cancelling(env, monkeypatch):
    from app.routers import screenplay
    from fastapi import HTTPException
    changes = []
    thread = SimpleNamespace(thread_id="thread", workspace_id="own-book", workspace_path="/tmp/own")
    recorder = SimpleNamespace(find_run_by_trace_id=lambda *args, **kwargs: SimpleNamespace(workspace_id="other-book", thread_id="other-thread"),
                               request_user_stop=lambda key: changes.append(key), cancel_run_task=lambda key: changes.append(key))
    monkeypatch.setattr(screenplay, "get_thread_store", lambda: SimpleNamespace(get_thread=lambda *args: thread))
    monkeypatch.setattr(screenplay, "get_trace_recorder", lambda: recorder)
    with pytest.raises(HTTPException) as result:
        await screenplay.stop_screenplay(screenplay.StopRequest(thread_id="thread", trace_id="foreign"),
                                          SimpleNamespace(user_id=env.owner))
    assert result.value.status_code == 404
    assert changes == []


def test_disabling_billing_does_not_reexecute_an_existing_paid_request(env, monkeypatch):
    from app.platform.credits import api
    from app.schemas.screenplay import ScreenplayGenerateRequest
    import asyncio
    env.ledger.configure(enabled=True, default_budgets={"default": 20}, admin_id=env.admin)
    monkeypatch.setattr(api, "active_model", lambda: "model")
    payload = ScreenplayGenerateRequest(thread_id="thread", prompt="test", message_id="original",
                                         billing_protocol=2, billing_budget=20)
    first = asyncio.run(api.prepare_operation(payload, env.owner, "thread"))
    env.ledger.settle(first["operation_id"], "completed")
    env.ledger.configure(enabled=False, default_budgets={"default": 20}, admin_id=env.admin)
    repeated = asyncio.run(api.prepare_operation(payload, env.owner, "thread"))
    assert repeated is not None
    assert repeated["operation_id"] == first["operation_id"]


@pytest.mark.asyncio
async def test_stop_during_preparation_without_a_trace_is_not_lost(env, monkeypatch):
    import asyncio
    from app.routers import screenplay
    from app.platform.credits.runtime import get_billing_runs
    thread = SimpleNamespace(thread_id="thread", workspace_id="own-book", workspace_path="/tmp/own")
    monkeypatch.setattr(screenplay, "get_thread_store", lambda: SimpleNamespace(get_thread=lambda *args: thread))
    monkeypatch.setattr(screenplay, "get_trace_recorder", lambda: SimpleNamespace(request_user_stop=lambda *args: None))
    monkeypatch.setattr(screenplay, "_clear_conversation_queue", lambda thread: 0)
    op = env.ledger.reserve(user_id=env.owner, thread_id="thread", request_id="preparation",
                            budget="20", model="model", fingerprint="preparation")
    ready = asyncio.Event()
    async def source():
        ready.set()
        await asyncio.Event().wait()
        yield ""
    run = get_billing_runs().submit(op, source)
    await ready.wait()
    result = await screenplay.stop_screenplay(screenplay.StopRequest(thread_id="thread", trace_id=""),
                                               SimpleNamespace(user_id=env.owner))
    await run.task
    assert result["task_cancelled"] is True
    assert env.ledger.operation(op["operation_id"], env.owner)["status"] == "stopped"


@pytest.mark.asyncio
async def test_stop_while_model_configuration_is_loading_never_reserves_or_calls(env, monkeypatch):
    import asyncio
    import threading
    from app.platform.credits import api
    from app.platform.credits.runtime import get_billing_runs, OperationStopped
    from app.schemas.screenplay import ScreenplayGenerateRequest
    entered, release = threading.Event(), threading.Event()
    def slow_model():
        entered.set()
        release.wait(5)
        return "model"
    monkeypatch.setattr(api, "active_model", slow_model)
    env.ledger.configure(enabled=True, default_budgets={"default": 20}, admin_id=env.admin)
    payload = ScreenplayGenerateRequest(thread_id="thread", prompt="test", message_id="pre-admit",
                                         billing_protocol=2, billing_budget=20)
    task = asyncio.create_task(api.prepare_operation(payload, env.owner, "thread"))
    await asyncio.to_thread(entered.wait, 5)
    assert get_billing_runs().request_stop(env.owner, "thread", None) == 1
    release.set()
    with pytest.raises(OperationStopped):
        await task
    assert env.ledger.operations(env.owner) == []
    assert env.ledger.balance(env.owner)["reserved"] == 0
