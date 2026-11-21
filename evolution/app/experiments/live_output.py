"""Bounded, process-local output for in-progress experiment model calls."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Literal

Channel = Literal["reasoning", "text"]
MAX_DELTA_CHARS = 4096
_MAX_CHANNEL_CHARS = 24000
_MAX_CALLS = 8
_MAX_TRACES = 64
_FRAME_LIMIT = 192
_RETENTION_SECONDS = 120


@dataclass
class _Call:
    run_id: str
    reasoning: str = ""
    text: str = ""
    finished: bool = False


@dataclass
class _Trace:
    updated: float
    calls: dict[str, _Call] = field(default_factory=dict)
    frames: deque[tuple[int, str]] = field(default_factory=lambda: deque(maxlen=_FRAME_LIMIT))
    writing_sample: tuple[str, str] | None = None


_traces: dict[str, _Trace] = {}
_lock = Lock()
_cursor = 0


def frame(name: str, data: dict) -> str:
    # The desktop relay may split a UTF-8 sequence between network chunks.
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=True, separators=(',', ':'))}\n\n"


def _get_or_create(trace_id: str) -> _Trace:
    trace = _traces.get(trace_id)
    if trace is None:
        if len(_traces) >= _MAX_TRACES:
            oldest = min(_traces, key=lambda key: _traces[key].updated)
            del _traces[oldest]
        trace = _traces[trace_id] = _Trace(time.monotonic())
    return trace


def _append(trace: _Trace, name: str, data: dict) -> None:
    global _cursor
    _cursor += 1
    trace.updated = time.monotonic()
    trace.frames.append((_cursor, frame(name, data)))


def bind_writing_trace(trace_id: str, experiment_id: str, sample_id: str) -> bool:
    source = (experiment_id, sample_id)
    with _lock:
        trace = _get_or_create(trace_id)
        if trace.writing_sample not in (None, source):
            return False
        trace.writing_sample = source
        return True


def writing_sample(trace_id: str) -> tuple[str, str] | None:
    with _lock:
        trace = _traces.get(trace_id)
        return trace.writing_sample if trace else None


def publish_delta(trace_id: str, run_id: str, channel: Channel, text: str) -> None:
    if not text or not run_id:
        return
    text = text[-MAX_DELTA_CHARS:]
    with _lock:
        trace = _get_or_create(trace_id)
        call = trace.calls.get(run_id)
        if call is None:
            if len(trace.calls) >= _MAX_CALLS:
                del trace.calls[next(iter(trace.calls))]
            call = trace.calls[run_id] = _Call(run_id)
        if call.finished:
            return
        if channel == "reasoning":
            call.reasoning = (call.reasoning + text)[-_MAX_CHANNEL_CHARS:]
        else:
            call.text = (call.text + text)[-_MAX_CHANNEL_CHARS:]
        _append(trace, "delta", {"run_id": run_id, "channel": channel, "text": text})


def finish_call(trace_id: str, run_id: str) -> None:
    with _lock:
        trace = _traces.get(trace_id)
        call = trace.calls.get(run_id) if trace else None
        if trace and call and not call.finished:
            call.finished = True
            _append(trace, "end", {"run_id": run_id})


def discard(trace_id: str) -> None:
    with _lock:
        _traces.pop(trace_id, None)


def read(trace_id: str, after: int | None) -> tuple[int, list[str]]:
    with _lock:
        trace = _traces.get(trace_id)
        if after is None or (trace and trace.frames and after < trace.frames[0][0] - 1):
            return _cursor, [frame("snapshot", {"calls": [asdict(call) for call in trace.calls.values()] if trace else []})]
        return _cursor, [value for cursor, value in trace.frames if cursor > after] if trace else []


def expire() -> None:
    cutoff = time.monotonic() - _RETENTION_SECONDS
    with _lock:
        for trace_id in [key for key, trace in _traces.items() if trace.updated < cutoff]:
            del _traces[trace_id]


async def reap() -> None:
    while True:
        await asyncio.sleep(10)
        expire()
