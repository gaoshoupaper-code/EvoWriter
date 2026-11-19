import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.domains.writing.creative_worker_model import forward_model, make_worker_model
from app.domains.writing.deepseek_thinking import ReasoningStreamChatModel
from app.platform.credits.runtime import BillingContext, BudgetExhausted, OperationStopped, response_usage


def run_bridge(stream=False, status=200, billing=None):
    calls, sent, recorded = [], [], []
    usage = {"prompt_tokens": 21, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 8}}
    answer = {"id": "completion", "model": "bound", "object": "chat.completion",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "saved"}, "finish_reason": "stop"}],
              "usage": usage}

    async def upstream(request):
        calls.append((request.url, request.headers.get("authorization"), json.loads(request.content)))
        if status != 200:
            return httpx.Response(status, json={"detail": "private diagnostic"})
        if not stream:
            return httpx.Response(200, json=answer)
        chunks = [{"id": "completion", "model": "bound", "object": "chat.completion.chunk",
                   "choices": [{"index": 0, "delta": {"role": "assistant", "content": "saved"}, "finish_reason": "stop"}]},
                  {"id": "completion", "choices": [], "usage": usage}]
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              content="".join("data: " + json.dumps(row) + "\n\n" for row in chunks) + "data: [DONE]\n\n")

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            model = ReasoningStreamChatModel(model="bound", api_key="parent-secret", base_url="https://provider.invalid/v1",
                                             http_async_client=client, temperature=.25, max_tokens=12)
            charged = billing or SimpleNamespace(operation_id="operation", before_call=lambda name: recorded.append(name),
                record=lambda response, call_id, name, **kw: recorded.append((call_id, name, response_usage(response))))

            async def emit(chunk):
                sent.append(chunk)
            body = {"model": "foreign", "api_key": "ignored", "base_url": "https://ignored.invalid", "temperature": 1,
                    "max_completion_tokens": 9000, "stream": stream, "stream_options": {"include_usage": False},
                    "messages": [{"role": "user", "content": "write"}], "billing_fault_retry": True,
                    "usage": {"prompt_tokens": 999}, "extra_headers": {"Authorization": "ignored"}}
            await forward_model(body, model, charged, emit)
    asyncio.run(execute())
    return calls, sent, recorded


@pytest.mark.parametrize("stream", [True, False])
def test_bound_parent_chooses_endpoint_model_settings_and_actual_usage(stream):
    calls, sent, recorded = run_bridge(stream)
    [(url, authorization, payload)] = calls
    assert str(url) == "https://provider.invalid/v1/chat/completions" and authorization == "Bearer parent-secret"
    assert payload["model"] == "bound" and payload["temperature"] == .25 and payload["max_completion_tokens"] == 12
    assert set(payload).isdisjoint({"usage", "base_url", "api_key", "billing_fault_retry", "extra_headers"})
    assert not stream or payload["stream_options"] == {"include_usage": True}
    assert b"saved" in b"".join(sent)
    assert recorded[0] == "bound"
    assert recorded[1][1:] == ("bound", {"input_tokens": 21, "output_tokens": 4, "cached_tokens": 8})


def test_provider_failure_is_redacted():
    with pytest.raises(RuntimeError) as exc:
        run_bridge(status=403)
    assert "403" in str(exc.value) and "private diagnostic" not in str(exc.value)


def test_only_frozen_owner_wallet_is_charged_and_budget_stops_later_calls(tmp_path):
    from app.platform.core.db import Database, UserRepository
    from app.platform.core.security import generate_master_key, load_master_key
    from app.platform.credits.ledger import BillingLedger

    database = Database(tmp_path / "wallet.db", load_master_key(generate_master_key()))
    try:
        users = UserRepository(database)
        owner = users.create(username="owner", password="password123")["user_id"]
        other = users.create(username="other", password="password123", is_super_admin=True)["user_id"]
        for uid in (owner, other):
            users.adjust_credits(uid, 100)
        ledger = BillingLedger(database)
        ledger.publish_price("bound", "10000", "10000", "10000", admin_id=other)
        op = ledger.reserve(user_id=owner, thread_id="creative:text:1", request_id="1", budget=".25",
                            model="bound", fingerprint="1")
        other_balance = ledger.balance(other)
        context = BillingContext(ledger, op["operation_id"])
        run_bridge(stream=True, billing=context)
        with pytest.raises(BudgetExhausted):
            run_bridge(billing=context)
        assert ledger.settle(op["operation_id"], "budget_exhausted")["charged"] == .25
        assert ledger.balance(other) == other_balance
        context.cancelled.set()
        with pytest.raises(OperationStopped):
            run_bridge(billing=context)
    finally:
        database.close()


def test_child_langchain_tool_binding_streams_through_exchange_only():
    seen = []
    async def exchange(body):
        seen.append(body)
        reply = {"id": "c", "object": "chat.completion.chunk", "model": "bound", "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": "scene"}, "finish_reason": "stop"}]}
        yield ("data: " + json.dumps(reply) + "\n\ndata: [DONE]\n\n").encode()

    async def invoke():
        model = make_worker_model("bound", exchange)
        bound = model.bind_tools([{"type": "function", "function": {"name": "save", "parameters": {"type": "object"}}}])
        return "".join([chunk.content async for chunk in bound.astream("write") if isinstance(chunk.content, str)])
    assert asyncio.run(invoke()) == "scene"
    assert seen[0]["tools"][0]["function"]["name"] == "save"


def test_parent_owns_reasoning_recovery_and_rejects_a_truncated_provider_stream():
    requests, charges, sent = [], [], []
    async def upstream(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        delta = {"reasoning_content": "thinking"} if first else {"content": "scene"}
        row = {"choices": [{"delta": delta, "finish_reason": "length" if first else "stop"}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
        data = "data: " + json.dumps(row) + "\n\ndata: [DONE]\n\n"
        if len(requests) == 3:
            data = "data: " + json.dumps({"choices": [{"delta": {"content": "interrupted"}}]}) + "\n\n"
        return httpx.Response(200, content=data)
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            model = ReasoningStreamChatModel(model="glm-test", api_key="parent", http_async_client=client)
            billing = SimpleNamespace(operation_id="retry", before_call=lambda _: None,
                record=lambda usage, call_id, name, billable: charges.append((response_usage(usage), billable)))
            async def emit(chunk):
                sent.append(chunk)
            body = {"messages": [{"role": "user", "content": "draft"}], "stream": True, "thinking": {"type": "disabled"}}
            await forward_model(body, model, billing, emit)
            assert len(requests) == 2 and "thinking" not in requests[0] and requests[1]["thinking"] == {"type": "disabled"}
            assert [billable for _, billable in charges] == [False, False]
            assert b"scene" in b"".join(sent) and b"length" not in b"".join(sent)
            with pytest.raises(RuntimeError, match="中断"):
                await forward_model(body, model, billing, emit)
    asyncio.run(execute())
