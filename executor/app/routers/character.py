"""character 生成路由（PR-14 从 main.py 抽出）。

POST /api/character/generate —— 角色生成（同步）。
"""

from __future__ import annotations
import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser, current_user
from app.routers.context import get_character_service, get_thread_store
from app.schemas.character import CharacterGenerateRequest, CharacterGenerateResponse

router = APIRouter()


@router.post("/character/generate", response_model=CharacterGenerateResponse)
async def generate_character(payload: CharacterGenerateRequest, user: CurrentUser = Depends(current_user)) -> CharacterGenerateResponse:
    thread = get_thread_store().get_thread(user.user_id, payload.thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    from app.platform.credits.api import prepare_operation, run_json_operation
    operation = await prepare_operation(payload, user.user_id, thread.thread_id)
    def generate_and_save():
        response = get_character_service().generate(payload, thread, owner_id=user.user_id)
        from app.platform.credits.runtime import current_billing
        billing = current_billing()
        if billing:
            billing.check_cancelled()
        get_thread_store().artifacts.write_character(user.user_id, thread, response)
        return response
    async def action():
        return await asyncio.to_thread(generate_and_save)
    return await run_json_operation(operation, action)
