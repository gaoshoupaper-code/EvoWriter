"""个人整卡文风库与作品导入/保存（REQ-20261102-162419）。"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from app.auth import CurrentUser, current_user
from app.domains.writing.run_gate import get_workspace_mutation_lock
from app.domains.writing.style_assets import replace_style_card
from app.platform.core.db import WritingStyleRepository, get_database
from app.routers.context import get_thread_store, get_trace_recorder
from contracts.writing_artifacts import STYLE_FILENAME

async def _guard_target_writes(request: Request, user: CurrentUser = Depends(current_user)):
    workspace_id = request.path_params.get("workspace_id")
    if workspace_id and request.method not in {"GET", "HEAD", "OPTIONS"}:
        from app.domains.writing.run_gate import active_target_task
        workspace = get_thread_store().get_workspace(user.user_id, workspace_id)
        if workspace is None:
            raise HTTPException(404, "Workspace not found")
        if active_target_task(workspace.workspace_path):
            raise HTTPException(409, "作品正在执行全自动任务，请先停止任务再修改文风。")


router = APIRouter(dependencies=[Depends(_guard_target_writes)])
logger = logging.getLogger(__name__)


class StyleName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str

    @field_validator("name")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("文风名称不能为空白")
        return value.strip()


class SaveStyle(StyleName):
    target_style_id: str | None = None


class ImportStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style_id: str


def _repo() -> WritingStyleRepository:
    return WritingStyleRepository(get_database())


def _workspace(owner_id: str, workspace_id: str) -> Path:
    workspace = get_thread_store().get_workspace(owner_id, workspace_id)
    if workspace is None or workspace.domain != "writing":
        raise HTTPException(404, "作品不存在")
    from app.domains.writing.pipeline import classify_workspace

    if classify_workspace(Path(workspace.workspace_path)) == "legacy":
        raise HTTPException(409, "旧版工作区只读，不支持保存或导入文风。")
    return Path(workspace.workspace_path)


def _write_failed(exc: Exception) -> HTTPException:
    if isinstance(exc, sqlite3.IntegrityError) and "UNIQUE constraint failed" in str(exc):
        return HTTPException(409, "已有同名文风，请改名或明确选择更新旧文风。")
    logger.exception("文风写入失败")
    return HTTPException(500, "文风保存失败，原数据已保留，请重试。")


@router.get("/writing-styles")
def list_styles(user: CurrentUser = Depends(current_user)) -> list[dict]:
    return _repo().list_by_owner(user.user_id)


@router.patch("/writing-styles/{style_id}")
def rename_style(style_id: str, payload: StyleName, user: CurrentUser = Depends(current_user)) -> dict:
    try:
        asset = _repo().rename(style_id, user.user_id, payload.name)
    except sqlite3.Error as exc:
        raise _write_failed(exc) from exc
    if asset is None:
        raise HTTPException(404, "文风不存在")
    return asset


@router.delete("/writing-styles/{style_id}")
def delete_style(style_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    try:
        deleted = _repo().delete(style_id, user.user_id)
    except sqlite3.Error as exc:
        raise _write_failed(exc) from exc
    if not deleted:
        raise HTTPException(404, "文风不存在")
    return {"status": "ok", "deleted": style_id}


@router.post("/workspaces/{workspace_id}/writing-style/save")
def save_style(workspace_id: str, payload: SaveStyle, user: CurrentUser = Depends(current_user)) -> dict:
    workspace = _workspace(user.user_id, workspace_id)
    try:
        with get_workspace_mutation_lock(workspace):
            content = (workspace / STYLE_FILENAME).read_bytes().decode("utf-8")
            if not content.strip():
                raise HTTPException(422, "当前作品文风为空，无法保存。")
            asset = _repo().save(user.user_id, payload.name, content, payload.target_style_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "当前作品还没有文风，请先通过对话生成。") from exc
    except (OSError, UnicodeError, sqlite3.Error) as exc:
        raise _write_failed(exc) from exc
    if asset is None:
        raise HTTPException(404, "要更新的文风不存在")
    return asset


@router.post("/workspaces/{workspace_id}/writing-style/import")
def import_style(workspace_id: str, payload: ImportStyle, user: CurrentUser = Depends(current_user)) -> dict:
    workspace = _workspace(user.user_id, workspace_id)
    with get_workspace_mutation_lock(workspace):
        if any(run.get("workspace_id") == workspace_id and run.get("status") == "running"
               for run in get_trace_recorder().list_active_runs()):
            raise HTTPException(409, "作品正在生成，请停止或等待生成结束后再导入文风。")
        asset = _repo().get(payload.style_id, user.user_id)
        if asset is None:
            raise HTTPException(404, "文风不存在")
        try:
            invalidated = replace_style_card(workspace, asset["content"])
        except (OSError, ValueError) as exc:
            raise _write_failed(exc) from exc
    return {"status": "ok", "style_id": payload.style_id, "content": asset["content"], "invalidated_annotations": invalidated}
