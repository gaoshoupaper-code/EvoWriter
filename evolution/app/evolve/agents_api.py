"""进化 Agent CRUD API（REQ-20261001-131018 / FR-001 / FR-009）。

端点：
  GET    /api/evolve/workspaces                    可绑作品列表（代理 executor，创建页数据源）
  POST   /api/evolve/agents                        创建（1:1 绑定校验 + 作品存在性校验）
  GET    /api/evolve/agents                        Agent 列表（active；include_archived 含归档区）
  GET    /api/evolve/agents/{agent_id}             详情（会话列表 + 作品探测；404 时粘性标记删除）
  PATCH  /api/evolve/agents/{agent_id}             改名（绑定不可改，FR-001）
  POST   /api/evolve/agents/{agent_id}/archive     归档（释放绑定，DEC-010）

设计依据：DEC-003（独立实体）/ DEC-008（全平台可选）/ DEC-009（1:1）/
DEC-010（作品删除粘性标记，不可达不误标）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.evolve import agents_repo
from app.evolve import db as ev_db
from app.evolve.executor_client import (
    ExecutorUnavailableError,
    WorkNotFoundError,
    fetch_workspace,
    fetch_workspaces,
)

logger = logging.getLogger("evolution.evolve.agents_api")

router = APIRouter(tags=["evolve-agents"])


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    workspace_id: str = Field(min_length=1)


class RenameAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/evolve/workspaces")
def list_bindable_workspaces() -> dict[str, Any]:
    """可绑作品列表（创建 Agent 的选择源，DEC-008 全平台）。

    executor 不可达时明确报错（FR-001 失败语义），不静默返回空。
    """
    try:
        workspaces = fetch_workspaces()
    except ExecutorUnavailableError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"作品列表拉取失败（executor 不可达）：{exc}",
        ) from exc
    return {"workspaces": workspaces, "total": len(workspaces)}


@router.post("/evolve/agents", status_code=201)
def create_agent(req: CreateAgentRequest) -> dict[str, Any]:
    """创建进化 Agent（FR-001）。

    校验顺序：作品存在（executor 404 → 400）→ 绑定 1:1（409，含占用者信息）。
    """
    try:
        work = fetch_workspace(req.workspace_id)
    except WorkNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=f"作品 {req.workspace_id} 不存在（可能已被删除），无法绑定",
        ) from None
    except ExecutorUnavailableError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"作品校验失败（executor 不可达）：{exc}",
        ) from exc

    try:
        agent = agents_repo.create(req.name, req.workspace_id)
    except agents_repo.WorkspaceAlreadyBoundError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "bound_agent_id": exc.occupier["agent_id"],
                "bound_agent_name": exc.occupier["name"],
            },
        ) from exc
    logger.info(
        "进化 Agent 创建: agent=%s name=%s workspace=%s(owner=%s)",
        agent["agent_id"], req.name, req.workspace_id, work.get("owner_username"),
    )
    return agent


@router.get("/evolve/agents")
def list_agents(
    include_archived: bool = Query(False, description="含归档 Agent（归档区展示）"),
) -> dict[str, Any]:
    """Agent 列表（active 优先；归档区单独拉）。不触发 executor 探测（列表高频）。"""
    agents = agents_repo.list_agents(include_archived=include_archived)
    return {"agents": agents, "total": len(agents)}


@router.get("/evolve/agents/{agent_id}")
def get_agent(agent_id: str) -> dict[str, Any]:
    """Agent 详情（FR-009：作品探测 + 粘性删除标记）。

    探测语义（DEC-010）：
      - executor 404 → mark_work_deleted（粘性），返回 work_deleted=true
      - executor 不可达 → 不误标，work_probe="unreachable"
      - 作品健在 → work_deleted 取决于既有标记；返回作品概要
    """
    agent = agents_repo.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"进化 Agent {agent_id} 不存在")

    work_overview: dict[str, Any] | None = None
    work_probe = "ok"
    try:
        work_overview = fetch_workspace(agent["workspace_id"])
    except WorkNotFoundError:
        work_probe = "missing"
        if agents_repo.mark_work_deleted(agent_id):
            logger.warning(
                "Agent %s 绑定作品 %s 已被删除，标记失效（DEC-010）",
                agent_id, agent["workspace_id"],
            )
        agent = agents_repo.get(agent_id) or agent
    except ExecutorUnavailableError as exc:
        # 不可达不误标（FR-009 失败语义）
        work_probe = "unreachable"
        logger.warning("Agent %s 作品探测失败（不标记删除）: %s", agent_id, exc)

    sessions = ev_db.list_sessions_by_agent(agent_id, limit=200)
    return {
        **agent,
        "work_probe": work_probe,
        "work_overview": work_overview,
        "work_deleted": agent.get("work_deleted_at") is not None,
        "sessions": sessions,
    }


@router.patch("/evolve/agents/{agent_id}")
def rename_agent(agent_id: str, req: RenameAgentRequest) -> dict[str, Any]:
    """改名（绑定关系不可改——换作品须归档重建，FR-001）。"""
    if not agents_repo.rename(agent_id, req.name):
        raise HTTPException(status_code=404, detail=f"进化 Agent {agent_id} 不存在")
    return agents_repo.get(agent_id)


@router.post("/evolve/agents/{agent_id}/archive")
def archive_agent(agent_id: str) -> dict[str, Any]:
    """归档 Agent（DEC-010）：释放绑定；历史会话进「未绑定」区（查询侧归类）。"""
    if not agents_repo.archive(agent_id):
        raise HTTPException(
            status_code=404,
            detail=f"进化 Agent {agent_id} 不存在或已归档",
        )
    logger.info("进化 Agent 归档（绑定释放）: agent=%s", agent_id)
    return {"agent_id": agent_id, "status": "archived"}


__all__ = ["router"]
