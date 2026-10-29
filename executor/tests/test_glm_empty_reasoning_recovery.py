import asyncio

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from app.domains.writing.deepseek_thinking import ReasoningStreamChatModel


def chunk(content="", *, finish=None, usage=None, tool=False):
    message = AIMessageChunk(
        content=content,
        usage_metadata=usage,
        tool_call_chunks=[{"name": "save_artifact", "args": "", "id": "call-a", "index": 0}] if tool else [],
    )
    return ChatGenerationChunk(message=message, generation_info={"finish_reason": finish} if finish else None)


def test_empty_reasoning_retries_once_and_keeps_both_usage_records(monkeypatch):
    calls = []
    usage = {"input_tokens": 5, "output_tokens": 32, "total_tokens": 37}

    def stream(self, *args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            terminal = chunk(finish="length")
            terminal.generation_info.update(model_name="glm-5.3", system_fingerprint="first",
                                            headers={"x-request-id": "first"})
            yield terminal
            yield chunk(usage=usage)
        else:
            assert kwargs["extra_body"] == {"custom": True, "thinking": {"type": "disabled"}}
            terminal = chunk("done", finish="stop", usage=usage)
            terminal.generation_info["headers"] = {"x-request-id": "second"}
            yield terminal

    monkeypatch.setattr(ChatOpenAI, "_stream", stream)
    model = ReasoningStreamChatModel(model="glm-5.3", api_key="test")
    chunks = list(model._stream([], extra_body={"custom": True}))
    assert len(calls) == 2
    assert "".join(c.text for c in chunks) == "done"
    assert [c.generation_info.get("finish_reason") for c in chunks if c.generation_info] == ["stop"]
    assert sum((c.message.usage_metadata or {}).get("output_tokens", 0) for c in chunks) == 64
    assert not any((c.generation_info or {}).get("system_fingerprint") == "first" for c in chunks)
    assert [c.generation_info["headers"] for c in chunks if (c.generation_info or {}).get("headers")] == [{"x-request-id": "second"}]


@pytest.mark.parametrize("content,tool,finish,disabled,model_name", [
    ("partial", False, "length", False, "glm-5.3"),
    ("", True, "length", False, "glm-5.3"),
    ("", False, "stop", False, "glm-5.3"),
    ("", False, "length", True, "glm-5.3"),
    ("", False, "length", False, "gpt-4o-mini"),
])
def test_visible_output_or_other_models_are_not_replayed(monkeypatch, content, tool, finish, disabled, model_name):
    calls = []

    def stream(self, *args, **kwargs):
        calls.append(kwargs)
        yield chunk(content, tool=tool, finish=finish)

    monkeypatch.setattr(ChatOpenAI, "_stream", stream)
    model = ReasoningStreamChatModel(model=model_name, api_key="test")
    options = {"extra_body": {"thinking": {"type": "disabled"}}} if disabled else {}
    result = list(model._stream([], **options))
    assert len(calls) == 1
    assert result[-1].generation_info["finish_reason"] == finish


@pytest.mark.parametrize("disabled,expected_calls", [(True, 1), (False, 2)])
def test_model_level_extra_body_is_preserved(monkeypatch, disabled, expected_calls):
    calls = []

    def stream(self, *args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            assert kwargs["extra_body"] == {"custom": True, "thinking": {"type": "disabled"}}
        yield chunk(finish="length")

    monkeypatch.setattr(ChatOpenAI, "_stream", stream)
    model = ReasoningStreamChatModel(
        model="glm-5.3", api_key="test",
        extra_body={"custom": True, "thinking": {"type": "disabled" if disabled else "enabled"}},
    )
    list(model._stream([]))
    assert len(calls) == expected_calls


def test_second_empty_response_is_not_retried_again(monkeypatch):
    calls = []

    def stream(self, *args, **kwargs):
        calls.append(kwargs)
        yield chunk(finish="length")

    monkeypatch.setattr(ChatOpenAI, "_stream", stream)
    model = ReasoningStreamChatModel(model="glm-5.3", api_key="test")
    result = list(model._stream([]))
    assert len(calls) == 2
    assert result[-1].generation_info["finish_reason"] == "length"


def test_async_empty_reasoning_recovers_once(monkeypatch):
    calls = []

    async def stream(self, *args, **kwargs):
        calls.append(kwargs)
        yield chunk(finish="length") if len(calls) == 1 else chunk("done", finish="stop")

    monkeypatch.setattr(ChatOpenAI, "_astream", stream)
    model = ReasoningStreamChatModel(model="glm-5.3", api_key="test")

    async def scenario():
        return [c async for c in model._astream([])]

    result = asyncio.run(scenario())
    assert len(calls) == 2
    assert calls[1]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "".join(c.text for c in result) == "done"
