"""Shared, replayable Trace usage and successful model-call timing."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel


class TraceMetrics(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    input_partial: bool = False
    output_partial: bool = False
    total_partial: bool = False
    average_duration_ms: float | None = None


def _get(value: object, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _token(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def normalize_usage(value: object) -> dict[str, int | None]:
    """Keep zero distinct from missing; derive a total only from both known parts."""
    input_value = _get(value, "input_tokens")
    output_value = _get(value, "output_tokens")
    input_tokens = _token(input_value if input_value is not None else _get(value, "prompt_tokens"))
    output_tokens = _token(output_value if output_value is not None else _get(value, "completion_tokens"))
    total_tokens = _token(_get(value, "total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}


def usage_from_output(output: object) -> dict[str, int | None] | None:
    """Read already-inline legacy usage without fetching any referenced payload."""
    messages = _get(output, "messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        usage = message.get("usage") or message.get("usage_metadata")
        if isinstance(usage, Mapping):
            return normalize_usage(usage)
    return None


def _duration(value: object, status: str) -> float | None:
    if status != "completed" or value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


class TraceMetricsAccumulator:
    """Keep compact call accounting, never model input/output payloads."""

    def __init__(self) -> None:
        self._samples: dict[str, tuple[int | None, int | None, int | None, float | None]] = {}
        self._totals = [0, 0, 0]
        self._known = [0, 0, 0]
        self._duration_sum = 0.0
        self._duration_count = 0
        self._seen_starts: set[str] = set()
        self._open_calls: dict[str, set[str]] = {}
        self._open_count = 0

    def _add_sample(self, key: str, usage: object, duration: float | None) -> None:
        normalized = normalize_usage(usage)
        sample = (normalized["input_tokens"], normalized["output_tokens"], normalized["total_tokens"], duration)
        previous = self._samples.get(key)
        if previous == sample:
            return
        for contribution, direction in ((previous, -1), (sample, 1)):
            if contribution is None:
                continue
            for index, value in enumerate(contribution[:3]):
                if value is not None:
                    self._totals[index] += direction * value
                    self._known[index] += direction
            if contribution[3] is not None:
                self._duration_sum += direction * contribution[3]
                self._duration_count += direction
        self._samples[key] = sample

    def add_event(self, event: object) -> None:
        event_type = _get(event, "type")
        if event_type not in {"llm_start", "llm_end", "llm_error"}:
            return
        event_id = str(_get(event, "event_id"))
        tool_call_id = _get(event, "tool_call_id")
        run_id = _get(event, "run_id")
        if tool_call_id:
            key = f"tool_call:{tool_call_id}"
        elif run_id:
            key = f"run:{run_id}"
        else:
            key = f"llm:{_get(event, 'agent_name') or 'unknown'}:{_get(event, 'model_name') or 'unknown'}"
        if event_type == "llm_start":
            if event_id not in self._seen_starts:
                self._seen_starts.add(event_id)
                self._open_calls.setdefault(key, set()).add(event_id)
                self._open_count += 1
            return
        if event_id not in self._samples:
            pending = self._open_calls.get(key)
            if pending:
                pending.pop()
                self._open_count -= 1
                if not pending:
                    del self._open_calls[key]
        status = _get(event, "status")
        usage = _get(event, "usage")
        if usage is None:
            usage = usage_from_output(_get(event, "output"))
        self._add_sample(
            event_id,
            usage,
            _duration(_get(event, "duration_ms"), status),
        )

    def snapshot(self, terminal: bool = False) -> TraceMetrics:
        count = len(self._samples) + (self._open_count if terminal else 0)
        totals = [total if known else None for total, known in zip(self._totals, self._known)]
        partial = [0 < known < count for known in self._known]
        return TraceMetrics(
            input_tokens=totals[0], output_tokens=totals[1], total_tokens=totals[2],
            input_partial=partial[0], output_partial=partial[1], total_partial=partial[2],
            average_duration_ms=self._duration_sum / self._duration_count if self._duration_count else None,
        )


def metrics_from_nodes(nodes: Iterable[object]) -> TraceMetrics:
    accumulator = TraceMetricsAccumulator()
    for node in nodes:
        kind = _get(node, "kind")
        node_id = str(_get(node, "node_id"))
        llm_error = kind == "error" and not _get(node, "tool_name") and (
            node_id.startswith("llm:") or _get(node, "model_name")
        )
        status = _get(node, "status")
        if (kind != "llm" and not llm_error) or status in {"pending", "running", "awaiting_input", "cancelling"}:
            continue
        usage = _get(node, "usage")
        if usage is None:
            usage = {"input_tokens": _get(node, "usage_input"), "output_tokens": _get(node, "usage_output"),
                     "total_tokens": _get(node, "usage_total")}
        accumulator._add_sample(node_id, usage, _duration(_get(node, "duration_ms"), status))
    return accumulator.snapshot()
