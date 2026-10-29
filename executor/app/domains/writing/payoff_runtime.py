"""Bind volume checks to one request's Skill snapshot and writer model."""
from __future__ import annotations

import copy
import asyncio
import concurrent.futures
import json
import logging
import threading
import time
from pathlib import Path
from uuid import uuid4

from contracts.payoff_constraints import PayoffCatalog, Violation, content_hash

from app.domains.writing.retry import RetryBudget, WriterRetryController
from app.domains.writing.volume_payoff_guard import VolumePayoffGuard

logger = logging.getLogger(__name__)


def make_payoff_model_call(
    model, *, recorder=None, trace_id: str | None = None,
    is_active=None, event_loop=None,
):
    """Use the Run-bound model and the existing transport retry budget."""
    calling_thread = threading.get_ident()
    def call(messages: list[dict], purpose: str):
        def assert_active() -> None:
            if is_active is not None and not is_active():
                raise RuntimeError("本次创作已停止，复核结果未提交。")

        assert_active()
        run_id = str(uuid4())
        started = time.perf_counter()

        def record(event_type: str, status: str, **values) -> None:
            if recorder is None or not trace_id:
                return
            try:
                recorder.append_event(trace_id, {
                    "type": event_type, "status": status, "source": "middleware",
                    "agent_name": "volume-payoff-check", "node_name": purpose,
                    "run_id": run_id, "model_name": getattr(model, "model_name", None),
                    **values,
                })
            except Exception:
                logger.warning("卷纲复核追踪记录失败", exc_info=True)

        record("llm_start", "running", input={"messages": messages})
        budget = RetryBudget(on_attempt_complete=lambda outcome: (
            None if outcome.success else record(
                "middleware_intervention", "running", intervention={
                    "action": "model_attempt_failed", "hook": "volume_payoff_check",
                    "affected_fields": ["control_flow"], "reason": outcome.error_message,
                },
            )
        ))
        try:
            controller = WriterRetryController(budget)
            if (event_loop is not None and threading.get_ident() != calling_thread
                    and callable(getattr(model, "ainvoke", None))):
                async def invoke_async():
                    assert_active()
                    return await model.ainvoke(messages, config={"callbacks": []})

                future = asyncio.run_coroutine_threadsafe(controller.acall(invoke_async), event_loop)
                try:
                    while True:
                        assert_active()
                        try:
                            response = future.result(timeout=0.25)
                            break
                        except concurrent.futures.TimeoutError:
                            continue
                finally:
                    if not future.done():
                        future.cancel()
            else:
                def invoke():
                    assert_active()
                    return model.invoke(messages, config={"callbacks": []})

                response = controller.call(invoke)
            assert_active()
        except Exception as exc:
            record("llm_error", "failed", error=f"{type(exc).__name__}: {exc}",
                   duration_ms=int((time.perf_counter() - started) * 1000))
            raise
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
        usage = getattr(response, "usage_metadata", None)
        record("llm_end", "completed", output={"content": content}, usage=usage,
               duration_ms=int((time.perf_counter() - started) * 1000))
        return content

    return call


def _snapshot_hash(skills: list[dict]) -> str:
    return content_hash(json.dumps(
        [{"skill_id": skill["skill_id"], "content": skill["content"]} for skill in skills],
        ensure_ascii=False, sort_keys=True,
    ))


class VolumePayoffRuntime:
    """Compile lazily inside a tool worker, keeping SSE assembly non-blocking."""

    def __init__(
        self, workspace_path: Path, *, skills: list[dict], operation_id: str,
        engine, load_error: str = "", sources_loader=None, is_active=None, operation_loader=None,
    ):
        self.workspace = Path(workspace_path)
        self.skills = copy.deepcopy(skills)
        self.operation_id = operation_id
        self.engine = engine
        self.load_error = load_error
        self.sources_loader = sources_loader
        self.is_active = is_active
        self.operation_loader = operation_loader
        self._source_hash = _snapshot_hash(self.skills)
        self._guard = None
        self._policy = None
        self._lock = threading.Lock()

    def _assert_sources_unchanged(self) -> None:
        if self.sources_loader is not None and _snapshot_hash(self.sources_loader()) != self._source_hash:
            raise RuntimeError("爽点技能已更新，请按新定义重新生成本次候选。")

    def _resolve(self) -> VolumePayoffGuard:
        with self._lock:
            if self._guard is not None:
                return self._guard
            if self.operation_loader is not None:
                self.operation_id = self.operation_loader()
            try:
                if self.load_error:
                    raise RuntimeError(self.load_error)
                self._assert_sources_unchanged()
                self._policy = self.engine.catalog(self.skills)
            except Exception as exc:
                self._policy = PayoffCatalog(
                    "error", self._source_hash, errors=[f"{type(exc).__name__}: {exc}"],
                )
            policy = self._policy

            def check(candidate: str) -> dict:
                self._assert_sources_unchanged()
                verdict = self.engine.check(candidate, policy)
                self._assert_sources_unchanged()
                return verdict.to_dict()

            def repair(candidate: str, errors: list) -> str:
                self._assert_sources_unchanged()
                violations = []
                for error in errors:
                    if isinstance(error, Violation):
                        violations.append(error)
                    elif isinstance(error, dict):
                        violations.append(Violation(
                            str(error.get("code", "volume_structure")),
                            str(error.get("message", error)), error.get("chapter"), error.get("location", ""),
                        ))
                    else:
                        violations.append(Violation("volume_structure", str(error)))
                return self.engine.repair(candidate, violations, policy)

            self._guard = VolumePayoffGuard(
                self.workspace, operation_id=self.operation_id, policy_hash=policy.policy_hash,
                policy_status=policy.status, check=check, repair=repair, max_repairs=2,
                is_active=self.is_active,
            )
            return self._guard

    def commit(self, *args, **kwargs) -> dict:
        guard = self._resolve()
        try:
            self._assert_sources_unchanged()
        except Exception as exc:
            guard.policy_status = "error"
            self._policy.errors = [str(exc)]
        result = guard.commit(*args, **kwargs)
        if self._policy.errors and result["status"] != "committed":
            result = {**result, "reason": result["reason"] + " " + "；".join(self._policy.errors)}
        return result
