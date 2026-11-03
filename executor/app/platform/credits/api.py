"""Public prices, owner-scoped statements and super-admin billing control."""
from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import CurrentUser, current_user, require_super_admin
from .ledger import BillingError, get_billing, model_key
from .runtime import get_billing_runs, sse

router = APIRouter(prefix="/api")
admin_router = APIRouter(prefix="/api/admin/billing")


def active_model():
    from app.platform.llm_config.loader import get_active_llm_config
    from app.platform.core.settings import get_settings
    config = get_active_llm_config()
    return model_key(config.model if config and config.model else get_settings().writer_model)


async def prepare_operation(payload, user_id, thread_id):
    ledger = get_billing()
    request_id = getattr(payload, "message_id", None)
    fingerprint = hashlib.sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if request_id and ledger.find_request(user_id, request_id):
        existing = ledger.reserve(user_id=user_id, thread_id=thread_id, request_id=request_id,
                                  budget=getattr(payload, "billing_budget", None), model="", fingerprint=fingerprint)
        return {**existing, "_replay_only": True}
    if not ledger.config()["enabled"]:
        return None
    if getattr(payload, "billing_protocol", None) != 2 or not getattr(payload, "message_id", None):
        raise BillingError("客户端版本过旧，请更新桌面端后再创作；已有内容仍可阅读。", "billing_upgrade_required", 426)
    budget = getattr(payload, "billing_budget", None)
    if budget is None:
        raise BillingError("开始前请确认本次最多使用的积分", "budget_required", 426)
    runs = get_billing_runs()
    preparation, cancelled = runs.begin_preparation(user_id, thread_id)
    try:
        model = await asyncio.to_thread(active_model)
        if cancelled.is_set():
            from .runtime import OperationStopped
            raise OperationStopped()
        return ledger.reserve(user_id=user_id, thread_id=thread_id, request_id=payload.message_id,
                              budget=budget, model=model, fingerprint=fingerprint)
    finally:
        runs.end_preparation(preparation)


async def run_json_operation(operation, action):
    if operation is None:
        return await action()
    async def source():
        result = await action()
        value = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        yield sse("final", value)
    run = get_billing_runs().submit(operation, source)
    # Losing the HTTP connection must not cancel the accepted logical task.
    await asyncio.shield(run.task)
    if run.result is not None:
        return run.result
    saved = get_billing().operation(operation["operation_id"], operation["user_id"])
    if saved.get("result") is not None:
        return saved["result"]
    if saved["status"] == "budget_exhausted":
        from .runtime import BudgetExhausted
        raise BudgetExhausted()
    if saved["status"] == "stopped":
        raise BillingError("操作已停止，按已用积分结算。", "user_stopped", 409)
    raise BillingError(run.error or "本次操作已结束，请查看账单和已有内容。", "operation_ended", 409)


def public_config():
    config = get_billing().config()
    return {key: config[key] for key in ("enabled", "default_budgets", "contact_qq", "protocol")}


def user_operation(operation):
    result = {key: value for key, value in operation.items() if key != "prices"}
    if "calls" in result:
        result["calls"] = [{key: value for key, value in call.items() if key not in {"cost", "platform_cost"}}
                           for call in result["calls"]]
    return result


@router.get("/credits/rules")
def public_rules():
    return public_config()


@router.get("/me/billing")
def my_billing(user: CurrentUser = Depends(current_user)):
    ledger = get_billing()
    return {**public_config(), **ledger.balance(user.user_id),
            "operations": ledger.operations(user.user_id), "transactions": ledger.transactions(user.user_id)}


@router.get("/me/billing/operations/{operation_id}")
def my_operation(operation_id: str, user: CurrentUser = Depends(current_user)):
    return user_operation(get_billing().operation(operation_id, user.user_id))


@router.get("/me/billing/operations/{operation_id}/events")
async def operation_events(operation_id: str, user: CurrentUser = Depends(current_user)):
    operation = get_billing().operation(operation_id, user.user_id)
    runs = get_billing_runs()
    run = runs.runs.get(operation_id)
    async def saved():
        if operation.get("result") is not None:
            yield sse(operation.get("result_event") or "final", operation["result"])
        yield sse("billing", {key: value for key, value in operation.items() if key not in {"calls", "prices", "result"}})
    return StreamingResponse(runs.stream(run) if run else saved(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ModelPrice(BaseModel):
    input_per_million: str | float
    output_per_million: str | float
    cached_per_million: str | float


class BillingConfig(BaseModel):
    enabled: bool
    default_budgets: dict[str, str | float]


@admin_router.get("/config")
def billing_config(admin: CurrentUser = Depends(require_super_admin)):
    return {**get_billing().config(), "prices": get_billing().prices()}


@admin_router.put("/config")
def set_config(payload: BillingConfig, admin: CurrentUser = Depends(require_super_admin)):
    return get_billing().configure(**payload.model_dump(), admin_id=admin.user_id)


@admin_router.put("/prices/{model:path}")
def set_price(model: str, payload: ModelPrice, admin: CurrentUser = Depends(require_super_admin)):
    return get_billing().publish_price(model, **payload.model_dump(), admin_id=admin.user_id)


@admin_router.get("/operations")
def all_operations(limit: int = Query(50, ge=1, le=200), user_id: str | None = None,
                   admin: CurrentUser = Depends(require_super_admin)):
    return get_billing().operations(user_id=user_id, limit=limit)


@admin_router.get("/operations/{operation_id}")
def admin_operation(operation_id: str, admin: CurrentUser = Depends(require_super_admin)):
    return get_billing().operation(operation_id)
