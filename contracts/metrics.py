"""基建指标契约（需求 REQ-20260918-144856 FR-001 附件）。

双端（executor / evolution）共享的指标名与标签键单一真源。
只放常量与纯映射函数，不 import opentelemetry——contracts 层保持零依赖。

命名遵循 OTel 语义约定：点分层命名；Collector → Prometheus 转换时
点变下划线并追加单位/类型后缀（如 writer.llm.call.duration →
writer_llm_call_duration_seconds）。L1/L2 指标由 OTel 自动埋点产生
（http.server.* / process.*），不在此声明。

基数红线（FR-003）：所有标签值域受控；user_id / thread_id / trace_id
永不作为标签。
"""

from __future__ import annotations

from collections.abc import Mapping

# ── L3：Agent 业务语义指标 ──────────────────────────────────
LLM_CALL_DURATION = "writer.llm.call.duration"
LLM_TOKENS_USAGE = "writer.llm.tokens.usage"
LLM_ATTEMPT_FAILED = "writer.llm.attempt.failed"
TOOL_CALL_DURATION = "writer.tool.call.duration"
MIDDLEWARE_INTERVENTION = "writer.middleware.intervention"
AGENT_RUN_DURATION = "writer.agent.run.duration"
AGENT_RUN_ACTIVE = "writer.agent.run.active"

# ── L4：流水线指标（evolution 为主，executor 的 workload=creation 复用）──
INGESTION_PULL = "writer.ingestion.pull"
INGESTION_PULL_DURATION = "writer.ingestion.pull.duration"
RUN_LAST_SUCCESS = "writer.run.last_success"

# ── 观测自身健康 ────────────────────────────────────────────
TRACE_DRAIN_QUEUE_DEPTH = "writer.trace.drain.queue.depth"

# ── SSE（executor 专用，FR-008）────────────────────────────
SSE_TTFB = "writer.sse.time.to_first_byte"

# ── 标签键 ─────────────────────────────────────────────────
LABEL_MODEL = "model"
LABEL_AGENT = "agent"
LABEL_STATUS = "status"
LABEL_DIRECTION = "direction"
LABEL_ERROR_CLASS = "error_class"
LABEL_TOOL = "tool"
LABEL_ACTION = "action"
LABEL_WORKLOAD = "workload"
LABEL_ROUTE = "route"
LABEL_SERVICE = "service"

# ── workload 值域（与 contracts.trace.TraceWorkload 对齐）────
WORKLOAD_CREATION = "creation"
WORKLOAD_EVALUATION = "evaluation"
WORKLOAD_EVOLUTION = "evolution"
WORKLOAD_INGESTION = "ingestion"  # 仅 RUN_LAST_SUCCESS 使用（摄取不是 trace workload）

# ── 状态值域 ───────────────────────────────────────────────
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_ERROR = "error"
STATUS_OK = "ok"
STATUS_NO_CONTENT = "no_content"  # 摄取：拉不到内容
STATUS_REJECTED = "rejected"      # 摄取：内容被导入器拒绝

# token 方向值域（llm.tokens.usage 的 direction 标签）
DIRECTION_INPUT = "input"
DIRECTION_OUTPUT = "output"

# run 终态事件类型（两套 recorder 共用口径）。
# cancel_timeout：取消未在截止期内收敛（EDGE-007 技术故障，按 failed 口径）。
TERMINAL_RUN_EVENTS = frozenset({"run_end", "run_error", "run_cancelled", "cancel_timeout"})

# 终态事件 → status 标签值。
_TERMINAL_STATUS = {
    "run_end": STATUS_COMPLETED,
    "run_error": STATUS_FAILED,
    "run_cancelled": STATUS_CANCELLED,
    "cancel_timeout": STATUS_FAILED,
}


def terminal_run_status(event_type: str) -> str | None:
    """run 终态事件 → status 标签值；非终态事件返回 None。"""
    return _TERMINAL_STATUS.get(event_type)


def usage_tokens(usage: object) -> tuple[int, int]:
    """从 trace 事件 usage 字段提取 (input_tokens, output_tokens)。

    兼容三种形态：
    - contracts.trace.TraceUsage（TraceLogEvent.usage 经 pydantic 强转后的实际形态）
    - dict，键名 input_tokens/prompt_tokens 与 output_tokens/completion_tokens
      （不同 SDK 版本差异）
    缺失或非数字时该侧记 0。
    """
    if not isinstance(usage, Mapping):
        usage = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }

    def _pick(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return 0

    return (
        _pick("input_tokens", "prompt_tokens", "input_token_count"),
        _pick("output_tokens", "completion_tokens", "output_token_count"),
    )


def error_class_from_reason(reason: str) -> str:
    """从 intervention reason（"attempt 1/2:TimeoutError"）提取错误类名。

    取最后一个冒号后的片段；无冒号时截断到 64 字符（值域保护）。
    """
    head, sep, tail = reason.rpartition(":")
    value = tail if sep and tail else reason
    return value[:64] or "unknown"


def bounded_label(value: object, *, max_length: int = 64) -> str:
    """任意标签值 → 受控形态：None/空 → unknown，超长截断。

    基数红线（FR-003）的统一落点：所有自定义标签值入指标前必须经过此处。
    """
    text = str(value) if value else "unknown"
    return text[:max_length] or "unknown"
