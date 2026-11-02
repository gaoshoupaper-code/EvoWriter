"""用户自定义写作技能路由（REQ-20261023-000000 / REQ-20261025-205943）。

个人 Skill 库 CRUD + 账号全局启用。完全私有（per-owner，所有读写带
owner_id）；技能带阶段归属（总纲/卷纲/章纲/正文四选一），启用上限按段
计数（DEC-005：每段 ≤5 条，跨段不挤占，服务端硬闸与界面拦截双保险）。
多段注入逻辑在 writing agent 装配处（见 agent.py），本路由只管数据。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.auth import CurrentUser, current_user
from app.platform.core.db import WritingSkillRepository, get_database
from app.routers.context import get_thread_store

router = APIRouter()

# 阶段四选一（REQ-20261025-205943 DEC-001：题材段不开放）
WritingStage = Literal["outline", "volume", "chapter", "text"]


class WritingSkillPayload(BaseModel):
    """三字段卡片（DEC-001）＋ 阶段归属（四选一）；指令正文 ≤20000 字。

    stage 缺省 'text'（旧桌面端不带该字段，需求风险节兼容口径）：
    新建按缺省归正文段；编辑时路由按 model_fields_set 判定「缺省＝不改
    阶段」。非法值一律 422。
    """

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=20000)
    stage: WritingStage = "text"

    @field_validator("name", "description", "content")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("不能为空白")
        return stripped


class EnabledSkillsRequest(BaseModel):
    skill_ids: list[str]


class GlobalEnabledSkillsRequest(EnabledSkillsRequest):
    stage: WritingStage


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
        stage=payload.stage,
    )


@router.get("/writing-skills/enabled")
def get_global_enabled_writing_skills(
    user: CurrentUser = Depends(current_user),
) -> dict:
    return _repo().get_global_state(user.user_id)


@router.put("/writing-skills/enabled")
def set_global_enabled_writing_skills(
    payload: GlobalEnabledSkillsRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    """单阶段替换，其他阶段的启用与待选状态不变。"""
    try:
        return _repo().set_enabled_for_stage(user.user_id, payload.stage, payload.skill_ids)
    except ValueError as exc:
        status = 404 if str(exc).startswith("Skill not found:") else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.put("/writing-skills/{skill_id}")
def update_writing_skill(
    skill_id: str, payload: WritingSkillPayload,
    user: CurrentUser = Depends(current_user),
) -> dict:
    repo = _repo()
    existing = repo.get(skill_id, user.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    # 旧客户端编辑不带 stage（需求风险节兼容口径）：缺省＝不改阶段，
    # 避免 PUT 把已设阶段静默重置回正文段；显式传值才生效
    new_stage = payload.stage if "stage" in payload.model_fields_set else None
    try:
        updated = repo.update(
            skill_id, user.user_id, name=payload.name,
            description=payload.description, content=payload.content,
            stage=new_stage,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    """兼容旧客户端读取：返回已确认的账号全局清单。"""
    thread_store = get_thread_store()
    if thread_store.get_workspace(user.user_id, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _repo().list_enabled_for_owner(user.user_id)


@router.put("/workspaces/{workspace_id}/writing-skills")
def set_enabled_writing_skills(
    workspace_id: str, payload: EnabledSkillsRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    """旧客户端清单没有全局语义，明确拒写，防止覆盖账号配置。"""
    thread_store = get_thread_store()
    if thread_store.get_workspace(user.user_id, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    raise HTTPException(status_code=409, detail="技能已改为账号全局设置，请更新客户端后管理技能。")
