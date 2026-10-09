"""workspaces 路由（PR-14 从 main.py 抽出）。

工作区 CRUD + 产物读取（storyline/worldview/characters）+ SSE watch。
v8 产物路由（outline/detail-outline/storyline-graph/novel 及导出）已随
单故事专家收敛退役（REQ-20260930-163019 FR-002/004/005）——storyline 响应
自带 panorama（跨线全景事件，FR-003）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from watchfiles import awatch

from app.auth import CurrentUser, current_user
from app.domains.writing.expert_agent.services.storyline_graph import (
    build_panorama_events,
    build_rhythm_data,
)
from app.routers.context import _log, get_agent_service, get_character_service, get_thread_store
from app.schemas.screenplay import (
    PanoramaEvent,
    PromiseProgress,
    RhythmDataModel,
    RhythmPoint,
    ShapeSlotModel,
    WorkspaceBootstrapResponse,
    WorkspaceCharacterContent,
    WorkspaceCreateRequest,
    WorkspaceObjectContent,
    WorkspaceStorylineContent,
    WorkspaceSummary,
    WorkspaceWorldviewContent,
)

router = APIRouter()


def _attach_panorama(
    content: WorkspaceStorylineContent | None, workspace_path: Path
) -> WorkspaceStorylineContent | None:
    """给 v2 storyline 内容补跨线全景事件（FR-003）+ 节奏数据（FR-006）。

    组装在路由层完成——platform 层（artifact_store）禁止依赖 domains 层解析器
    （分层规则 R1）。旧格式（legacy）不解析：panorama/rhythm 保持空，前端降级。
    """
    if content is None or content.format != "v2":
        return content
    events = build_panorama_events(workspace_path)
    if events is not None:
        content.panorama = [
            PanoramaEvent(
                t=ev.t_raw, name=ev.name, type=ev.type, storylines=list(ev.storylines),
                characters=ev.characters, location=ev.location, desc=ev.desc,
                tension=ev.tension, payoff=ev.payoff,
            )
            for ev in events
        ]
    rhythm = build_rhythm_data(workspace_path)
    if rhythm is not None:
        content.rhythm = RhythmDataModel(
            mainline=[
                RhythmPoint(t=p.t, name=p.name, tension=p.tension, payoff=p.payoff, line=p.line, surface=p.surface)
                for p in rhythm.mainline
            ],
            synthesis=[
                RhythmPoint(t=p.t, name=p.name, tension=p.tension, payoff=p.payoff, line=p.line, surface=p.surface)
                for p in rhythm.synthesis
            ],
            dark={
                line: [
                    RhythmPoint(t=p.t, name=p.name, tension=p.tension, payoff=p.payoff, line=p.line, surface=p.surface)
                    for p in pts
                ]
                for line, pts in rhythm.dark.items()
            },
            shape_slots=[
                ShapeSlotModel(slot=s.slot, op=s.op, values=list(s.values), twin_peak=s.twin_peak)
                for s in rhythm.shape_slots
            ],
            promises=[
                PromiseProgress(
                    id=r.id, text=r.text, level=r.level, line=r.line, status=r.status,
                    promise_events=list(r.promise_events), progress_events=list(r.progress_events),
                    payoff_events=list(r.payoff_events), note=r.note,
                )
                for r in rhythm.promises
            ],
        )
    return content


# ════════════════════════════════════════════════════════════
# SSE watch 辅助
# ════════════════════════════════════════════════════════════

def _sse_event(event_type: str, payload: object) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n"


def _classify_changes(changes, workspace_path: Path) -> set[str]:
    categories: set[str] = set()
    for _change_type, path_str in changes:
        try:
            rel = Path(path_str).relative_to(workspace_path)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue
        top = parts[0]
        if top in ("storyline.md", "timeline.md", "promises.md") or (len(parts) > 1 and parts[0] == "storyline"):
            categories.add("storyline")
        elif top == "worldview.md":
            categories.add("worldview")
        elif len(parts) > 1 and parts[0] == "character":
            categories.add("characters")
        elif len(parts) > 1 and parts[0] == "object":
            categories.add("objects")
    return categories


async def _workspace_watch_generator(owner_id: str, workspace_id: str, workspace_path: Path):
    thread_store = get_thread_store()
    _log("sse_open", channel="watch", workspace_id=workspace_id)
    start = time.perf_counter()
    try:
        async for changes in awatch(
            workspace_path,
            watch_filter=lambda _change, path: Path(path).suffix == ".md",
            debounce=400, step=50, recursive=True, ignore_permission_denied=True,
        ):
            categories = _classify_changes(changes, workspace_path)
            if not categories:
                continue
            if "storyline" in categories:
                content = thread_store.artifacts.read_workspace_storyline(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("storyline", content.model_dump())
            if "worldview" in categories:
                content = thread_store.artifacts.read_workspace_worldview(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("worldview", content.model_dump())
            if "characters" in categories:
                content = thread_store.artifacts.read_workspace_characters(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("characters", content.model_dump())
            if "objects" in categories:
                content = thread_store.artifacts.read_workspace_objects(owner_id, workspace_id)
                if content is not None:
                    yield _sse_event("objects", content.model_dump())
        _log("sse_close", channel="watch", workspace_id=workspace_id,
             ms=int((time.perf_counter() - start) * 1000))
    except BaseException as exc:
        _log("sse_error", channel="watch", workspace_id=workspace_id,
             error=type(exc).__name__, ms=int((time.perf_counter() - start) * 1000))
        raise


# ════════════════════════════════════════════════════════════
# 端点
# ════════════════════════════════════════════════════════════

@router.get("/workspaces/{workspace_id}/bootstrap", response_model=WorkspaceBootstrapResponse)
def bootstrap_workspace(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceBootstrapResponse:
    thread_store = get_thread_store()
    data = thread_store.bootstrap_workspace(user.user_id, workspace_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is not None:
        data["storyline"] = _attach_panorama(data.get("storyline"), Path(workspace.workspace_path))
    return WorkspaceBootstrapResponse(**data)


@router.get("/workspaces", response_model=list[WorkspaceSummary])
def list_workspaces(user: CurrentUser = Depends(current_user)) -> list[WorkspaceSummary]:
    return get_thread_store().list_workspaces(user.user_id)


@router.post("/workspaces", response_model=WorkspaceSummary)
def create_workspace(payload: WorkspaceCreateRequest, user: CurrentUser = Depends(current_user)) -> WorkspaceSummary:
    thread_store = get_thread_store()
    try:
        return thread_store.create_workspace(user.user_id, payload.title, payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}/storyline", response_model=WorkspaceStorylineContent)
def get_workspace_storyline(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceStorylineContent:
    thread_store = get_thread_store()
    content = thread_store.artifacts.read_workspace_storyline(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is not None:
        content = _attach_panorama(content, Path(workspace.workspace_path))
    return content


@router.get("/workspaces/{workspace_id}/worldview", response_model=WorkspaceWorldviewContent)
def get_workspace_worldview(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceWorldviewContent:
    content = get_thread_store().artifacts.read_workspace_worldview(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return content


@router.get("/workspaces/{workspace_id}/characters", response_model=WorkspaceCharacterContent)
def get_workspace_characters(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceCharacterContent:
    content = get_thread_store().artifacts.read_workspace_characters(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return content


@router.get("/workspaces/{workspace_id}/objects", response_model=WorkspaceObjectContent)
def get_workspace_objects(workspace_id: str, user: CurrentUser = Depends(current_user)) -> WorkspaceObjectContent:
    content = get_thread_store().artifacts.read_workspace_objects(user.user_id, workspace_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return content


@router.get("/workspaces/{workspace_id}/watch")
async def watch_workspace(workspace_id: str, user: CurrentUser = Depends(current_user)):
    thread_store = get_thread_store()
    workspace = thread_store.get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace_path = Path(workspace.workspace_path)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="Workspace directory missing")
    return StreamingResponse(
        _workspace_watch_generator(user.user_id, workspace_id, workspace_path),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, str | bool | list[str]]:
    thread_store = get_thread_store()
    try:
        deleted_thread_ids = thread_store.delete_workspace(user.user_id, workspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if deleted_thread_ids is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    # T2.9：清理 checkpoint（分库）+ trace
    agent_service = get_agent_service()
    character_service = get_character_service()
    for thread_id in deleted_thread_ids:
        await agent_service.delete_thread_checkpoint(thread_id, owner_id=user.user_id)
        await character_service.delete_thread_checkpoint(thread_id)
    return {"status": "ok", "deleted": workspace_id, "deleted_threads": deleted_thread_ids}
