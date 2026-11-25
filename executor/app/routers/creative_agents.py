from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import CurrentUser, current_user
from app.domains.writing.conversation_log import ConversationLog
from app.domains.writing.creative_scope import ACTIVE, Book, flush_unfinished_draft
from app.routers.context import get_agent_service, get_thread_store, get_trace_recorder
from app.schemas.screenplay import ScreenplayGenerateRequest
from contracts.creative_harness import AGENTS, entry_missing

router = APIRouter(prefix="/workspaces/{workspace_id}/agents/{agent}")


def book_for(workspace_id: str, agent: str, user: CurrentUser = Depends(current_user)):
    if agent not in AGENTS or agent != os.getenv("WRITING_AGENT_ID"):
        raise HTTPException(404, "创作入口不存在")
    ws = get_thread_store().get_workspace(user.user_id, workspace_id)
    if ws is None or not (Path(ws.workspace_path) / ".writing/four-agents").is_file():
        raise HTTPException(404, "作品不存在或未启用四个创作入口")
    return Book(ws.workspace_path, owner=user.user_id, workspace=workspace_id)


def _thread(book, agent, thread_id, user):
    row = get_thread_store().get_thread(user.user_id, thread_id)
    if row is None or Path(row.workspace_path) != book.root or thread_id not in book.threads(agent):
        raise HTTPException(404, "会话不存在")
    return row.model_copy(update={"workspace_path": str(book.session_dir(agent, thread_id))})


class Target(BaseModel):
    volume: int = Field(default=1, ge=1)
    chapter: int = Field(default=1, ge=1)
    session_name: str | None = None


class Instruction(ScreenplayGenerateRequest):
    volume: int = Field(default=1, ge=1)
    chapter: int = Field(default=1, ge=1)


class Resolution(BaseModel):
    choice: Literal["current", "proposed"]


@router.get("/entry")
def entry(workspace_id: str, agent: str, volume: int = Query(1, ge=1), chapter: int = Query(1, ge=1),
          thread_id: str | None = None, book: Book = Depends(book_for), user: CurrentUser = Depends(current_user)):
    book.export()
    ids = book.threads(agent)
    if thread_id and thread_id not in ids:
        raise HTTPException(404, "会话不存在")
    rows = {t.thread_id: t for t in get_thread_store().list_threads(user.user_id, workspace_id)}
    threads = [rows[k] for k in ids if k in rows]
    last = threads[0].thread_id if threads else None
    task = book.task(agent, thread=thread_id or last)
    return {"agent": agent, "missing": entry_missing(book.root, agent, volume, chapter), "threads": threads,
            "last_thread_id": last, "task": task.info() if task else None, "conflicts": book.conflicts(agent)}


@router.post("/threads")
def new_thread(workspace_id: str, agent: str, target: Target, book: Book = Depends(book_for), user: CurrentUser = Depends(current_user)):
    book.export()
    if missing := entry_missing(book.root, agent, target.volume, target.chapter):
        raise HTTPException(409, {"missing": missing})
    row = get_thread_store().create_thread(user.user_id, workspace_id, target.session_name)
    book.register_thread(agent, row.thread_id)
    return row


@router.get("/threads/{thread_id}/conversation")
def conversation(agent: str, thread_id: str, book: Book = Depends(book_for), user: CurrentUser = Depends(current_user)):
    row = _thread(book, agent, thread_id, user)
    book.touch(agent, thread_id)
    return ConversationLog(Path(row.workspace_path)).read(thread_id)


def _get_task(book, agent, task_id):
    task = book.task(agent, task_id=task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/tasks/{task_id}")
def task_status(agent: str, task_id: str, book: Book = Depends(book_for)):
    return _get_task(book, agent, task_id).info()


@router.post("/tasks/{task_id}/stop")
async def stop(agent: str, task_id: str, book: Book = Depends(book_for), user: CurrentUser = Depends(current_user)):
    task = _get_task(book, agent, task_id)
    if task.row["status"] in ("running", "awaiting_input", "interrupted", "new"):
        task.mark("stopped")
        from app.platform.credits.runtime import get_billing_runs
        get_billing_runs().request_stop(user.user_id, f"creative:{agent}:{task.row['thread']}", task.row["trace"])
        from app.domains.writing.creative_worker import cancel_private_instruction
        cancel_private_instruction(task.id)
        if trace_id := task.row["trace"]:
            get_trace_recorder().cancel_run_task(trace_id)
    return task.info()


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(agent: str, conflict_id: int, body: Resolution, book: Book = Depends(book_for)):
    try:
        book.resolve(agent, conflict_id, body.choice)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"conflicts": book.conflicts(agent)}


def _run_private(task, row, payload, owner_id, traceparent):
    from app.domains.writing.creative_worker import run_private_instruction
    return run_private_instruction(task, row, payload, owner_id, traceparent, binding_service=get_agent_service())


def _owned_trace(row, trace_id):
    if not trace_id:
        return
    recorder = get_trace_recorder()
    if recorder.register_external_run(trace_id, row.workspace_path):
        run = recorder.find_run_by_trace_id(trace_id, workspace_path=row.workspace_path)
        if run and run.thread_id == row.thread_id:
            return run


def _notify_private_trace(task, row):
    from app.platform.trace.recorder import _notify_evolution
    trace_id = task.row["trace"]
    try:
        if run := _owned_trace(row, trace_id):
            _notify_evolution(row, trace_id, run.status, run.model_dump(mode="json"))
    except (OSError, ValueError):
        logging.getLogger(__name__).exception("创作追踪交付失败")


async def _stream(task, row, payload, user, request, *, resume):
    from app.platform.credits.api import prepare_operation
    from app.platform.credits.runtime import get_billing_runs
    previous = task.row["status"]
    if resume and previous == "interrupted":
        # 上次没能正常收尾（如服务重启）：先把检查点里没进文件的句子补进本章，再从断点续跑。
        await flush_unfinished_draft(task, row.workspace_path)
    try:
        task.begin(resume=resume)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    from app.platform.credits.ledger import FINAL, BillingError, get_billing
    from decimal import Decimal
    ledger = get_billing()
    operation = None
    try:
        request_id = payload.message_id or task.row["msg"]
        bill_payload = payload
        if task.recovering:
            attempts = task.prior_attempts()
            source = attempts[-1]["source"] if attempts else request_id
            if ledger.config()["enabled"] and payload.billing_budget is not None:
                remaining = Decimal(str(payload.billing_budget))
                for prior in attempts:
                    if prior["source"] != source:
                        continue
                    existing = ledger.find_request(user.user_id, prior["request"])
                    if existing:
                        if existing["status"] not in FINAL:
                            raise BillingError("上次用量正在结算，请稍后继续", "settlement_pending", 409)
                        remaining -= Decimal(str(existing["charged"]))
                if remaining <= 0:
                    raise BillingError("本次预算已用尽，请确认新的创作预算", "budget_exhausted", 402)
                bill_payload = payload.model_copy(update={"billing_budget": str(remaining)})
            request_id = uuid.uuid4().hex
            bill_payload = bill_payload.model_copy(update={"message_id": request_id})
        else:
            source = request_id
        task.note_attempt(source, request_id, payload.model_dump())
        operation = await prepare_operation(bill_payload, user.user_id, f"creative:{task.agent}:{row.thread_id}")
        if task.book.task(task.agent, task_id=task.id).row["status"] == "stopped":
            if operation is not None and not operation.get("_replay_only"):
                ledger.finish(operation["operation_id"], "stopped")
            raise HTTPException(409, "任务已停止")
        if operation and operation.get("_replay_only"):
            raise HTTPException(409, "该条指令已有账单，请核对原任务")
    except BaseException:
        task.mark(previous)
        raise

    async def events():
        token = ACTIVE.set(task)
        try:
            yield "event: creative_task\ndata: " + json.dumps(task.info(), ensure_ascii=False) + "\n\n"
            async for frame in _run_private(task, row, payload, user.user_id, request.headers.get("traceparent")):
                if frame.startswith("event: "):
                    head, data, *_ = frame.split("\n")
                    obj = json.loads(data.removeprefix("data: "))
                    if head == "event: status" and obj.get("status") == "started":
                        trace_id = obj.get("trace_id")
                        if not _owned_trace(row, trace_id):
                            raise ValueError("创作记录与本会话不符")
                        task.mark("running", trace_id)
                        await asyncio.to_thread(get_agent_service()._issue_run_binding, trace_id, "user_generation")
                    elif head == "event: interrupt":
                        task.interrupt(obj)
                    elif head == "event: final":
                        task.mark("completed")
                yield frame
        finally:
            if task.row["status"] == "running":
                task.mark("interrupted")
            try:
                # 无论怎样结束（含作者主动停止），已写出的句子都要进文件。
                await flush_unfinished_draft(task, row.workspace_path)
                await asyncio.to_thread(_notify_private_trace, task, row)
            finally:
                ACTIVE.reset(token)

    if operation is not None:
        runs = get_billing_runs()
        stream = runs.stream(runs.submit(operation, events))
    else:
        stream = events()
    return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/threads/{thread_id}/generate/stream")
async def generate(agent: str, thread_id: str, payload: Instruction, request: Request, book: Book = Depends(book_for),
                   user: CurrentUser = Depends(current_user)):
    row = _thread(book, agent, thread_id, user)
    if payload.thread_id != thread_id or payload.character_repair or payload.fresh_session or payload.demand_md:
        raise HTTPException(422, "创作指令与当前入口不符")
    if getattr(payload, "resume", None) is not None:
        task = book.task(agent, thread=thread_id)
        if not task or task.row["status"] != "awaiting_input" or not task.row["trace"] or task.row["trace"] != payload.trace_id:
            raise HTTPException(409, "本会话没有对应的待确认任务")
        return await _stream(task, row, payload, user, request, resume=True)
    if not payload.primary_text().strip():
        raise HTTPException(422, "请输入创作指令")
    from app.platform.agent.loader import load_current_package, production_commit
    await asyncio.to_thread(load_current_package)
    revision = production_commit()
    if not revision:
        raise HTTPException(503, "本套已发布版本暂时不可用")
    try:
        task = book.start(agent, thread_id, payload.message_id or uuid.uuid4().hex, payload.model_dump(),
                          payload.volume, payload.chapter, revision)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return await _stream(task, row, payload, user, request, resume=False)


@router.post("/tasks/{task_id}/continue")
async def continue_task(agent: str, task_id: str, request: Request, book: Book = Depends(book_for),
                        user: CurrentUser = Depends(current_user)):
    task = _get_task(book, agent, task_id)
    if task.row["status"] != "interrupted":
        raise HTTPException(409, "只有已中断任务可以继续")
    row = _thread(book, agent, task.row["thread"], user)
    attempts = task.prior_attempts()
    saved_payload = attempts[-1]["payload"] if attempts else task.row["payload"]
    payload = Instruction.model_validate(json.loads(saved_payload))
    return await _stream(task, row, payload, user, request, resume=True)
