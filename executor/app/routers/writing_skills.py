"""用户自定义写作技能路由（REQ-20261023-000000）。

个人 Skill 库 CRUD + 按书启用绑定。完全私有（per-owner，所有读写带
owner_id）；每本书同时启用 ≤5 条（DEC-006，服务端硬闸与界面拦截双保险）。
仅正文段注入逻辑在 writing agent 装配处（见 agent.py），本路由只管数据。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.auth import CurrentUser, current_user
from app.platform.core.db import WritingSkillRepository, get_database
from app.routers.context import get_thread_store

router = APIRouter()


class WritingSkillPayload(BaseModel):
    """三字段卡片（DEC-001）；三字段必填，指令正文 ≤3000 字（DEC-006）。"""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=3000)

    @field_validator("name", "description", "content")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("不能为空白")
        return stripped


class EnabledSkillsRequest(BaseModel):
    skill_ids: list[str]


def _repo() -> WritingSkillRepository:
    return WritingSkillRepository(get_database())


@router.get("/writing-skills")
def list_writing_skills(
    user: CurrentUser = Depends(current_user),
) -> list[dict]:
    """当前用户的全部写作技能（含指令正文，供列表与编辑一次拉齐）。"""
    return _repo().list_by_owner(user.user_id)


@router.post("/writing-skills")
def create_writing_skill(
    payload: WritingSkillPayload,
    user: CurrentUser = Depends(current_user),
) -> dict:
    return _repo().create(
        owner_id=user.user_id, name=payload.name,
        description=payload.description, content=payload.content,
    )


@router.put("/writing-skills/{skill_id}")
def update_writing_skill(
    skill_id: str, payload: WritingSkillPayload,
    user: CurrentUser = Depends(current_user),
) -> dict:
    updated = _repo().update(
        skill_id, user.user_id, name=payload.name,
        description=payload.description, content=payload.content,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return updated


@router.delete("/writing-skills/{skill_id}")
def delete_writing_skill(
    skill_id: str, user: CurrentUser = Depends(current_user),
) -> dict:
    if not _repo().delete(skill_id, user.user_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"status": "ok", "deleted": skill_id}


@router.get("/workspaces/{workspace_id}/writing-skills")
def list_enabled_writing_skills(
    workspace_id: str, user: CurrentUser = Depends(current_user),
) -> list[dict]:
    """本书启用的写作技能（按启用顺序）。"""
    thread_store = get_thread_store()
    if thread_store.get_workspace(user.user_id, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _repo().list_enabled_for_workspace(workspace_id, user.user_id)


@router.put("/workspaces/{workspace_id}/writing-skills")
def set_enabled_writing_skills(
    workspace_id: str, payload: EnabledSkillsRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    """整书替换启用列表（勾选即保存）。超限 422；skill 不属于本人 404。"""
    thread_store = get_thread_store()
    if thread_store.get_workspace(user.user_id, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    repo = _repo()
    if len(set(payload.skill_ids)) != len(payload.skill_ids):
        raise HTTPException(status_code=422, detail="技能列表含重复项")
    if len(payload.skill_ids) > WritingSkillRepository.MAX_PER_WORKSPACE:
        raise HTTPException(
            status_code=422,
            detail=f"每本书最多同时启用 {WritingSkillRepository.MAX_PER_WORKSPACE} 条写作技能",
        )
    for sid in payload.skill_ids:
        if repo.get(sid, user.user_id) is None:
            raise HTTPException(status_code=404, detail=f"Skill not found: {sid}")
    repo.set_enabled(workspace_id, user.user_id, payload.skill_ids)
    return {"status": "ok", "enabled": payload.skill_ids}
