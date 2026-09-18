"""evolution telemetry 单测（FR-001 / FR-006 / FR-007 / AC-002 本地等价）。

与 executor/tests/test_telemetry.py 同构（telemetry 模块为移植关系），
evolution 独有面单独覆盖：
- L4 摄取指标全状态口径（ok-touch / ok-sync-no-touch / no_content / rejected / error）；
- evolution recorder._emit_metrics 映射（workload 查找 + unknown 兜底）；
- 摄取包装的真实路径测试（monkeypatch 拉取/导入边界）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.core import telemetry
from contracts import metrics as cm


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    """每个用例独立埋点状态，退出时清理 provider（避免跨用例污染）。"""
    telemetry._reset_for_tests()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    yield
    telemetry._reset_for_tests()


def _metrics_by_name(reader: InMemoryMetricReader) -> dict[str, list]:
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
        "evolution", metric_reader=reader, system_metrics=False
    ) is True
    return reader


# ── 开关与直通 ─────────────────────────────────────────────


def test_setup_off_without_endpoint_all_calls_pass_through():
    assert telemetry.setup_telemetry("evolution") is False
    telemetry.record_llm_call("m", "a", "completed", 1.0, {"input_tokens": 3})
    telemetry.record_tool_call("t", "completed", 0.5)
    telemetry.record_intervention("retry")
    telemetry.record_llm_attempt_failed("m", "TimeoutError")
    telemetry.record_run_start("evaluation")
    telemetry.record_run_terminal("evolution", "completed", 9.0)
    telemetry.record_ingestion("ok", 0.4)
    telemetry.register_drain_depth_gauge("evolution", lambda: 3)


def test_facade_failure_is_silent_on_raising_instrument(monkeypatch: pytest.MonkeyPatch):
    """FR-006：instrument 抛异常时 record_* 静默吞掉（逐路径真实触发）。"""
    _enabled(monkeypatch)

    class _Boom:
        def add(self, *a, **kw):
            raise RuntimeError("boom")

        def record(self, *a, **kw):
            raise RuntimeError("boom")

    originals = dict(telemetry._instruments)

    def _swap_then_restore(key: str, calls) -> None:
        telemetry._instruments[key] = _Boom()
        try:
            calls()
        finally:
            telemetry._instruments.update(originals)

    # 摄取计数 add 路径 / 摄取耗时 record 路径 / llm record 路径
    _swap_then_restore("ingestion_pull", lambda: telemetry.record_ingestion(cm.STATUS_OK, 0.1))
    _swap_then_restore("ingestion_pull_duration", lambda: telemetry.record_ingestion(cm.STATUS_OK, 0.1))
    _swap_then_restore("llm_duration", lambda: telemetry.record_llm_call("m", "a", "completed", 1.0, None))


# ── 契约指标产出 ───────────────────────────────────────────


def test_facade_emits_contract_metrics(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)

    telemetry.record_llm_call(
        "gpt-test", "eval-agent", cm.STATUS_FAILED, 2.5, {"prompt_tokens": 8},
    )
    telemetry.record_tool_call("read_evidence", cm.STATUS_COMPLETED, 0.15)
    telemetry.record_run_start(cm.WORKLOAD_EVALUATION, "trace-e1")
    telemetry.record_run_terminal(cm.WORKLOAD_EVALUATION, cm.STATUS_COMPLETED, 30.0, "trace-e1")
    telemetry.record_run_terminal(cm.WORKLOAD_EVOLUTION, cm.STATUS_FAILED, 60.0, "trace-x1")
    telemetry.record_ingestion(cm.STATUS_OK, 0.35)

    by_name = _metrics_by_name(reader)

    token_points = {
        p.attributes[cm.LABEL_DIRECTION]: p.value for p in by_name[cm.LLM_TOKENS_USAGE]
    }
    assert token_points == {"input": 8}

    run_attrs = {
        (p.attributes[cm.LABEL_WORKLOAD], p.attributes[cm.LABEL_STATUS])
        for p in by_name[cm.AGENT_RUN_DURATION]
    }
    assert run_attrs == {
        (cm.WORKLOAD_EVALUATION, cm.STATUS_COMPLETED),
        (cm.WORKLOAD_EVOLUTION, cm.STATUS_FAILED),
    }

    # last_success：completed 刷新；failed 不刷新；ingestion 成功刷新
    last_success_workloads = {
        p.attributes[cm.LABEL_WORKLOAD] for p in by_name[cm.RUN_LAST_SUCCESS]
    }
    assert cm.WORKLOAD_EVALUATION in last_success_workloads
    assert cm.WORKLOAD_INGESTION in last_success_workloads
    assert cm.WORKLOAD_EVOLUTION not in last_success_workloads

    assert by_name[cm.INGESTION_PULL][0].attributes == {cm.LABEL_STATUS: cm.STATUS_OK}
    assert by_name[cm.INGESTION_PULL_DURATION][0].attributes == {
        cm.LABEL_STATUS: cm.STATUS_OK,
    }
    # 在途配对：trace-e1 有配对归零；trace-x1 未见 start，不 -1
    active_eval = [p.value for p in by_name[cm.AGENT_RUN_ACTIVE]
                   if p.attributes == {cm.LABEL_WORKLOAD: cm.WORKLOAD_EVALUATION}]
    assert active_eval == [0]


# ── 摄取指标：同步刷新不刷 last_success（FR-007 告警③语义）──


def test_record_ingestion_sync_refresh_does_not_touch_last_success(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    telemetry.record_ingestion(cm.STATUS_OK, 0.2, touch_last_success=False)
    by_name = _metrics_by_name(reader)
    assert cm.INGESTION_PULL in by_name  # 计数照记
    assert cm.RUN_LAST_SUCCESS not in by_name  # 但不刷新「最近成功」


# ── drain gauge 启用态 ────────────────────────────────────


def test_drain_gauge_emits_when_enabled(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    telemetry.register_drain_depth_gauge("evolution", lambda: 4)
    by_name = _metrics_by_name(reader)
    points = by_name.get(cm.TRACE_DRAIN_QUEUE_DEPTH, [])
    assert points and points[0].value == 4
    assert points[0].attributes == {cm.LABEL_SERVICE: "evolution"}


# ── evolution recorder._emit_metrics 映射（workload 查找）────


def test_emit_metrics_maps_evolution_workloads(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)

    from app.core.models import TraceLogEvent
    from app.trace.recorder import _emit_metrics

    def ev(**kw):
        base = dict(
            trace_id="t-evo", event_id="e", sequence=1, type="run_start",
            status="running", timestamp="now", source="system", schema_version=2,
        )
        base.update(kw)
        return TraceLogEvent(**base)

    # workload 显式传入（recorder 从 _run_workloads 查出）；每个 run 用独立
    # trace_id，避免在途配对（已见 run_start 的 trace_id 集合）被同 id 覆盖。
    _emit_metrics(ev(trace_id="t-eval"), cm.WORKLOAD_EVALUATION)
    _emit_metrics(ev(trace_id="t-evo", event_id="e2"), cm.WORKLOAD_EVOLUTION)
    _emit_metrics(ev(trace_id="t-adopted", event_id="e3", type="run_end", status="completed", duration_ms=60000), "unknown")

    by_name = _metrics_by_name(reader)
    workloads = {p.attributes[cm.LABEL_WORKLOAD] for p in by_name[cm.AGENT_RUN_ACTIVE]}
    assert workloads == {cm.WORKLOAD_EVALUATION, cm.WORKLOAD_EVOLUTION}
    run_status = {(p.attributes[cm.LABEL_WORKLOAD], p.attributes[cm.LABEL_STATUS])
                  for p in by_name[cm.AGENT_RUN_DURATION]}
    assert ("unknown", cm.STATUS_COMPLETED) in run_status


# ── forget_run：心跳超时等不经事件流的终态收敛（review 整改）──


def test_forget_run_collapses_paired_active(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    telemetry.record_run_start(cm.WORKLOAD_EVALUATION, "t-hb")
    telemetry.forget_run(cm.WORKLOAD_EVALUATION, "t-hb")
    by_name = _metrics_by_name(reader)
    active = [p.value for p in by_name[cm.AGENT_RUN_ACTIVE]
              if p.attributes == {cm.LABEL_WORKLOAD: cm.WORKLOAD_EVALUATION}]
    assert active == [0]  # +1 后被 forget_run 收敛归零


def test_forget_run_ignores_unknown_trace(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    telemetry.forget_run("unknown", "t-never-seen")  # 无配对：不 -1，不抛错
    by_name = _metrics_by_name(reader)
    assert cm.AGENT_RUN_ACTIVE not in by_name


# ── 摄取包装真实路径：四状态 + 异常重抛（review 整改补测）────


def _patch_ingest(monkeypatch, *, fetched, ingest_result):
    from app.ingestion import ingestion as ing

    monkeypatch.setattr(ing, "_load_prior_events", lambda tid: ([], 0))
    monkeypatch.setattr(ing, "_fetch_trace_content", lambda tid, since, tp=None: fetched)
    monkeypatch.setattr(ing, "_sync_status_only", lambda tid, tp=None: None)
    monkeypatch.setattr(ing.importer, "ingest_events", lambda *a, **kw: ingest_result)
    monkeypatch.setattr(ing.db, "query_one", lambda q, p: None)
    return ing


def test_ingest_now_no_content(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    ing = _patch_ingest(monkeypatch, fetched=None, ingest_result=None)
    assert ing.ingest_trace_now("t-nc") is None
    by_name = _metrics_by_name(reader)
    assert by_name[cm.INGESTION_PULL][0].attributes == {cm.LABEL_STATUS: cm.STATUS_NO_CONTENT}


def test_ingest_now_sync_only_no_touch(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    # since_seq>0 且无新事件 → 状态同步路径：ok 但不刷 last_success
    from app.ingestion import ingestion as ing

    run_summary = SimpleNamespace(workspace_id="ws", status="running")
    monkeypatch.setattr(ing, "_load_prior_events", lambda tid: ([], 5))
    monkeypatch.setattr(ing, "_fetch_trace_content", lambda tid, since, tp=None: ([], run_summary, {}))
    monkeypatch.setattr(ing, "_sync_status_only", lambda tid, tp=None: None)
    assert ing.ingest_trace_now("t-sync") == "t-sync"
    by_name = _metrics_by_name(reader)
    assert by_name[cm.INGESTION_PULL][0].attributes == {cm.LABEL_STATUS: cm.STATUS_OK}
    assert cm.RUN_LAST_SUCCESS not in by_name


def test_ingest_now_ok_touches_last_success(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    ing = _patch_ingest(monkeypatch, fetched=([object()], SimpleNamespace(workspace_id="ws", status="completed"), {}), ingest_result="t-ok")
    assert ing.ingest_trace_now("t-ok") == "t-ok"
    by_name = _metrics_by_name(reader)
    assert by_name[cm.INGESTION_PULL][0].attributes == {cm.LABEL_STATUS: cm.STATUS_OK}
    assert {p.attributes[cm.LABEL_WORKLOAD] for p in by_name[cm.RUN_LAST_SUCCESS]} == {
        cm.WORKLOAD_INGESTION
    }


def test_ingest_now_rejected(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    ing = _patch_ingest(monkeypatch, fetched=([object()], SimpleNamespace(workspace_id="ws", status="completed"), {}), ingest_result=None)
    assert ing.ingest_trace_now("t-rj") is None
    by_name = _metrics_by_name(reader)
    assert by_name[cm.INGESTION_PULL][0].attributes == {cm.LABEL_STATUS: cm.STATUS_REJECTED}


def test_ingest_now_error_reraises_and_records(monkeypatch: pytest.MonkeyPatch):
    reader = _enabled(monkeypatch)
    from app.ingestion import ingestion as ing

    def _boom(tid, since, tp=None):
        raise RuntimeError("fetch boom")

    monkeypatch.setattr(ing, "_load_prior_events", lambda tid: ([], 0))
    monkeypatch.setattr(ing, "_fetch_trace_content", _boom)
    with pytest.raises(RuntimeError):
        ing.ingest_trace_now("t-err")
    by_name = _metrics_by_name(reader)
    assert by_name[cm.INGESTION_PULL][0].attributes == {cm.LABEL_STATUS: cm.STATUS_ERROR}
