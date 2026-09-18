"""OTel 基建指标埋点（FR-001 / FR-006 / DEC-007）。

开关：环境变量 OTEL_EXPORTER_OTLP_ENDPOINT（docker compose 注入；
本地开发不设置 = 本模块全部函数零开销直通，见 FR-001 埋点开关条款）。

架构（DEC-003 / DEC-007）：
- 埋点留在业务进程内（dsh 哲学：采集不出进程）；
- 指标经 OTLP/HTTP 推送至 otel-collector，Prometheus 30s 抓取 collector；
- 全部导出由 SDK 异步批量（30s 周期，与抓取对齐让 AC-003 的 60s 窗口可达成），
  任何观测失败静默记日志，绝不打断业务主流程（FR-006，与 trace 中间件
  「观测失败吞掉」同原则）。

指标名与标签键的单一真源在 contracts/metrics.py（本模块不硬编码名字）。
标签值入指标前统一经 cm.bounded_label 清洗（FR-003 基数红线）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from contracts import metrics as cm

logger = logging.getLogger("executor.telemetry")

EXPORT_INTERVAL_MS = 30_000

# 模块级状态：未 setup（_instruments is None）时所有 record_* 直通返回（零开销）。
_instruments: dict[str, Any] | None = None
_provider: Any = None  # 持有引用供测试 shutdown；不 set_meter_provider（避免全局单例污染）
_drain_gauge_lock = threading.Lock()
_drain_gauge_bound = False
# run 在途配对（review fp-run-active-drift）：已见 run_start 的 trace_id 集合。
# 只在进程内见过 run_start 的 trace 才允许终态 -1，防止重启/收养路径把
# 在途计数打成负数。随终态移除，容量有界（≤ 活跃 trace 数）。
_active_trace_ids: set[str] = set()
_active_lock = threading.Lock()


def is_enabled() -> bool:
    """指标埋点是否已启用（setup 成功且 endpoint 已配置）。"""
    return _instruments is not None


def is_sse_response(response: Any) -> bool:
    """响应是否为 SSE 流（FR-008）。

    BaseHTTPMiddleware 的 call_next 响应对象 media_type 属性为 None
    （content-type 只在 headers），必须查 header 而非属性。
    """
    if response is None:
        return False
    try:
        content_type = response.headers.get("content-type", "")
    except Exception:
        return False
    return content_type.startswith("text/event-stream")


def setup_telemetry(
    service_name: str,
    *,
    app: Any = None,
    metric_reader: Any = None,
    system_metrics: bool = True,
) -> bool:
    """初始化 OTel 指标管线。幂等；任何失败静默降级（FR-006）。

    Args:
        service_name: Resource service.name（executor / evolution）。
        app: FastAPI 实例；非 None 时挂 L1 自动埋点（http.server.*）。
        metric_reader: 测试注入自定义 reader（如 InMemoryMetricReader）；
            None 时用 PeriodicExportingMetricReader + OTLP/HTTP 导出器。
        system_metrics: 是否挂 L2 进程指标（测试注入 reader 时传 False 跳过）。
    Returns:
        True 已启用；False 未配置 endpoint 或初始化失败（全部函数将直通）。
    """
    global _instruments, _provider
    if _instruments is not None:
        return True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT 未配置，指标埋点静默关闭")
        return False

    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        if metric_reader is None:
            exporter = OTLPMetricExporter(endpoint=f"{endpoint.rstrip('/')}/v1/metrics")
            metric_reader = PeriodicExportingMetricReader(
                exporter, export_interval_millis=EXPORT_INTERVAL_MS
            )
        _provider = MeterProvider(
            resource=Resource.create({"service.name": service_name}),
            metric_readers=[metric_reader],
        )
        meter = _provider.get_meter("writer.telemetry")

        _instruments = {
            "llm_duration": meter.create_histogram(cm.LLM_CALL_DURATION, unit="s"),
            "llm_tokens": meter.create_counter(cm.LLM_TOKENS_USAGE),
            "llm_attempt_failed": meter.create_counter(cm.LLM_ATTEMPT_FAILED),
            "tool_duration": meter.create_histogram(cm.TOOL_CALL_DURATION, unit="s"),
            "intervention": meter.create_counter(cm.MIDDLEWARE_INTERVENTION),
            "run_duration": meter.create_histogram(cm.AGENT_RUN_DURATION, unit="s"),
            "run_active": meter.create_up_down_counter(cm.AGENT_RUN_ACTIVE),
            "run_last_success": meter.create_gauge(cm.RUN_LAST_SUCCESS, unit="s"),
            "sse_ttfb": meter.create_histogram(cm.SSE_TTFB, unit="s"),
        }

        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor().instrument_app(app, meter_provider=_provider)
            # instrument_app 只替换 build_middleware_stack；lifespan 启动时栈已
            # 构建完成（首个请求/lifespan scope 触发过 __call__），必须强制重建
            # 才能把 OTel 中间件装进活栈——否则 L1 指标静默死亡。
            # startup 期无在途请求，重建安全。
            app.middleware_stack = app.build_middleware_stack()

        if system_metrics:
            from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor

            SystemMetricsInstrumentor().instrument(meter_provider=_provider)

        logger.info("OTel 指标埋点已启用：service=%s endpoint=%s", service_name, endpoint)
        return True
    except Exception:
        logger.exception("OTel 初始化失败，指标埋点关闭（不影响业务）")
        if _provider is not None:
            try:
                _provider.shutdown()
            except Exception:
                pass
        _provider = None
        _instruments = None
        return False


def register_drain_depth_gauge(service: str, getter: Callable[[], int]) -> None:
    """注册 trace 写盘队列深度观测（FR-001 L4「观测自身健康」）。

    getter 由 recorder 提供（pending_writes_depth），每次采集周期回调读取。
    未启用埋点时 no-op；重复调用幂等。回调返回 Observation 列表
    （OTel Python 回调契约：return Iterable[Observation]，无 options.observe）。
    """
    global _drain_gauge_bound
    if _instruments is None:
        return
    with _drain_gauge_lock:
        if _drain_gauge_bound:
            return
        try:
            from opentelemetry.metrics import Observation

            meter = _provider.get_meter("writer.telemetry.observable")

            def _observe(options: Any) -> list[Observation]:
                try:
                    depth = getter()
                except Exception:
                    return []  # 观测自身失败必须静默（FR-006）
                return [
                    Observation(max(int(depth), 0), {cm.LABEL_SERVICE: service})
                ]

            meter.create_observable_gauge(
                name=cm.TRACE_DRAIN_QUEUE_DEPTH,
                callbacks=[_observe],
            )
            _drain_gauge_bound = True
        except Exception:
            logger.exception("注册 drain 深度指标失败（不影响业务）")


def _touch_last_success(workload: str) -> None:
    """完成态刷新「最近成功」时间戳（unix 秒）。"""
    _instruments["run_last_success"].set(time.time(), {cm.LABEL_WORKLOAD: workload})


# ── L3：Agent 业务语义（数据来自 recorder 事件，FR-001）─────────


def record_llm_call(
    model: str, agent: str, status: str, duration_s: float, usage: Any
) -> None:
    """一次模型调用的耗时与 token（llm_end / llm_error 事件触发）。"""
    if _instruments is None:
        return
    try:
        model = cm.bounded_label(model)
        _instruments["llm_duration"].record(
            duration_s,
            {cm.LABEL_MODEL: model, cm.LABEL_AGENT: cm.bounded_label(agent),
             cm.LABEL_STATUS: status},
        )
        input_tokens, output_tokens = cm.usage_tokens(usage)
        if input_tokens:
            _instruments["llm_tokens"].add(
                input_tokens, {cm.LABEL_MODEL: model, cm.LABEL_DIRECTION: cm.DIRECTION_INPUT}
            )
        if output_tokens:
            _instruments["llm_tokens"].add(
                output_tokens, {cm.LABEL_MODEL: model, cm.LABEL_DIRECTION: cm.DIRECTION_OUTPUT}
            )
    except Exception:
        logger.debug("record_llm_call 失败（静默）", exc_info=True)


def record_tool_call(tool: str, status: str, duration_s: float) -> None:
    """一次工具调用的耗时（tool_end / tool_error 事件触发）。"""
    if _instruments is None:
        return
    try:
        _instruments["tool_duration"].record(
            duration_s,
            {cm.LABEL_TOOL: cm.bounded_label(tool), cm.LABEL_STATUS: status},
        )
    except Exception:
        logger.debug("record_tool_call 失败（静默）", exc_info=True)


def record_intervention(action: str) -> None:
    """一次中间件治理干预（middleware_intervention 事件触发）。"""
    if _instruments is None:
        return
    try:
        _instruments["intervention"].add(1, {cm.LABEL_ACTION: cm.bounded_label(action)})
    except Exception:
        logger.debug("record_intervention 失败（静默）", exc_info=True)


def record_llm_attempt_failed(model: str, error_class: str) -> None:
    """一次模型传输 attempt 失败（intervention action=model_attempt_failed 触发）。"""
    if _instruments is None:
        return
    try:
        _instruments["llm_attempt_failed"].add(
            1,
            {
                cm.LABEL_MODEL: cm.bounded_label(model),
                cm.LABEL_ERROR_CLASS: cm.bounded_label(error_class),
            },
        )
    except Exception:
        logger.debug("record_llm_attempt_failed 失败（静默）", exc_info=True)


# ── 任务级（run_start / run 终态事件触发）────────────────────


def record_run_start(workload: str, trace_id: str = "") -> None:
    if _instruments is None:
        return
    try:
        _instruments["run_active"].add(1, {cm.LABEL_WORKLOAD: workload})
        if trace_id:
            with _active_lock:
                _active_trace_ids.add(trace_id)
    except Exception:
        logger.debug("record_run_start 失败（静默）", exc_info=True)


def record_run_terminal(workload: str, status: str, duration_s: float, trace_id: str = "") -> None:
    """run 终态：耗时直方图 + 在途 -1 + 完成时刷新 last_success 时间戳。

    在途 -1 只在进程内见过对应 run_start 时执行（trace_id 配对）——重启/
    收养路径产生的终态事件没有配对 +1，直接 -1 会把在途计数打成负数。
    """
    if _instruments is None:
        return
    try:
        attrs = {cm.LABEL_WORKLOAD: workload, cm.LABEL_STATUS: status}
        # 配对/清理先于任何 instrument 调用：record 抛异常也不得让 trace_id
        # 残留集合或丢掉 -1（review 整改：守护次序加固）。
        with _active_lock:
            paired = (not trace_id) or (trace_id in _active_trace_ids)
            _active_trace_ids.discard(trace_id)
        _instruments["run_duration"].record(duration_s, attrs)
        if paired:
            _instruments["run_active"].add(-1, {cm.LABEL_WORKLOAD: workload})
        if status == cm.STATUS_COMPLETED:
            _touch_last_success(workload)
    except Exception:
        logger.debug("record_run_terminal 失败（静默）", exc_info=True)


def forget_run(workload: str, trace_id: str) -> None:
    """不经事件流的终态（如心跳超时直接 SQL 标 interrupted）：仅收敛在途配对。

    正常 run 终态走 record_run_terminal（含耗时直方图）；本函数只处理绕过
    事件流的收尾，防止在途计数与配对集合漂移（FR-001/FR-006，review 整改）。
    """
    if _instruments is None or not trace_id:
        return
    try:
        with _active_lock:
            paired = trace_id in _active_trace_ids
            _active_trace_ids.discard(trace_id)
        if paired:
            _instruments["run_active"].add(-1, {cm.LABEL_WORKLOAD: workload})
    except Exception:
        logger.debug("forget_run 失败（静默）", exc_info=True)


# ── SSE 首字节（FR-008，executor 专用）──────────────────────


def record_sse_ttfb(route: str, seconds: float) -> None:
    if _instruments is None:
        return
    try:
        _instruments["sse_ttfb"].record(
            max(seconds, 0.0), {cm.LABEL_ROUTE: cm.bounded_label(route, max_length=80)}
        )
    except Exception:
        logger.debug("record_sse_ttfb 失败（静默）", exc_info=True)


# ── 测试钩子（仅测试使用；生产代码不得调用）────────────────


def shutdown() -> None:
    """进程退出前 flush 余量指标（lifespan 收尾调用）。

    不 shutdown 会丢最后一个导出周期（≤30s）内的样本（review 二轮残留整改）。
    """
    global _instruments, _provider
    provider, _provider = _provider, None
    _instruments = None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.debug("telemetry shutdown 失败（静默）", exc_info=True)


def _reset_for_tests() -> None:
    """测试隔离：关闭埋点并停掉已有 provider。"""
    global _instruments, _provider, _drain_gauge_bound
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:
            pass
    _instruments = None
    _provider = None
    _drain_gauge_bound = False
    _active_trace_ids.clear()
