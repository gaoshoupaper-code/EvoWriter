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
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core import db
from app.evolve import agents_repo
from app.evolve import db as ev_db
from app.evolve.executor_client import (
    ExecutorUnavailableError,
    WorkNotFoundError,
    fetch_workspace,
    fetch_workspaces,
    fetch_workspace_writing_artifacts,
    fetch_workspace_writing_artifact,
    WritingArtifactNotFoundError,
    InvalidWritingArtifactPathError,
    WritingWorkspaceNotFiveSegmentError,
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


def _require_agent(agent_id: str) -> dict[str, Any]:
    agent = agents_repo.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"进化 Agent {agent_id} 不存在")
    return agent


def _require_full_artifact_access(request: Request) -> None:
    if not getattr(request.state, "is_super_admin", False):
        raise HTTPException(status_code=403, detail="完整作品产物正文仅超级管理员可访问")


_FIVE_SEGMENT_ARTIFACT_PATTERNS = (
    re.compile(r"主题\.md"),
    re.compile(r"总纲\.md"),
    re.compile(r"文风\.md"),
    re.compile(r"卷纲/第\d+卷\.md"),
    re.compile(r"章纲/第\d+章\.md"),
    re.compile(r"正文/第\d+章[^/]*\.md"),
    re.compile(r"人物/[^/]+\.md"),
)
_INTERNAL_ARTIFACT_DIRS = {".writing", ".git", ".zcode", ".agents", "config", "configs"}
_LEGACY_ARTIFACT_PATTERNS = (
    re.compile(r"(?:storyline|worldview)\.md"),
    re.compile(r"(?:character|characters|outline|detail|chapter|review)/.+\.md"),
)


def _normalized_artifact_path(path: str) -> str | None:
    if "\\" in path:
        return None
    normalized = path[1:] if path.startswith("/") else path
    if normalized.startswith("/") or any(
        part in {"", ".", ".."} for part in normalized.split("/")
    ):
        return None
    return normalized


def _is_five_segment_path(path: str) -> bool:
    normalized = _normalized_artifact_path(path)
    return normalized is not None and any(
        pattern.fullmatch(normalized) for pattern in _FIVE_SEGMENT_ARTIFACT_PATTERNS
    )


def _is_visible_artifact_path(path: str) -> bool:
    normalized = _normalized_artifact_path(path)
    if normalized is None:
        return False
    parts = normalized.split("/")
    if any(part.startswith(".") or part.lower() in _INTERNAL_ARTIFACT_DIRS for part in parts):
        return False
    return _is_five_segment_path(normalized) or any(
        pattern.fullmatch(normalized) for pattern in _LEGACY_ARTIFACT_PATTERNS
    )


@router.get("/evolve/agents/{agent_id}/artifacts")
def list_agent_artifacts(
    agent_id: str,
    request: Request,
    category: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    revision_offset: int = Query(0, ge=0),
    revision_limit: int = Query(100, ge=1, le=200),
) -> dict[str, Any]:
    """返回 Agent 绑定作品当前文件与跨运行修订元数据。"""
    agent = _require_agent(agent_id)
    workspace_id = agent["workspace_id"]
    try:
        current = fetch_workspace_writing_artifacts(
            workspace_id, category=category, offset=offset, limit=limit
        )
    except WorkNotFoundError:
        agents_repo.mark_work_deleted(agent_id)
        current = {
            "workspace_id": workspace_id, "kind": "missing", "category": category,
            "offset": offset, "limit": limit, "total": 0, "files": [],
        }
    except ExecutorUnavailableError as exc:
        current = {
            "workspace_id": workspace_id, "kind": "unreachable", "category": category,
            "offset": offset, "limit": limit, "total": 0, "files": [],
            "error": str(exc),
        }

    rows = db.query_all(
        """SELECT r.artifact_revision_id, r.content_hash, r.producer_trace_id,
                  r.producer_event_id, r.harness_version, r.created_at,
                  a.artifact_type, a.logical_key, p.size_bytes, p.expires_at, p.deleted_at
           FROM artifacts a
           JOIN artifact_revisions r ON r.artifact_id = a.artifact_id
           LEFT JOIN payload_objects p ON p.payload_id = r.payload_id
           WHERE a.workspace_id = ?
           ORDER BY a.logical_key, r.created_at, r.artifact_revision_id""",
        (workspace_id,),
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        logical_key = str(row["logical_key"] or "")
        if not _is_visible_artifact_path(logical_key):
            continue
        revisions = grouped.setdefault(logical_key, [])
        revisions.append({
            "artifact_revision_id": row["artifact_revision_id"],
            "content_hash": row["content_hash"],
            "producer_trace_id": row["producer_trace_id"],
            "producer_event_id": row["producer_event_id"],
            "harness_version": row["harness_version"],
            "created_at": row["created_at"],
            "artifact_type": row["artifact_type"],
            "size_bytes": row["size_bytes"],
            "expires_at": row["expires_at"],
            "deleted_at": row["deleted_at"],
        })
    revision_entries = [
        {"logical_key": key, **revision}
        for key, revisions in grouped.items()
        for revision in revisions
    ]
    total_revisions = len(revision_entries)
    selected_entries = revision_entries[revision_offset:revision_offset + revision_limit]
    selected_grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in selected_entries:
        selected_grouped.setdefault(entry["logical_key"], []).append(
            {key: value for key, value in entry.items() if key != "logical_key"}
        )
    return {
        "agent_id": agent_id,
        "workspace_id": workspace_id,
        "current": current,
        "revisions": [
            {
                "logical_key": key,
                "revision_count": len(grouped[key]),
                "head_revision_id": grouped[key][-1]["artifact_revision_id"],
                "revisions": revisions,
            }
            for key, revisions in selected_grouped.items()
        ],
        "revision_total": total_revisions,
        "revision_offset": revision_offset,
        "revision_limit": revision_limit,
        "can_read_content": bool(getattr(request.state, "is_super_admin", False)),
    }


@router.get("/evolve/agents/{agent_id}/artifacts/content")
def read_agent_artifact(
    agent_id: str,
    request: Request,
    path: str = Query(..., min_length=1, max_length=500),
) -> dict[str, Any]:
    """读取绑定作品的当前文件；必须超管，且路径来自五段式文本白名单。"""
    _require_full_artifact_access(request)
    agent = _require_agent(agent_id)
    if not _is_five_segment_path(path):
        raise HTTPException(status_code=400, detail="Path is not a five-segment text artifact")
    workspace_id = agent["workspace_id"]
    try:
        content = fetch_workspace_writing_artifact(workspace_id, path)
    except WorkNotFoundError:
        agents_repo.mark_work_deleted(agent_id)
        raise HTTPException(status_code=404, detail="作品已删除") from None
    except WritingArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="当前文件不存在") from None
    except InvalidWritingArtifactPathError:
        raise HTTPException(status_code=400, detail="Path is not a writing artifact") from None
    except WritingWorkspaceNotFiveSegmentError:
        raise HTTPException(status_code=409, detail="当前作品不是五段式工作区") from None
    except ExecutorUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"当前文件读取失败（executor 不可达）：{exc}") from exc
    from app.trace.access import audit_content_access
    audit_content_access(request, "view", "workspace_artifact", f"{agent_id}:{path}")
    return {"agent_id": agent_id, "workspace_id": workspace_id, "path": path, "content": content}


@router.get("/evolve/agents/{agent_id}/artifacts/revisions/{revision_id}/content")
def read_agent_artifact_revision(
    agent_id: str, revision_id: str, request: Request,
) -> dict[str, Any]:
    """读取绑定作品的历史修订正文；元数据接口不暴露 payload_id。"""
    _require_full_artifact_access(request)
    agent = _require_agent(agent_id)
    row = db.query_one(
        """SELECT r.artifact_revision_id, r.payload_id, r.content_hash, r.created_at,
                  a.logical_key, a.workspace_id
           FROM artifact_revisions r
           JOIN artifacts a ON a.artifact_id = r.artifact_id
           WHERE r.artifact_revision_id = ?""",
        (revision_id,),
    )
    if row is None or row.get("workspace_id") != agent["workspace_id"]:
        raise HTTPException(status_code=404, detail="Artifact revision not found")
    if not _is_visible_artifact_path(str(row["logical_key"])):
        raise HTTPException(status_code=404, detail="Artifact revision not found")
    from app.trace_payloads import read_payload
    content = read_payload(row["payload_id"])
    if content is None:
        raise HTTPException(status_code=404, detail="ArtifactRevision content expired")
    from app.trace.access import audit_content_access
    audit_content_access(request, "view", "artifact_revision", revision_id)
    return {
        "artifact_revision_id": revision_id,
        "logical_key": row["logical_key"],
        "created_at": row["created_at"],
        "content_hash": row["content_hash"],
        "content": content.get("content") if isinstance(content, dict) else content,
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
