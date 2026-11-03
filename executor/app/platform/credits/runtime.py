"""Operation-scoped billing and generation owned by the server, never by an SSE subscriber."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .ledger import BillingError, BillingLedger, FINAL, model_key

logger = logging.getLogger(__name__)
_current: ContextVar[BillingContext | None] = ContextVar("writer_billing", default=None)


class BudgetExhausted(BillingError):
    def __init__(self):
        super().__init__("本次预算已用尽，已有内容已保存。补分或调整预算后可继续。", "budget_exhausted", 402)


class OperationStopped(BillingError):
    def __init__(self):
        super().__init__("操作已停止，按已用积分结算。", "user_stopped", 409)


def current_billing():
    return _current.get()


def _response_nodes(response):
    def get(value, key, default=None):
        return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
    pending = [response]
    seen = set()
    while pending:
        value = pending.pop()
        if value is None or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, (list, tuple)):
            pending.extend(value)
            continue
        yield value
        for key in ("result", "messages", "response_metadata", "token_usage", "usage", "usage_metadata"):
            child = get(value, key)
            if child is not None:
                pending.append(child)


def response_usage(response) -> dict:
    candidates = [value for value in _response_nodes(response) if isinstance(value, dict)
                  and any(key in value for key in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens"))]
    normalized = [value for value in candidates if "input_tokens" in value or "output_tokens" in value]
    ordered = normalized + [value for value in candidates if value not in normalized]
    def first(*keys):
        return next((value[key] for value in ordered for key in keys if value.get(key) is not None), None)
    caches = []
    for value in candidates:
        details = value.get("input_token_details") or value.get("prompt_tokens_details") or {}
        for source, key in ((value, "prompt_cache_hit_tokens"), (value, "cached_tokens"),
                            (details, "cache_read"), (details, "cached_tokens")):
            if key in source and source[key] is not None:
                caches.append(source[key])
    # A normalized usage object may omit provider-specific cache evidence; never discard a real hit.
    valid_caches = [value for value in caches if isinstance(value, int) and not isinstance(value, bool) and value >= 0]
    cached = max(valid_caches) if valid_caches else (None if caches or not candidates else 0)
    return {"input_tokens": first("input_tokens", "prompt_tokens"),
            "output_tokens": first("output_tokens", "completion_tokens"), "cached_tokens": cached}


class BillingContext:
    def __init__(self, ledger: BillingLedger, operation_id: str):
        self.ledger, self.operation_id = ledger, operation_id
        self.prices = ledger.operation(operation_id)["prices"]
        self.exhausted = False
        self.cancelled = threading.Event()

    def __enter__(self):
        self._token = _current.set(self)
        return self

    def __exit__(self, *args):
        _current.reset(self._token)

    def before_call(self, model):
        self.check_cancelled()
        if model_key(model) not in self.prices:
            raise BillingError("当前模型未配置本次操作的积分系数", "model_unpriced", 409)
        if self.ledger.exhausted(self.operation_id):
            self.exhausted = True
            raise BudgetExhausted()

    def record(self, response, call_id, model, *, billable=True):
        self.check_cancelled()
        fault_retry = any(isinstance(value, dict) and value.get("billing_fault_retry") is True
                          for value in _response_nodes(response))
        self.ledger.record_call(self.operation_id, call_id, model,
                                **response_usage(response), billable=billable and not fault_retry)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise OperationStopped()


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def frame_data(frame):
    event, data = None, None
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
            except ValueError:
                pass
    return event, data


@dataclass
class BillingRun:
    operation: dict
    task: asyncio.Task | None = None
    frames: deque = field(default_factory=lambda: deque(maxlen=512))
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    sequence: int = 0
    completed: bool = False
    started: bool = False
    stopped: bool = False
    trace_id: str | None = None
    result: Any = None
    error: str | None = None
    context: BillingContext | None = None

    def emit(self, frame):
        self.sequence += 1
        self.frames.append((self.sequence, frame))
        self.changed.set()


class BillingRuns:
    def __init__(self, ledger):
        self.ledger = ledger
        self.runs: dict[str, BillingRun] = {}
        self.scanner: asyncio.Task | None = None
        self.preparations: dict[str, tuple[str, str, threading.Event]] = {}

    def begin_preparation(self, user_id, thread_id):
        from uuid import uuid4
        key, cancelled = uuid4().hex, threading.Event()
        self.preparations[key] = (user_id, thread_id, cancelled)
        return key, cancelled

    def end_preparation(self, key):
        self.preparations.pop(key, None)

    def submit(self, operation, source):
        key = operation["operation_id"]
        if key in self.runs:
            return self.runs[key]
        run = BillingRun(operation)
        if operation.get("_replay_only") and operation["status"] not in FINAL:
            run.completed = True
            run.emit(sse("billing", {**operation, "status": "processing"}))
            run.task = asyncio.create_task(asyncio.sleep(0))
            return run
        self.runs[key] = run
        if operation["status"] in FINAL:
            saved = self.ledger.operation(key, operation["user_id"])
            if saved.get("result") is not None:
                run.result = saved["result"]
                run.emit(sse(saved.get("result_event") or "final", saved["result"]))
            run.completed = True
            run.emit(sse("billing", operation))
            run.task = asyncio.create_task(asyncio.sleep(0))
        else:
            run.task = asyncio.create_task(self._produce(run, source))
        finished = [key for key, value in self.runs.items() if value.completed]
        for old in finished[:-32]:
            self.runs.pop(old, None)
        return run

    async def _produce(self, run, source):
        key = run.operation["operation_id"]
        run.started = True
        outcome = "failed"
        context = None
        try:
            context = BillingContext(self.ledger, key)
            run.context = context
            self.ledger.started(key)
            run.emit(sse("billing", {**run.operation, "status": "running"}))
            with context:
                async for frame in source():
                    event, data = frame_data(frame)
                    if event == "status" and isinstance(data, dict) and data.get("trace_id"):
                        run.trace_id = data["trace_id"]
                        self.ledger.started(key, run.trace_id)
                    if event in {"final", "interrupt"}:
                        self.ledger.save_result(key, data, event_type=event)
                        outcome = "completed"
                        run.result = data
                    run.emit(frame)
        except asyncio.CancelledError:
            outcome = "stopped" if run.stopped else "failed"
        except BudgetExhausted:
            outcome = "budget_exhausted"
        except OperationStopped:
            outcome = "stopped"
        except Exception:
            logger.exception("创作操作失败 operation=%s", key)
            run.error = "系统故障，本次操作积分退还；已有内容已保存。"
        finally:
            if context is not None and context.exhausted:
                outcome = "budget_exhausted"
                run.emit(sse("credit_exhausted", {"message": str(BudgetExhausted()), "operation_id": key}))
            elif outcome == "failed":
                run.error = run.error or "系统故障，本次操作积分退还；已有内容已保存。"
                run.emit(sse("billing_error", {"message": run.error, "operation_id": key}))
            try:
                result = self.ledger.finish(key, outcome)
            except Exception:
                logger.exception("结算恢复凭据暂不可用 operation=%s", key)
                result = None
            finally:
                run.emit(sse("billing", result or {**run.operation, "status": "processing"}))
                run.completed = True
                run.frames = deque(list(run.frames)[-48:], maxlen=512)
                run.changed.set()

    async def stream(self, run, after=0):
        cursor = after
        while True:
            run.changed.clear()
            frames = [(number, frame) for number, frame in run.frames if number > cursor]
            if frames and frames[0][0] > cursor + 1:
                yield sse("billing_reconcile", {"operation_id": run.operation["operation_id"]})
            for number, frame in frames:
                cursor = number
                yield frame
            if run.completed and cursor >= run.sequence:
                return
            try:
                await asyncio.wait_for(run.changed.wait(), timeout=15)
            except TimeoutError:
                yield ": ping\n\n"

    def request_stop(self, user_id, thread_id, trace_id, before_cancel=None):
        count = 0
        for owner, thread, cancelled in self.preparations.values():
            if owner == user_id and thread == thread_id and not cancelled.is_set():
                cancelled.set()
                count += 1
        for run in self.runs.values():
            if (not run.completed and run.operation["user_id"] == user_id and
                run.operation["thread_id"] == thread_id and
                (trace_id is None or run.trace_id == trace_id or run.trace_id is None)):
                run.stopped = True
                if run.context:
                    run.context.cancelled.set()
                count += 1
                if before_cancel and run.trace_id:
                    before_cancel(run.trace_id)
                if not run.started:
                    self.ledger.finish(run.operation["operation_id"], "stopped")
                    run.completed = True
                    run.changed.set()
                run.task.cancel()
        return count

    def start_recovery(self):
        self.ledger.recover(set())
        async def scan():
            while True:
                await asyncio.sleep(30)
                try:
                    active = {key for key, value in self.runs.items() if not value.completed}
                    self.ledger.recover(active)
                except Exception:
                    logger.exception("积分账目恢复暂不可用，将继续重试")
        self.scanner = asyncio.create_task(scan())

    async def aclose(self):
        for _, _, cancelled in self.preparations.values():
            cancelled.set()
        for run in self.runs.values():
            if not run.completed and run.context:
                run.context.cancelled.set()
        tasks = [run.task for run in self.runs.values() if run.task and not run.task.done()]
        if self.scanner:
            tasks.append(self.scanner)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_runs: BillingRuns | None = None


def init_billing_runs(ledger):
    global _runs
    _runs = BillingRuns(ledger)
    return _runs


def get_billing_runs():
    if _runs is None:
        raise RuntimeError("Billing runtime is not initialized")
    return _runs
