"""REQ-20261029-094930: runtime wiring must preserve the model and Skill snapshot."""
from __future__ import annotations

from types import SimpleNamespace

from contracts.payoff_constraints import PayoffCatalog, PayoffVerdict, content_hash

from app.domains.writing.payoff_runtime import VolumePayoffRuntime, make_payoff_model_call

CANDIDATE = "# 卷纲\n> 状态：定稿\n\n## 发展阶段\n安排\n## 爽点节奏表\n剧情\n## 爽点质检表\n检查\n## 卷末钩子\n衔接\n"


class Engine:
    def __init__(self, status="ready"):
        self.status = status
        self.catalog_calls = 0
        self.checks = []

    def catalog(self, skills):
        self.catalog_calls += 1
        return PayoffCatalog(self.status, "policy", errors=["definition unavailable"] if self.status == "error" else [])

    def check(self, candidate, policy):
        self.checks.append(candidate)
        return PayoffVerdict(content_hash(candidate), policy.policy_hash)

    def repair(self, candidate, violations, policy):
        raise AssertionError("The passing candidate does not need repair")


def test_catalog_is_lazy_and_reused_for_writes(tmp_path):
    engine = Engine()
    guard = VolumePayoffRuntime(
        tmp_path, skills=[{"skill_id": "s", "content": "source"}],
        operation_id="server-trace", engine=engine,
    )
    assert engine.catalog_calls == 0
    candidate = CANDIDATE
    result = guard.commit(1, "", candidate, "full")
    assert result["status"] == "committed"
    assert engine.catalog_calls == 1
    assert engine.checks == [candidate]
    guard.commit(1, candidate, candidate, "full")
    assert engine.catalog_calls == 1


def test_definition_load_error_keeps_candidate_as_draft(tmp_path):
    engine = Engine()
    guard = VolumePayoffRuntime(
        tmp_path, skills=[], operation_id="server-trace", engine=engine,
        load_error="skill store unavailable",
    )
    result = guard.commit(1, "", "# 卷纲\n> 状态：定稿\n", "full")
    assert result["status"] in {"draft", "blocked"}
    assert "skill store unavailable" in result["reason"]
    assert engine.catalog_calls == 0
    assert engine.checks == []
    assert "> 状态：定稿" not in (tmp_path / result["path"]).read_text()


def test_unrelated_volume_skill_does_not_require_a_payoff_catalog(tmp_path):
    engine = Engine("not_applicable")
    guard = VolumePayoffRuntime(
        tmp_path, skills=[{"skill_id": "method", "content": "保持因果连贯"}],
        operation_id="server-trace", engine=engine,
    )
    candidate = "# 卷纲\n> 状态：定稿\nordinary content"
    result = guard.commit(1, "", candidate, "full")
    assert result["status"] == "committed"
    assert engine.checks == []


def test_model_call_uses_bound_model_and_records_usage():
    class Model:
        model_name = "bound-writer-model"

        def __init__(self):
            self.calls = []

        def invoke(self, messages, config=None):
            assert config == {"callbacks": []}
            self.calls.append(messages)
            return SimpleNamespace(content='{"result":"ok"}', usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})

    class Recorder:
        def __init__(self):
            self.events = []

        def append_event(self, trace_id, event):
            self.events.append((trace_id, event))

    model, recorder = Model(), Recorder()
    call = make_payoff_model_call(model, recorder=recorder, trace_id="bound-trace")
    messages = [{"role": "system", "content": "review"}]
    assert call(messages, "payoff_review") == '{"result":"ok"}'
    assert model.calls == [messages]
    assert [e[1]["type"] for e in recorder.events if e[1]["type"] in {"llm_start", "llm_end"}] == ["llm_start", "llm_end"]
    end = next(event for _, event in recorder.events if event["type"] == "llm_end")
    assert end["usage"]["total_tokens"] == 7
    assert end["model_name"] == "bound-writer-model"


def test_model_retry_records_a_valid_trace_event():
    import httpx
    from openai import APIConnectionError
    from datetime import datetime, timezone
    from contracts.trace import TraceLogEvent

    class Model:
        model_name = "bound"
        calls = 0

        def invoke(self, messages, config=None):
            self.calls += 1
            if self.calls == 1:
                raise APIConnectionError(request=httpx.Request("POST", "https://example.test"))
            return SimpleNamespace(content="{}", usage_metadata=None)

    class Recorder:
        def __init__(self):
            self.events = []

        def append_event(self, trace_id, event):
            TraceLogEvent(trace_id=trace_id, event_id=str(len(self.events)),
                          sequence=len(self.events) + 1, timestamp=datetime.now(timezone.utc).isoformat(), **event)
            self.events.append(event)

    model, recorder = Model(), Recorder()
    make_payoff_model_call(model, recorder=recorder, trace_id="t")([], "payoff_review")
    assert model.calls == 2
    assert any(event["type"] == "middleware_intervention" for event in recorder.events)


def test_cancelled_review_cannot_commit_after_model_returns(tmp_path):
    alive = [True]

    class StoppingEngine(Engine):
        def check(self, candidate, policy):
            alive[0] = False
            return super().check(candidate, policy)

    original = CANDIDATE.replace("安排", "已确认安排")
    (tmp_path / "卷纲").mkdir()
    path = tmp_path / "卷纲/第01卷.md"
    path.write_text(original, encoding="utf-8")
    guard = VolumePayoffRuntime(
        tmp_path, skills=[{"skill_id": "s", "content": "source"}],
        operation_id="stopped-trace", engine=StoppingEngine(), is_active=lambda: alive[0],
    )
    result = guard.commit(1, original, CANDIDATE, "full")
    assert result["status"] in {"blocked", "draft"}
    assert path.read_text(encoding="utf-8") == original
    assert result["repair_attempts"] == 0


def test_async_model_request_is_cancelled_when_parent_stops():
    import asyncio
    import pytest

    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        alive = [True]

        class Model:
            async def ainvoke(self, messages, config=None):
                assert config == {"callbacks": []}
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            def invoke(self, messages, config=None):
                raise AssertionError("the production async transport must be cancellable")

        call = make_payoff_model_call(
            Model(), event_loop=asyncio.get_running_loop(), is_active=lambda: alive[0],
        )
        task = asyncio.create_task(asyncio.to_thread(call, [], "payoff_review"))
        await asyncio.wait_for(started.wait(), 3)
        alive[0] = False
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(task, 3)
        await asyncio.wait_for(cancelled.wait(), 3)

    asyncio.run(scenario())


def test_nonstream_call_on_event_loop_thread_does_not_wait_on_itself():
    import asyncio
    import threading

    async def scenario():
        alive = [True]

        class Model:
            def invoke(self, messages, config=None):
                return SimpleNamespace(content="{}", usage_metadata=None)

            async def ainvoke(self, messages, config=None):
                raise AssertionError("cannot wait synchronously on the caller's own event loop")

        call = make_payoff_model_call(
            Model(), event_loop=asyncio.get_running_loop(), is_active=lambda: alive[0],
        )
        timer = threading.Timer(1, lambda: alive.__setitem__(0, False))
        timer.start()
        try:
            assert call([], "payoff_review") == "{}"
        finally:
            timer.cancel()

    asyncio.run(scenario())
