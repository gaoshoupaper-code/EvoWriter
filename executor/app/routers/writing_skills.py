"""用户自定义写作技能路由（REQ-20261023-000000 / REQ-20261025-205943）。

个人 Skill 库 CRUD + 按书启用绑定。完全私有（per-owner，所有读写带
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
    # 改阶段不得使任何书的该段启用数突破上限（FR-002 不变量；
    # 编辑入口是 set_enabled 之外唯一能改变启用技能段归属的路径）
    if new_stage is not None and new_stage != existing["stage"]:
        over = repo.stage_change_overflow(skill_id, user.user_id, new_stage)
        if over:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"改阶段会让这些书的该段启用技能超过 "
                    f"{WritingSkillRepository.MAX_PER_STAGE} 条上限：{', '.join(over)}"
                ),
            )
    updated = repo.update(
        skill_id, user.user_id, name=payload.name,
        description=payload.description, content=payload.content,
        stage=new_stage,
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
    """整书替换启用列表（勾选即保存）。每段超限 422；skill 不属于本人 404。"""
    thread_store = get_thread_store()
    if thread_store.get_workspace(user.user_id, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    repo = _repo()
    if len(set(payload.skill_ids)) != len(payload.skill_ids):
        raise HTTPException(status_code=422, detail="技能列表含重复项")
    # 上限按段计数（REQ-20261025-205943 DEC-005）：同段 ≤ MAX_PER_STAGE
    stage_counts: dict[str, int] = {}
    for sid in payload.skill_ids:
        sk = repo.get(sid, user.user_id)
        if sk is None:
            raise HTTPException(status_code=404, detail=f"Skill not found: {sid}")
        stage_counts[sk["stage"]] = stage_counts.get(sk["stage"], 0) + 1
    over = [s for s, n in stage_counts.items() if n > WritingSkillRepository.MAX_PER_STAGE]
    if over:
        raise HTTPException(
            status_code=422,
            detail=(
                f"每个阶段最多同时启用 {WritingSkillRepository.MAX_PER_STAGE} 条写作技能"
                f"（超限阶段：{', '.join(over)}）"
            ),
        )
    repo.set_enabled(workspace_id, user.user_id, payload.skill_ids)
    # 响应附带保存后的权威启用列表（含 stage），客户端免二次回拉——
    # 附加字段，旧客户端忽略无感知
    return {
        "status": "ok",
        "enabled": payload.skill_ids,
        "skills": repo.list_enabled_for_workspace(workspace_id, user.user_id),
    }
