"""telemetry 模块单测（FR-001 / FR-006 / FR-008 / AC-002 本地等价）。

覆盖：
- 未配置 endpoint → setup False + 全部 record_* 直通不抛错（FR-001 埋点开关）；
- 配置 endpoint（InMemoryMetricReader 注入）→ 指标按 contracts/metrics 契约名
  与标签产出（FR-003 标签值域）；
- FR-006 静默保护的真实 except 路径（注入抛异常 instrument，review 整改）；
- drain 深度 observable gauge 启用态产出（review 整改：Observation 回调 API）；
- SSE 响应判定（header 而非 media_type 属性，review 整改）；
- run 在途配对（trace_id 配对防重启漂移，review 整改）；
- recorder 事件流 → 指标派生映射（llm/tool/error/终态分支）；
- contracts.metrics 纯函数。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.platform import telemetry
from contracts import metrics as cm


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    """每个用例独立埋点状态，退出时清理 provider（避免跨用例污染）。"""
    telemetry._reset_for_tests()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    yield
    telemetry._reset_for_tests()


def _metrics_by_name(reader: InMemoryMetricReader) -> dict[str, list]:
    """收集当前指标 → {指标名: [数据点...]}。"""
    data = reader.get_metrics_data()
    result: dict[str, list] = {}
    if data is None:
        return result
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                result.setdefault(metric.name, []).extend(metric.data.data_points)
    return result


def _enabled(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector-test:4318")
    reader = InMemoryMetricReader()
    assert telemetry.setup_telemetry(
        "executor", metric_reader=reader, system_metrics=False
    ) is True
    return reader


# ── 开关与直通 ─────────────────────────────────────────────


def test_setup_off_without_endpoint_all_calls_pass_through():
    """AC-002 本地等价：无 endpoint 时埋点零开销直通，任何调用不抛错。"""
    assert telemetry.setup_telemetry("executor") is False
    assert telemetry.is_enabled() is False
    telemetry.record_llm_call("m", "a", "completed", 1.0, {"input_tokens": 3})
    telemetry.record_tool_call("t", "completed", 0.5)
    telemetry.record_intervention("retry")
    telemetry.record_llm_attempt_failed("m", "TimeoutError")
    telemetry.record_run_start("creation")
    telemetry.record_run_terminal("creation", "completed", 9.0)
    telemetry.record_sse_ttfb("/api/x", 0.2)
    telemetry.register_drain_depth_gauge("executor", lambda: 3)


def test_setup_init_failure_degrades_silently(monkeypatch: pytest.MonkeyPatch):
    """初始化失败 → 返回 False 静默降级，后续调用全部直通（FR-006）。"""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector-test:4318")
    import opentelemetry.sdk.metrics as sdk_metrics

    monkeypatch.setattr(sdk_metrics, "MeterProvider", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert telemetry.setup_telemetry("executor") is False
    assert telemetry.is_enabled() is False
    telemetry.record_llm_call("m", "a", "completed", 1.0, None)  # 不抛错


def test_setup_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    assert telemetry.setup_telemetry("executor", metric_reader=reader) is True


# ── 契约指标产出 ───────────────────────────────────────────


def test_facade_emits_contract_metrics(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)

    telemetry.record_llm_call(
        "gpt-test", "outline", cm.STATUS_COMPLETED, 1.5,
        {"input_tokens": 10, "output_tokens": 4},
    )
    telemetry.record_tool_call("write_file", cm.STATUS_FAILED, 0.25)
    telemetry.record_intervention("model_attempt_failed")
    telemetry.record_llm_attempt_failed("gpt-test", "TimeoutError")
    telemetry.record_run_start(cm.WORKLOAD_CREATION, "trace-1")
    telemetry.record_run_terminal(cm.WORKLOAD_CREATION, cm.STATUS_COMPLETED, 12.0, "trace-1")
    telemetry.record_sse_ttfb("/api/generate", 0.3)

    by_name = _metrics_by_name(reader)

    points = by_name[cm.LLM_CALL_DURATION]
    assert points, "llm.call.duration 未产出"
    assert points[0].attributes == {
        cm.LABEL_MODEL: "gpt-test", cm.LABEL_AGENT: "outline",
        cm.LABEL_STATUS: cm.STATUS_COMPLETED,
    }

    token_points = {
        p.attributes[cm.LABEL_DIRECTION]: p.value for p in by_name[cm.LLM_TOKENS_USAGE]
    }
    assert token_points == {"input": 10, "output": 4}

    assert by_name[cm.TOOL_CALL_DURATION][0].attributes == {
        cm.LABEL_TOOL: "write_file", cm.LABEL_STATUS: cm.STATUS_FAILED,
    }
    assert by_name[cm.MIDDLEWARE_INTERVENTION][0].attributes == {
        cm.LABEL_ACTION: "model_attempt_failed",
    }
    assert by_name[cm.LLM_ATTEMPT_FAILED][0].attributes == {
        cm.LABEL_MODEL: "gpt-test", cm.LABEL_ERROR_CLASS: "TimeoutError",
    }

    assert by_name[cm.AGENT_RUN_DURATION][0].attributes == {
        cm.LABEL_WORKLOAD: cm.WORKLOAD_CREATION, cm.LABEL_STATUS: cm.STATUS_COMPLETED,
    }
    assert by_name[cm.RUN_LAST_SUCCESS][0].attributes == {
        cm.LABEL_WORKLOAD: cm.WORKLOAD_CREATION,
    }
    # 在途配对后归零（review 整改：显式断言，不再只靠注释）
    active = by_name[cm.AGENT_RUN_ACTIVE]
    assert [p.value for p in active if p.attributes == {cm.LABEL_WORKLOAD: cm.WORKLOAD_CREATION}] == [0]

    assert by_name[cm.SSE_TTFB][0].attributes == {cm.LABEL_ROUTE: "/api/generate"}


def test_run_active_trace_id_pairing_no_drift(monkeypatch: pytest.MonkeyPatch):
    """未配对的终态事件（重启/收养路径）不得把在途计数打成负数。"""
    reader = _enabled(monkeypatch)
    telemetry.record_run_start(cm.WORKLOAD_CREATION, "trace-a")
    # 终态来自另一个进程代（trace-b 未见 run_start）——不 -1
    telemetry.record_run_terminal(cm.WORKLOAD_CREATION, cm.STATUS_CANCELLED, 5.0, "trace-b")
    by_name = _metrics_by_name(reader)
    active = [p.value for p in by_name[cm.AGENT_RUN_ACTIVE]
              if p.attributes == {cm.LABEL_WORKLOAD: cm.WORKLOAD_CREATION}]
    assert active == [1]  # 只有 trace-a 的 +1


# ── FR-006 静默保护：真实 except 路径（review 整改）─────────


def test_facade_failure_is_silent_on_raising_instrument(monkeypatch: pytest.MonkeyPatch):
    """instrument 抛异常时 record_* 静默吞掉（FR-006），逐路径真实触发。"""
    _enabled(monkeypatch)

    class _Boom:
        def record(self, *a, **kw):
            raise RuntimeError("boom")

        def add(self, *a, **kw):
            raise RuntimeError("boom")

        def set(self, *a, **kw):
            raise RuntimeError("boom")

    originals = dict(telemetry._instruments)

    def _swap_then_restore(key: str, calls) -> None:
        telemetry._instruments[key] = _Boom()
        try:
            calls()
        finally:
            telemetry._instruments.update(originals)

    # histogram record 路径
    _swap_then_restore("llm_duration", lambda: telemetry.record_llm_call("m", "a", "completed", 1.0, None))
    # counter add 路径（带 usage 才会触达 token 计数）
    _swap_then_restore("llm_tokens", lambda: telemetry.record_llm_call("m", "a", "completed", 1.0, {"input_tokens": 1}))
    # run_start 在途 +1 路径
    _swap_then_restore("run_active", lambda: telemetry.record_run_start(cm.WORKLOAD_CREATION, "t-boom"))
    # run 终态在途 -1 路径（先真实 start 建配对，再换 boom）
    telemetry.record_run_start(cm.WORKLOAD_CREATION, "t-boom2")
    _swap_then_restore(
        "run_active",
        lambda: telemetry.record_run_terminal(cm.WORKLOAD_CREATION, cm.STATUS_FAILED, 1.0, "t-boom2"),
    )
    # last_success gauge 路径（未配对终态：跳过 run_active，直达 completed 刷新）
    _swap_then_restore(
        "run_last_success",
        lambda: telemetry.record_run_terminal(cm.WORKLOAD_CREATION, cm.STATUS_COMPLETED, 1.0, "t-unpaired"),
    )


# ── drain 深度 gauge 启用态（review 整改：Observation 回调）──


def test_drain_gauge_emits_when_enabled(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    telemetry.register_drain_depth_gauge("executor", lambda: 7)
    by_name = _metrics_by_name(reader)
    points = by_name.get(cm.TRACE_DRAIN_QUEUE_DEPTH, [])
    assert points, "drain 深度 gauge 未产出（回调 API 错误会静默零产出）"
    assert points[0].value == 7
    assert points[0].attributes == {cm.LABEL_SERVICE: "executor"}


def test_drain_gauge_getter_exception_silent(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)

    def _bad():
        raise RuntimeError("getter boom")

    telemetry.register_drain_depth_gauge("executor", _bad)
    by_name = _metrics_by_name(reader)  # 不抛错，gauge 无数据点
    assert cm.TRACE_DRAIN_QUEUE_DEPTH not in by_name


# ── SSE 判定（review 整改：header 而非 media_type 属性）────


def test_is_sse_response():
    sse = SimpleNamespace(headers={"content-type": "text/event-stream; charset=utf-8"})
    json_resp = SimpleNamespace(headers={"content-type": "application/json"})
    no_header = SimpleNamespace(headers={})
    assert telemetry.is_sse_response(sse) is True
    assert telemetry.is_sse_response(json_resp) is False
    assert telemetry.is_sse_response(no_header) is False
    assert telemetry.is_sse_response(None) is False
    # BaseHTTPMiddleware 场景：media_type 属性为 None，只查 header
    middleware_resp = SimpleNamespace(media_type=None, headers={"content-type": "text/event-stream"})
    assert telemetry.is_sse_response(middleware_resp) is True


# ── recorder 事件流 → 指标派生映射 ─────────────────────────


def test_emit_metrics_maps_recorder_events(monkeypatch: pytest.MonkeyPatch):
    """recorder 事件流 → 指标派生映射（FR-001 L3 同一埋点管线，含错误分支）。"""
    reader = _enabled(monkeypatch)

    from app.platform.trace.recorder import _emit_metrics
    from app.platform.trace.schemas import TraceLogEvent

    def ev(**kw):
        base = dict(
            trace_id="t", event_id="e", sequence=1, type="llm_end",
            status="completed", timestamp="now", source="middleware", schema_version=2,
        )
        base.update(kw)
        return TraceLogEvent(**base)

    # 成功路径
    _emit_metrics(
        ev(duration_ms=1500, model_name="gpt-x", agent_name="outline",
           usage={"input_tokens": 5, "output_tokens": 2}),
        cm.WORKLOAD_CREATION,
    )
    # 错误分支（review 整改补测）
    _emit_metrics(ev(type="llm_error", status="failed", model_name="gpt-x", duration_ms=100), cm.WORKLOAD_CREATION)
    _emit_metrics(ev(type="tool_end", tool_name="write_file", duration_ms=250), cm.WORKLOAD_CREATION)
    _emit_metrics(ev(type="tool_error", status="failed", tool_name="write_file", duration_ms=10), cm.WORKLOAD_CREATION)
    _emit_metrics(
        ev(type="middleware_intervention",
           intervention={"action": "model_attempt_failed", "reason": "attempt 1/2:TimeoutError"}),
        cm.WORKLOAD_CREATION,
    )
    _emit_metrics(ev(type="run_start", status="running"), cm.WORKLOAD_CREATION)
    _emit_metrics(ev(type="run_end", status="completed", duration_ms=12000), cm.WORKLOAD_CREATION)
    _emit_metrics(ev(type="run_error", status="failed", duration_ms=3000), cm.WORKLOAD_CREATION)
    _emit_metrics(ev(type="run_cancelled", status="cancelled", duration_ms=2000), cm.WORKLOAD_CREATION)

    by_name = _metrics_by_name(reader)
    assert by_name[cm.LLM_CALL_DURATION][0].attributes[cm.LABEL_MODEL] == "gpt-x"
    token_points = {
        p.attributes[cm.LABEL_DIRECTION]: p.value for p in by_name[cm.LLM_TOKENS_USAGE]
    }
    assert token_points == {"input": 5, "output": 2}
    assert by_name[cm.TOOL_CALL_DURATION][0].attributes[cm.LABEL_TOOL] == "write_file"
    assert by_name[cm.LLM_ATTEMPT_FAILED][0].attributes[cm.LABEL_ERROR_CLASS] == "TimeoutError"
    run_status = {p.attributes[cm.LABEL_STATUS] for p in by_name[cm.AGENT_RUN_DURATION]}
    assert run_status == {cm.STATUS_COMPLETED, cm.STATUS_FAILED, cm.STATUS_CANCELLED}


# ── contracts.metrics 纯函数 ────────────────────────────────


def test_usage_tokens_key_variants():
    assert cm.usage_tokens({"input_tokens": 3, "output_tokens": 5}) == (3, 5)
    assert cm.usage_tokens({"prompt_tokens": 7, "completion_tokens": 2}) == (7, 2)
    assert cm.usage_tokens(None) == (0, 0)
    assert cm.usage_tokens({"input_tokens": "bad"}) == (0, 0)


def test_usage_tokens_object_shape():
    """生产实际形态：TraceLogEvent.usage 被 pydantic 强转为 TraceUsage 对象。"""
    from contracts.trace import TraceUsage

    assert cm.usage_tokens(TraceUsage(input_tokens=3, output_tokens=5)) == (3, 5)
    assert cm.usage_tokens(TraceUsage()) == (0, 0)


def test_error_class_from_reason():
    assert cm.error_class_from_reason("attempt 1/2:TimeoutError") == "TimeoutError"
    assert cm.error_class_from_reason("no-colon-reason") == "no-colon-reason"
    assert len(cm.error_class_from_reason("x" * 200)) == 64


def test_terminal_run_status_mapping():
    assert cm.terminal_run_status("run_end") == cm.STATUS_COMPLETED
    assert cm.terminal_run_status("run_error") == cm.STATUS_FAILED
    assert cm.terminal_run_status("run_cancelled") == cm.STATUS_CANCELLED
    # cancel_timeout：取消未收敛的技术故障，按 failed 口径（EDGE-007，review 整改）
    assert cm.terminal_run_status("cancel_timeout") == cm.STATUS_FAILED
    assert "cancel_timeout" in cm.TERMINAL_RUN_EVENTS
    assert cm.terminal_run_status("llm_end") is None


def test_bounded_label():
    assert cm.bounded_label(None) == "unknown"
    assert cm.bounded_label("") == "unknown"
    assert cm.bounded_label("meta-agent") == "meta-agent"
    assert len(cm.bounded_label("m" * 100)) == 64
    assert cm.bounded_label("route", max_length=80) == "route"
