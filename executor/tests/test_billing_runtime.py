import asyncio
from types import SimpleNamespace

import pytest

from app.platform.core.db import Database, UserRepository
from app.platform.core.security import generate_master_key, load_master_key
from app.platform.credits.ledger import BillingLedger
from app.platform.credits.runtime import (
    BillingContext, BillingRuns, BudgetExhausted, current_billing,
    response_usage,
)


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "meta.db", load_master_key(generate_master_key()))
    users = UserRepository(db)
    owner = users.create(username="owner", password="password123")["user_id"]
    admin = users.create(username="admin", password="password123", is_super_admin=True)["user_id"]
    users.adjust_credits(owner, 100)
    ledger = BillingLedger(db)
    ledger.publish_price("model", "100", "200", "10", admin_id=admin)
    yield ledger, owner
    db.close()


def operation(env, key="request", budget="50"):
    ledger, owner = env
    return ledger.reserve(user_id=owner, thread_id="thread", request_id=key,
                          budget=budget, model="model", fingerprint=key)


def test_usage_handles_zero_missing_cache_and_reasoning_without_double_charge():
    response = SimpleNamespace(result=[SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 0,
                        "input_token_details": {"cache_read": 30},
                        "output_token_details": {"reasoning": 20}}, response_metadata={})])
    assert response_usage(response) == {"input_tokens": 100, "output_tokens": 0, "cached_tokens": 30}
    response.result[0].usage_metadata.pop("output_tokens")
    assert response_usage(response)["output_tokens"] is None


def test_raw_cache_evidence_is_not_lost_to_normalized_usage():
    response = SimpleNamespace(result=[SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "input_token_details": {}},
        response_metadata={"token_usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                            "prompt_cache_hit_tokens": 80}})])
    assert response_usage(response)["cached_tokens"] == 80


def test_model_adapter_fault_retry_metadata_is_not_billed(env):
    ledger, owner = env
    op = operation(env)
    response = SimpleNamespace(result=[SimpleNamespace(
        usage_metadata={"input_tokens": 1000, "output_tokens": 1000},
        response_metadata={"billing_fault_retry": True})])
    with BillingContext(ledger, op["operation_id"]) as context:
        context.record(response, "adapter-retry", "model")
    assert ledger.settle(op["operation_id"], "completed")["charged"] == 0


def test_context_budget_is_operation_local_and_retry_is_free(env):
    ledger, owner = env
    op = operation(env, budget="0.3")
    context = BillingContext(ledger, op["operation_id"])
    response = SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 1000})
    assert current_billing() is None
    with context:
        assert current_billing() is context
        context.before_call("model")
        context.record(response, "retry", "model", billable=False)
        context.before_call("model")
        context.record(response, "normal", "model")
        with pytest.raises(BudgetExhausted):
            context.before_call("model")
    assert current_billing() is None
    assert ledger.settle(op["operation_id"], "budget_exhausted")["charged"] == 0.3


@pytest.mark.asyncio
async def test_disconnect_does_not_cancel_and_retransmission_does_not_start_twice(env):
    ledger, owner = env
    runs = BillingRuns(ledger)
    op = operation(env)
    release = asyncio.Event()
    calls = []
    async def generate():
        calls.append("start")
        yield 'event: status\ndata: {"status":"started","trace_id":"trace"}\n\n'
        await release.wait()
        yield 'event: final\ndata: {"content":"saved"}\n\n'
    run = runs.submit(op, generate)
    stream = runs.stream(run)
    first = await anext(stream)
    assert "started" in first or "billing" in first
    await stream.aclose()
    assert not run.task.done()
    assert runs.submit(op, generate) is run
    release.set()
    await run.task
    assert calls == ["start"]
    assert ledger.operation(op["operation_id"], owner)["status"] == "completed"
    frames = [frame async for frame in runs.stream(run)]
    assert any('"saved"' in frame for frame in frames)
    await runs.aclose()


@pytest.mark.asyncio
async def test_failure_refunds_and_explicit_stop_charges_known_usage(env):
    ledger, owner = env
    runs = BillingRuns(ledger)
    response = SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 1000})
    async def failure():
        current_billing().record(response, "call", "model")
        raise RuntimeError("provider failed")
        yield ""
    failed = runs.submit(operation(env, "failure"), failure)
    await failed.task
    assert ledger.balance(owner)["balance"] == 100
    started = asyncio.Event()
    async def waiting():
        current_billing().record(response, "call", "model")
        started.set()
        await asyncio.Event().wait()
        yield ""
    stopped = runs.submit(operation(env, "stop"), waiting)
    await started.wait()
    runs.request_stop(owner, "thread", None)
    await stopped.task
    assert ledger.balance(owner)["balance"] == 99.7
    assert ledger.operation(stopped.operation["operation_id"], owner)["status"] == "stopped"
    await runs.aclose()


@pytest.mark.asyncio
async def test_free_context_never_creates_user_financial_side_effects(env):
    ledger, owner = env
    before = ledger.balance(owner)
    assert current_billing() is None
    assert ledger.operations(owner) == []
    await asyncio.sleep(0)
    assert ledger.balance(owner) == before


@pytest.mark.asyncio
async def test_real_trace_middleware_records_usage_and_excludes_transport_retry(env):
    from app.platform.agent.middleware.trace_middleware import TraceMiddleware
    ledger, owner = env
    op = operation(env)
    attempts = []
    async def retry(invoke, on_failed, on_backoff):
        try:
            return await invoke()
        except OSError:
            return await invoke()
    middleware = TraceMiddleware(SimpleNamespace(), "trace", "author", retry_runner=retry)
    middleware._record_model_start = lambda *args: None
    middleware._record_model_end = lambda *args: None
    request = SimpleNamespace(model=SimpleNamespace(model_name="model"))
    response = SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 1000})
    async def handler(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("transport")
        return response
    with BillingContext(ledger, op["operation_id"]):
        await middleware.awrap_model_call(request, handler)
    detail = ledger.operation(op["operation_id"], owner)
    assert len(detail["calls"]) == 1
    assert detail["calls"][0]["billable"] == 0


@pytest.mark.asyncio
async def test_slow_subscriber_receives_terminal_frames(env):
    ledger, _ = env
    runs = BillingRuns(ledger)
    release = asyncio.Event()
    async def source():
        yield 'event: status\ndata: {"status":"started"}\n\n'
        await release.wait()
        yield 'event: final\ndata: {"content":"done"}\n\n'
    run = runs.submit(operation(env), source)
    stream = runs.stream(run)
    await anext(stream)
    release.set()
    await run.task
    tail = [frame async for frame in stream]
    assert any("event: final" in frame for frame in tail)
    assert any("event: billing" in frame for frame in tail)


@pytest.mark.asyncio
async def test_restart_replays_original_interrupt_without_executing(env):
    ledger, owner = env
    op = operation(env)
    ledger.save_result(op["operation_id"], {"question": "review"}, event_type="interrupt")
    ledger.finish(op["operation_id"], "completed")
    runs = BillingRuns(ledger)
    def forbidden():
        raise AssertionError("must never run twice")
    run = runs.submit(ledger.operation(op["operation_id"], owner), forbidden)
    frames = [frame async for frame in runs.stream(run)]
    assert any("event: interrupt" in frame for frame in frames)
    assert not any("event: final" in frame for frame in frames)


@pytest.mark.asyncio
async def test_json_budget_exhaustion_never_claims_full_refund(env, monkeypatch):
    from app.platform.credits.api import run_json_operation
    from app.platform.credits import api
    from app.platform.credits.ledger import BillingError
    ledger, _ = env
    runs = BillingRuns(ledger)
    monkeypatch.setattr(api, "get_billing_runs", lambda: runs)
    monkeypatch.setattr(api, "get_billing", lambda: ledger)
    async def action():
        context = current_billing()
        context.record(SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 1000}), "call", "model")
        context.before_call("model")
        return {}
    with pytest.raises(BillingError) as caught:
        await run_json_operation(operation(env, budget="0.3"), action)
    assert caught.value.code == "budget_exhausted"
    assert "退还" not in str(caught.value)


@pytest.mark.asyncio
async def test_stop_prevents_a_worker_thread_from_starting_another_model_call(env):
    import threading
    ledger, owner = env
    runs = BillingRuns(ledger)
    release = threading.Event()
    first = threading.Event()
    escaped = []
    def worker():
        context = current_billing()
        context.record(SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 1000}), "first", "model")
        first.set()
        release.wait(5)
        try:
            context.before_call("model")
        except Exception:
            return
        escaped.append("second model call")
    async def source():
        await asyncio.to_thread(worker)
        yield 'event: final\ndata: {}\n\n'
    run = runs.submit(operation(env), source)
    await asyncio.to_thread(first.wait, 5)
    runs.request_stop(owner, "thread", None)
    await run.task
    release.set()
    await asyncio.sleep(.1)
    assert escaped == []


@pytest.mark.asyncio
async def test_shutdown_also_stops_cooperative_worker_threads(env):
    import threading
    ledger, _ = env
    runs = BillingRuns(ledger)
    release, first = threading.Event(), threading.Event()
    escaped = []
    def worker():
        context = current_billing()
        first.set()
        release.wait(5)
        try:
            context.before_call("model")
        except Exception:
            return
        escaped.append("new call after shutdown")
    async def source():
        await asyncio.to_thread(worker)
        yield 'event: final\ndata: {}\n\n'
    run = runs.submit(operation(env), source)
    await asyncio.to_thread(first.wait, 5)
    await runs.aclose()
    release.set()
    await asyncio.sleep(.1)
    assert escaped == []
