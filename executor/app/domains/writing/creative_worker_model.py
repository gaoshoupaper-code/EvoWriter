"""Chat-completions relay for a writing worker with no provider credentials or network."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
import json
from uuid import uuid4
from weakref import WeakValueDictionary

import httpx
from langchain_openai import ChatOpenAI

from app.domains.writing.deepseek_thinking import DeepSeekThinkingChatModel, ReasoningStreamChatModel
from app.domains.writing.models import parse_writer_model
from app.platform.credits.runtime import BillingContext

Emit = Callable[[bytes], Awaitable[None]]
Exchange = Callable[[dict], AsyncIterator[bytes]]
_operation_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


class _ExchangeStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        async for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        close = getattr(self.chunks, "aclose", None)
        if close:
            await close()


class _WorkerReasoningModel(ReasoningStreamChatModel):
    def _recover_empty_reasoning(self, options, visible, finish):
        # Only the parent can authorize and account for a provider retry.
        return False


def make_worker_model(model_name: str, exchange: Exchange) -> ChatOpenAI:
    """Keep LangChain tool binding and reasoning chunks on the worker's IPC transport."""
    async def relay(request):
        if request.method != "POST" or request.url.path != "/v1/chat/completions":
            raise ValueError("创作模型只接受对话请求")
        body = json.loads(await request.aread())
        content_type = "text/event-stream" if body.get("stream") else "application/json"
        return httpx.Response(200, headers={"Content-Type": content_type}, stream=_ExchangeStream(exchange(body)))

    def reject_sync(request):
        raise RuntimeError("创作模型使用异步调用")

    provider, name = parse_writer_model(model_name)
    adapter = DeepSeekThinkingChatModel if provider == "deepseek" else _WorkerReasoningModel
    return adapter(model=name, api_key="worker-local-only", base_url="http://worker.invalid/v1/",
                   use_responses_api=False, max_retries=0, stream_usage=True,
                   http_client=httpx.Client(transport=httpx.MockTransport(reject_sync)),
                   http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(relay)))


def _payload(body: dict, model: ChatOpenAI) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
        raise ValueError("创作模型缺少对话消息")
    messages = []
    for row in body["messages"]:
        if not isinstance(row, dict) or row.get("role") not in {"user", "system", "developer", "tool", "assistant"}:
            raise ValueError("创作模型消息格式错误")
        content = row.get("content")
        if isinstance(content, list):
            if not all(isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                       for part in content):
                raise ValueError("创作模型只接受文字和本地函数调用")
            content = [{"type": "text", "text": part["text"]} for part in content]
        elif content is not None and not isinstance(content, str):
            raise ValueError("创作模型消息内容无效")
        message = {key: row[key] for key in ("role", "name", "reasoning_content", "tool_calls", "tool_call_id") if key in row}
        messages.append({**message, "content": content})
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise ValueError("创作模型流式标记无效")
    tools = body.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(tool, dict) and tool.get("type") == "function"
                                              and isinstance(tool.get("function"), dict) for tool in tools):
        raise ValueError("创作模型只允许声明本地函数工具")
    trusted = model._get_request_payload([], stream=stream)
    trusted.update(trusted.pop("extra_body", None) or {})
    trusted.update(messages=messages, model=model.model_name, stream=stream)
    if tools:
        trusted["tools"] = [{"type": "function", "function": tool["function"]} for tool in tools]
    for key in ("tool_choice", "parallel_tool_calls", "response_format", "stop"):
        if key in body:
            trusted[key] = body[key]
    if stream:
        trusted["stream_options"] = {"include_usage": True}
    return trusted


async def _stream_reply(response, emit, model, receipt, *, allow_recovery):
    finish = None
    visible = done = recover = False
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            break
        if not data:
            continue
        value = json.loads(data)
        if not isinstance(value, dict) or "error" in value:
            raise RuntimeError("模型响应流出错")
        if value.get("usage"):
            receipt["usage"] = value["usage"]
        for choice in value.get("choices", []):
            delta = choice.get("delta") or {}
            visible = visible or bool(delta.get("content") or delta.get("tool_calls"))
            finish = choice.get("finish_reason") or finish
        if allow_recovery and isinstance(model, ReasoningStreamChatModel):
            recover = model._recover_empty_reasoning({}, visible, finish)
        if not recover:
            await emit((line + "\n\n").encode("utf-8"))
    if not done and not finish:
        raise RuntimeError("模型响应流提前中断")
    return recover


async def forward_model(body: dict, model: ChatOpenAI, billing: BillingContext | None, emit: Emit) -> None:
    """Call the parent's bound endpoint. Only provider usage can update the owner's operation."""
    payload = _payload(body, model)
    async with AsyncExitStack() as stack:
        if billing is not None:
            lock = _operation_locks.setdefault(billing.operation_id, asyncio.Lock())
            await stack.enter_async_context(lock)
            billing.before_call(model.model_name)
        client = model.http_async_client
        if client is None:
            client = await stack.enter_async_context(httpx.AsyncClient(timeout=model.request_timeout))
        root = model.root_async_client
        endpoint = root.base_url.join("chat/completions")
        for attempt in range(2):
            receipt = {}
            accepted = recover = False
            try:
                async with client.stream("POST", endpoint, headers=root.auth_headers, json=payload,
                                         follow_redirects=False, timeout=model.request_timeout) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"模型服务返回 HTTP {response.status_code}")
                    accepted = True
                    if payload["stream"]:
                        recover = await _stream_reply(response, emit, model, receipt, allow_recovery=attempt == 0)
                        final = b"data: [DONE]\n\n"
                    else:
                        final = await response.aread()
                        value = json.loads(final)
                        if not isinstance(value, dict) or "error" in value:
                            raise RuntimeError("模型响应出错")
                        receipt["usage"] = value.get("usage")
            except (httpx.HTTPError, ValueError):
                raise RuntimeError("模型服务通信失败") from None
            finally:
                # The worker may close its pipe while receiving a paid response.
                # Account for provider usage already received, including partial calls.
                if accepted and billing is not None:
                    billing.record(receipt, uuid4().hex, model.model_name, billable=attempt == 0 and not recover)
            if recover:
                # Match the writing adapter's one free retry after a reasoning-only GLM response.
                payload = {**payload, "thinking": {"type": "disabled"}}
                continue
            await emit(final)
            return
