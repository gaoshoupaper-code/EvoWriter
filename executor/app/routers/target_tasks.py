"""Owner-scoped automatic writing and trusted internal evaluation jobs."""
from __future__ import annotations

from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import CurrentUser, current_user
from app.routers.context import get_thread_store, get_trace_recorder
from app.domains.writing.target_tasks import get_manager, collect_artifacts
from contracts.target_tasks import TargetSpec, TaskInput, TargetTaskRequest, TargetTaskResult

router = APIRouter(prefix="/api/workspaces", tags=["target-tasks"])
internal_router = APIRouter(prefix="/internal/target-tasks", include_in_schema=False)


class PublicTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: TargetSpec
    confirmation_rules: str = Field(min_length=1)
    thread_id: str | None = None
    demand_md: str | None = None


def _workspace(workspace_id, user):
    workspace = get_thread_store().get_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace


def _owned_task(workspace_id, task_id, user):
    _workspace(workspace_id, user)
    manager = get_manager()
    try:
        record = manager.store.trusted(task_id)
    except ValueError:
        record = None
    if (record is None or record["metadata"].get("owner_id") != user.user_id
            or record["metadata"].get("thread", {}).get("workspace_id") != workspace_id):
        raise HTTPException(404, "Target task not found")
    return manager


@internal_router.get("/runtime")
def runtime_identity():
    from app.platform.agent.loader import production_commit, production_checkout
    digest = None
    try:
        root = production_checkout()
        if root is not None and (Path(root) / ".digest").is_file():
            digest = (Path(root) / ".digest").read_text(encoding="utf-8").strip()
    except RuntimeError:
        pass
    return {"commit": production_commit(), "artifact_digest": digest}


@internal_router.post("", response_model=TargetTaskResult, status_code=202)
async def start_internal(request: TargetTaskRequest):
    try:
        return get_manager().submit(request)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@internal_router.get("/{task_id}", response_model=TargetTaskResult)
async def get_internal(task_id: str):
    manager = get_manager()
    try:
        result = manager.result(task_id)
    except ValueError:
        result = None
    if result is None:
        raise HTTPException(404, "Target task not found")
    manager.start(task_id)
    return manager.result(task_id)


@internal_router.post("/{task_id}/stop", response_model=TargetTaskResult)
async def stop_internal(task_id: str):
    result = await get_manager().stop(task_id)
    if result is None:
        raise HTTPException(404, "Target task not found")
    return result


@router.get("/{workspace_id}/target-tasks")
async def list_public(workspace_id: str, user: CurrentUser = Depends(current_user)):
    _workspace(workspace_id, user)
    manager = get_manager()
    tasks = []
    for record in manager.store.trusted_all():
        result = manager.result(record["request"]["task_id"])
        metadata = record["metadata"]
        if metadata.get("owner_id") == user.user_id and metadata.get("thread", {}).get("workspace_id") == workspace_id:
            tasks.append(result.model_dump(mode="json"))
    return {"tasks": tasks}


@router.post("/{workspace_id}/target-tasks", response_model=TargetTaskResult, status_code=202)
async def start_public(workspace_id: str, payload: PublicTargetRequest, user: CurrentUser = Depends(current_user)):
    workspace = _workspace(workspace_id, user)
    root = Path(workspace.workspace_path)
    from app.domains.writing.pipeline import classify_workspace
    if classify_workspace(root) != "five":
        raise HTTPException(409, "旧版作品为只读，不能启动全自动任务。")
    if any(run.get("workspace_id") == workspace_id and run.get("status") == "running"
           for run in get_trace_recorder().list_active_runs()):
        raise HTTPException(409, "作品正在生成，请先停止当前创作。")
    store = get_thread_store()
    if payload.thread_id:
        thread = store.get_thread(user.user_id, payload.thread_id)
        if thread is None or thread.workspace_id != workspace_id:
            raise HTTPException(404, "Thread not found")
    else:
        thread = None
    try:
        from app.platform.agent.loader import load_current_package, production_commit, production_checkout
        from app.domains.writing.agent import build_llm_snapshot
        from app.domains.writing.target_tasks import validate_input
        from app.platform.core.db import WritingSkillRepository, get_database
        load_current_package()
        files = collect_artifacts(root)
        previous_demand = files.pop("demand.md", "")
        demand = payload.demand_md if payload.demand_md is not None else previous_demand
        if previous_demand and payload.demand_md is not None and demand != previous_demand:
            raise ValueError("已有需求与本次输入不同，请先更新作品需求后再启动，避免覆盖原稿")
        skills_repo = WritingSkillRepository(get_database())
        relevant = {"volume_outlines": {"outline", "volume"}, "chapter_outlines": {"chapter", "volume"}, "chapter_text": {"text"}}
        if relevant[payload.target.task_type] & set(skills_repo.get_global_state(user.user_id)["pending_stages"]):
            raise ValueError("目标阶段技能待选择，请先保存账号技能选择")
        skills = skills_repo.list_enabled_for_owner(user.user_id)
        data = TaskInput(demand_md=demand, files=files, skills=skills, confirmation_rules=payload.confirmation_rules)
        validate_input(payload.target, data)
        snapshot = build_llm_snapshot()
        if snapshot is None:
            from contracts.platform import LlmConfigSnapshot
            from app.platform.core.settings import get_settings
            settings = get_settings()
            snapshot = LlmConfigSnapshot(model=settings.writer_model,
                                         base_url=settings.platform_base_url or settings.openai_base_url or "https://api.openai.com/v1",
                                         source="environment")
        if thread is None:
            thread = store.create_thread(user.user_id, workspace_id, "全自动目标任务")
        digest_path = production_checkout() / ".digest"
        request = TargetTaskRequest(
            task_id=uuid.uuid4().hex, attempt_id=uuid.uuid4().hex,
            source_commit=production_commit(),
            artifact_digest=digest_path.read_text(encoding="utf-8").strip() if digest_path.is_file() else None,
            model_snapshot=snapshot, target=payload.target, input=data,
        )
        return get_manager().submit(request, workspace=root, thread=thread, owner_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/{workspace_id}/target-tasks/{task_id}", response_model=TargetTaskResult)
async def get_public(workspace_id: str, task_id: str, user: CurrentUser = Depends(current_user)):
    return _owned_task(workspace_id, task_id, user).result(task_id)


@router.post("/{workspace_id}/target-tasks/{task_id}/stop", response_model=TargetTaskResult)
async def stop_public(workspace_id: str, task_id: str, user: CurrentUser = Depends(current_user)):
    return await _owned_task(workspace_id, task_id, user).stop(task_id)
