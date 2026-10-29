"""卷纲长输出使用流式传输；部分响应和取消都不得整次重放。"""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from langchain_core.messages import AIMessageChunk
from openai import APIConnectionError

from app.domains.writing.payoff_runtime import make_payoff_model_call
from app.domains.writing.retry import WriterRetryError

USAGE = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}


class Recorder:
    def __init__(self):
        self.events = []

    def append_event(self, trace_id, event):
        self.events.append(event)


def connection_error():
    return APIConnectionError(request=httpx.Request("POST", "https://example.test"))


def test_healthy_long_stream_aggregates_text_usage_and_duration(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr("app.domains.writing.payoff_runtime.time.perf_counter", lambda: elapsed[0])

    class Model:
        model_name = "bound-model"

        def stream(self, messages, config=None):
            assert config == {"callbacks": []}
            elapsed[0] += 206
            yield AIMessageChunk(content='{"result":')
            elapsed[0] += 206
            yield AIMessageChunk(content='"')
            elapsed[0] += 206
            yield AIMessageChunk(content='ok')
            elapsed[0] += 206
            yield AIMessageChunk(content='"}', usage_metadata=USAGE)

        def invoke(self, *args, **kwargs):
            raise AssertionError("长输出不能等非流式整篇响应")

    recorder = Recorder()
    assert make_payoff_model_call(Model(), recorder=recorder, trace_id="t")([], "payoff_repair") == '{"result":"ok"}'
    end = next(event for event in recorder.events if event["type"] == "llm_end")
    assert end["usage"] == USAGE
    assert end["duration_ms"] == 824_000
    assert end["model_name"] == "bound-model"


@pytest.mark.parametrize("purpose", ["payoff_catalog", "payoff_catalog_review", "payoff_review", "payoff_repair"])
def test_glm_structured_stream_disables_thinking(purpose):
    class Model:
        model_name = "glm-5.3"

        def stream(self, messages, config=None, **kwargs):
            assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}
            yield AIMessageChunk(content="{}")

    assert make_payoff_model_call(Model())([], purpose) == "{}"


def test_glm_async_structured_stream_disables_thinking():
    async def scenario():
        class Model:
            model_name = "glm-5.3"

            async def astream(self, messages, config=None, **kwargs):
                assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}
                yield AIMessageChunk(content="{}")

        call = make_payoff_model_call(Model(), event_loop=asyncio.get_running_loop())
        assert await asyncio.to_thread(call, [], "payoff_catalog") == "{}"

    asyncio.run(scenario())


def test_non_structured_glm_call_keeps_default_thinking():
    class Model:
        model_name = "glm-5.3"

        def stream(self, messages, config=None):
            yield AIMessageChunk(content="{}")

    assert make_payoff_model_call(Model())([], "writing") == "{}"


def test_stream_retries_only_before_first_chunk():
    class Model:
        calls = 0

        def stream(self, messages, config=None):
            self.calls += 1
            if self.calls == 1:
                raise connection_error()
            yield AIMessageChunk(content="{}")

    model = Model()
    assert make_payoff_model_call(model)([], "payoff_review") == "{}"
    assert model.calls == 2


def test_partial_sync_stream_is_not_replayed_and_preserves_usage():
    closed = []

    class Model:
        calls = 0

        def stream(self, messages, config=None):
            self.calls += 1
            try:
                yield AIMessageChunk(content="partial", usage_metadata=USAGE)
                raise connection_error()
            finally:
                closed.append(True)

    model, recorder = Model(), Recorder()
    with pytest.raises(WriterRetryError) as failure:
        make_payoff_model_call(model, recorder=recorder, trace_id="t")([], "payoff_repair")
    assert failure.value.had_partial_response is True
    assert failure.value.attempts_made == model.calls == 1
    assert failure.value.usage == USAGE
    assert closed == [True]
    assert not any(event["type"] == "llm_end" for event in recorder.events)
    assert next(event for event in recorder.events if event["type"] == "llm_error")["usage"] == USAGE


def test_async_stream_runs_on_bound_event_loop_and_aggregates_chunks():
    async def scenario():
        loop_thread = threading.get_ident()

        class Model:
            async def astream(self, messages, config=None):
                assert threading.get_ident() == loop_thread
                assert config == {"callbacks": []}
                yield AIMessageChunk(content='{"ok":')
                await asyncio.sleep(0)
                yield AIMessageChunk(content="true}", usage_metadata=USAGE)

            async def ainvoke(self, *args, **kwargs):
                raise AssertionError("优先使用异步流")

            def stream(self, *args, **kwargs):
                raise AssertionError("异步模型必须留在绑定事件循环")

        recorder = Recorder()
        call = make_payoff_model_call(Model(), event_loop=asyncio.get_running_loop(), recorder=recorder, trace_id="t")
        assert await asyncio.to_thread(call, [], "payoff_repair") == '{"ok":true}'
        assert next(event for event in recorder.events if event["type"] == "llm_end")["usage"] == USAGE

    asyncio.run(scenario())


def test_partial_async_stream_is_not_replayed():
    async def scenario():
        class Model:
            calls = 0

            async def astream(self, messages, config=None):
                self.calls += 1
                yield AIMessageChunk(content="partial", usage_metadata=USAGE)
                raise connection_error()

        model = Model()
        call = make_payoff_model_call(model, event_loop=asyncio.get_running_loop())
        with pytest.raises(WriterRetryError) as failure:
            await asyncio.to_thread(call, [], "payoff_repair")
        assert failure.value.had_partial_response is True
        assert failure.value.attempts_made == model.calls == 1
        assert failure.value.usage == USAGE

    asyncio.run(scenario())


def test_stopping_parent_closes_async_stream_while_waiting_for_next_chunk():
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()
        alive = [True]

        class Model:
            calls = 0

            async def astream(self, messages, config=None):
                self.calls += 1
                try:
                    yield AIMessageChunk(content="partial")
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        model, recorder = Model(), Recorder()
        call = make_payoff_model_call(
            model, event_loop=asyncio.get_running_loop(), is_active=lambda: alive[0],
            recorder=recorder, trace_id="t",
        )
        task = asyncio.create_task(asyncio.to_thread(call, [], "payoff_repair"))
        await asyncio.wait_for(started.wait(), 3)
        alive[0] = False
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(task, 3)
        await asyncio.wait_for(closed.wait(), 3)
        assert model.calls == 1
        assert [event["type"] for event in recorder.events] == ["llm_start", "llm_error"]

    asyncio.run(scenario())
