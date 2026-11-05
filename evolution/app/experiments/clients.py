"""Small HTTP boundary for durable execution and immutable Platform releases."""
from __future__ import annotations

import httpx

from app.core.settings import settings


class ServiceUnavailable(RuntimeError):
    pass


class TaskNotFound(LookupError):
    pass


def _request(service: str, method: str, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
    base = settings.platform_url if service == "platform" else settings.executor_url
    try:
        response = httpx.request(method, base.rstrip("/") + path, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ServiceUnavailable(f"{service}连接暂时不可用：{type(exc).__name__}") from exc
    if response.status_code == 404:
        raise TaskNotFound(path)
    if response.status_code == 409:
        raise ValueError("生产版本或任务状态已变化，请重新核对")
    if response.status_code >= 500:
        raise ServiceUnavailable(f"{service}暂时不可用：HTTP {response.status_code}")
    if not response.is_success:
        raise ValueError(f"{service}拒绝请求：HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ServiceUnavailable(f"{service}返回了无效响应") from exc


def production() -> dict:
    return _request("platform", "GET", "/api/production")


def versions() -> dict:
    return _request("platform", "GET", "/api/versions")


def prepare_artifact(commit: str) -> dict:
    return _request("platform", "POST", "/api/artifacts/prepare", {"source_commit": commit}, timeout=150)


def start_task(payload: dict) -> dict:
    return _request("executor", "POST", "/internal/target-tasks", payload)


def get_task(task_id: str) -> dict:
    return _request("executor", "GET", f"/internal/target-tasks/{task_id}")


def stop_task(task_id: str) -> dict:
    return _request("executor", "POST", f"/internal/target-tasks/{task_id}/stop", {})


def runtime() -> dict:
    return _request("executor", "GET", "/internal/target-tasks/runtime")


def promote(payload: dict) -> dict:
    return _request("platform", "POST", "/api/release/promote", payload, timeout=300)


def restore(payload: dict) -> dict:
    return _request("platform", "POST", "/api/release/restore", payload, timeout=300)
