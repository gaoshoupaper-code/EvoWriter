"""evolution → executor 内部通道 client（REQ-20261001-131018 DEC-007/008/010）。

进化 Agent 绑定链路对 executor 的三处依赖统一走这里：
  - 作品列表（创建 Agent 的选择源）
  - 作品概要（开场注入 + 存在性探测——404 即作品被删）
  - 当前产物快照（storyline/worldview/characters 权威文件）

错误语义（FR-001/006/009 失败语义的统一底座）：
  - WorkNotFoundError：executor 明确 404 → 作品不存在（可做粘性删除标记）
  - ExecutorUnavailableError：连接失败/超时/5xx → 明确报错不静默，不得误标删除

internal 接口无鉴权（evolution 与 executor 同信任域，见 executor internal.py）。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.settings import settings

logger = logging.getLogger("evolution.evolve.executor_client")


class WorkNotFoundError(Exception):
    """executor 明确返回作品不存在（404）——可做「作品已删除」粘性标记。"""


class ExecutorUnavailableError(Exception):
    """executor 不可达或异常响应——明确报错，不误标作品删除。"""


def _get(url: str) -> httpx.Response:
    """GET with 统一超时与错误分类。连接类错误/5xx → ExecutorUnavailableError。"""
    try:
        resp = httpx.get(url, timeout=10.0)
    except httpx.HTTPError as exc:
        raise ExecutorUnavailableError(f"executor 不可达: {exc}") from exc
    if resp.status_code == 404:
        raise WorkNotFoundError(url)
    if resp.status_code >= 500:
        raise ExecutorUnavailableError(
            f"executor 异常响应 {resp.status_code}: {url}"
        )
    resp.raise_for_status()
    return resp


def fetch_workspaces() -> list[dict[str, Any]]:
    """拉全平台作品列表（创建进化 Agent 的选择源，DEC-008）。

    Raises:
        ExecutorUnavailableError: 不可达——创建页报错，不静默（FR-001 失败语义）。
    """
    resp = _get(f"{settings.executor_url}/internal/workspaces")
    data = resp.json()
    return data.get("workspaces", [])


def fetch_workspace(workspace_id: str) -> dict[str, Any]:
    """拉单个作品概要（开场注入的作品概览 + 存在性探测）。

    Raises:
        WorkNotFoundError: 作品不存在（被删）。
        ExecutorUnavailableError: 不可达。
    """
    resp = _get(f"{settings.executor_url}/internal/workspaces/{workspace_id}")
    return resp.json()


def fetch_workspace_artifacts(workspace_id: str) -> dict[str, Any]:
    """拉作品当前产物快照（DEC-007「当前产物」路——executor 权威文件）。

    Raises:
        WorkNotFoundError: 作品不存在——调用方返回「作品已删除」（FR-006 失败语义）。
        ExecutorUnavailableError: 不可达。
    """
    resp = _get(
        f"{settings.executor_url}/internal/workspaces/{workspace_id}/artifacts"
    )
    return resp.json()


__all__ = [
    "WorkNotFoundError",
    "ExecutorUnavailableError",
    "fetch_workspaces",
    "fetch_workspace",
    "fetch_workspace_artifacts",
]
