"""threads 路由（PR-14 从 main.py 抽出）。

线程 CRUD + outline + checkpoint + trace 查询/删除。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CurrentUser, current_user
from app.routers.context import (
    get_agent_service,
    get_character_service,
    get_thread_store,
    get_trace_recorder,
)
from app.schemas.checkpoint import CheckpointState
from app.schemas.screenplay import (
    AwaitingInputDetail,
    RunStateOption,
    ThreadCreateRequest,
    ThreadRunStateResponse,
    ThreadSummary,
    ThreadUpdateRequest,
)
from app.platform.trace import TraceDetail, TraceRunSummary

router = APIRouter()


@router.get("/threads", response_model=list[ThreadSummary])
def list_threads(workspace_id: str | None = None, user: CurrentUser = Depends(current_user)) -> list[ThreadSummary]:
    return get_thread_store().list_threads(user.user_id, workspace_id)


@router.post("/threads", response_model=ThreadSummary)
def create_thread(payload: ThreadCreateRequest, user: CurrentUser = Depends(current_user)) -> ThreadSummary:
    thread_store = get_thread_store()
    try:
        return thread_store.create_thread(user.user_id, payload.workspace_id, payload.session_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/threads/{thread_id}", response_model=ThreadSummary)
def update_thread(thread_id: str, payload: ThreadUpdateRequest, user: CurrentUser = Depends(current_user)) -> ThreadSummary:
    thread_store = get_thread_store()
    try:
        thread = thread_store.update_thread_name(user.user_id, thread_id, payload.session_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, str | bool]:
    thread_store = get_thread_store()
    trace_recorder = get_trace_recorder()
    thread = thread_store.get_thread(user.user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        trace_recorder.delete_thread_runs(thread)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    deleted = thread_store.delete_thread(user.user_id, thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread not found")
    await get_agent_service().delete_thread_checkpoint(thread_id, owner_id=user.user_id)
    await get_character_service().delete_thread_checkpoint(thread_id)
    return {"status": "ok", "deleted": thread_id}


@router.get("/threads/{thread_id}/checkpoint", response_model=CheckpointState)
async def get_thread_checkpoint(thread_id: str, user: CurrentUser = Depends(current_user)) -> CheckpointState:
    thread_store = get_thread_store()
    thread = thread_store.get_thread(user.user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return await get_agent_service().get_thread_checkpoint(thread_id, owner_id=user.user_id)


@router.get("/threads/{thread_id}/traces", response_model=list[TraceRunSummary])
def list_thread_traces(thread_id: str, user: CurrentUser = Depends(current_user)) -> list[TraceRunSummary]:
    thread_store = get_thread_store()
    trace_recorder = get_trace_recorder()
    thread = thread_store.get_thread(user.user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return trace_recorder.list_runs(thread)


@router.get("/threads/{thread_id}/traces/{trace_id}", response_model=TraceDetail)
def get_thread_trace(thread_id: str, trace_id: str, user: CurrentUser = Depends(current_user)) -> TraceDetail:
    thread_store = get_thread_store()
    trace_recorder = get_trace_recorder()
    thread = thread_store.get_thread(user.user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        detail = trace_recorder.read_run(thread, trace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return detail


def _reconstruct_awaiting_input(
    trace_recorder, thread, trace_id: str
) -> AwaitingInputDetail | None:
    """从 trace 事件重建 interrupt 提问 payload（question/options/multi_select）。

    SSE interrupt 帧只经当时连接送达，断连后客户端无法重放；本函数给恢复
    对账端点重建同一份数据。链路：最后一个 hitl(awaiting_input) 事件的
    tool_call_id → 同 id 的 tool_start 事件 → tool_args（read_run 已 hydrate）。
    重建失败（trace 缺失 / 事件形态不符 / 无 question）返回 None——客户端对
    awaiting 但无详情的 run 保持现状，不误渲染拍板卡。
    """
    detail = trace_recorder.read_run(thread, trace_id)
    if detail is None:
        return None
    hitl_events = [
        e for e in detail.events
        if e.type == "hitl" and e.status == "awaiting_input" and e.tool_call_id
    ]
    if not hitl_events:
        return None
    hitl = hitl_events[-1]
    for event in reversed(detail.events):
        if event.type != "tool_start" or event.tool_call_id != hitl.tool_call_id:
            continue
        args = event.tool_args if isinstance(event.tool_args, dict) else None
        question = str(args.get("question") or "") if args else ""
        if not question:
            # 形态不符（如图像评审的 interrupt payload 不走 question/options），
            # 重建不了完整拍板卡，宁缺毋滥。
            return None
        raw_options = args.get("options")
        options: list[RunStateOption] | None = None
        if isinstance(raw_options, list):
            options = [
                RunStateOption(label=str(o.get("label", "")), description=str(o.get("description", "")))
                for o in raw_options if isinstance(o, dict)
            ] or None
        return AwaitingInputDetail(
            kind="choice",
            question=question,
            options=options,
            multi_select=bool(args.get("multi_select", False)),
            source=f"{event.agent_name}-confirm" if event.agent_name else None,
            asked_at=hitl.timestamp,
        )
    return None


@router.get("/threads/{thread_id}/run-state", response_model=ThreadRunStateResponse)
def get_thread_run_state(thread_id: str, user: CurrentUser = Depends(current_user)) -> ThreadRunStateResponse:
    """恢复对账：该会话最新 run 的服务端真相（D-对账）。

    桌面端重启/刷新恢复会话时调用，纠正本地陈旧的「正在执行...」占位消息——
    interrupt 帧断连丢失后，两端状态机会永久分叉，唯一收敛途径是回查服务端。
    """
    thread_store = get_thread_store()
    trace_recorder = get_trace_recorder()
    thread = thread_store.get_thread(user.user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    runs = trace_recorder.list_runs(thread)
    if not runs:
        return ThreadRunStateResponse(trace_id=None, status="idle")
    latest = runs[0]
    if latest.status != "awaiting_input":
        return ThreadRunStateResponse(trace_id=latest.trace_id, status=latest.status)
    try:
        awaiting = _reconstruct_awaiting_input(trace_recorder, thread, latest.trace_id)
    except Exception:  # noqa: BLE001 — 对账重建尽力而为，失败退化为无详情
        awaiting = None
    return ThreadRunStateResponse(trace_id=latest.trace_id, status=latest.status, awaiting=awaiting)


@router.delete("/threads/{thread_id}/traces/{trace_id}")
def delete_thread_trace(thread_id: str, trace_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, str]:
    thread_store = get_thread_store()
    trace_recorder = get_trace_recorder()
    thread = thread_store.get_thread(user.user_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        deleted = trace_recorder.delete_run(thread, trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"status": "ok", "deleted": trace_id}
