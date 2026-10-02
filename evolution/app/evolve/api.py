"""evolve API —— 进化触发 + 查询 + SSE + 发版/丢弃（三功能解耦，决策 S8/S9）。

端点（自由启动改造，REQ-20260921-124733 DEC-004）：
  POST /api/evolve/start-converse               触发对话式进化（无必填业务输入；
                                                benchmark_batch_id 可选附带弱点视图）
  GET  /api/evolve/sessions                     session 列表（最新在前）
  GET  /api/evolve/sessions/{id}                单 session 详情
  GET  /api/evolve/sessions/{id}/messages       消息分页
  POST /api/evolve/sessions/{id}/messages       用户发言（converse round）
  POST /api/evolve/sessions/{id}/finalize       拍板（finalize round）
  POST /api/evolve/sessions/{id}/stop           停止
  POST /api/evolve/sessions/{id}/publish        发版（S9/S12：git commit + bootstrap config + snapshot）
  POST /api/evolve/sessions/{id}/discard        丢弃（S9：git reset 回 production + 状态推进）

执行模型（D3/D4：trace 统一接管 SSE）：
  start 时注入 recorder 到 ctx → 后台 task 跑 inspect round → recorder 产 trace 事件 →
  前端按消息/事件端点轮询。SessionEvents 已删除。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from contracts.cancel_state import HARD_STOP_DEADLINE_SECONDS, is_terminal
from app.core import db
from app.evolve import db as ev_db
from app.evolve.ctx import (
    ACTIVE_STATUSES,
    LANDING_STATUSES,
    STATUS_CONVERSING,
    STATUS_FINALIZING,
    STATUS_RUNNING,
    EvolveContext,
)
from app.trace.recorder import EvolutionTraceRecorder
from app.trace.facts import (
    append_release_event,
    latest_release_status,
)

logger = logging.getLogger("evolution.evolve.api")

router = APIRouter(tags=["evolve"])

# session_id → 后台进化 task。stop 端点靠它 cancel 正在跑的 Agent。
# 原先用 FastAPI BackgroundTasks.add_task 不持有 task 引用，外部无法取消；
# 改用 asyncio.create_task 后存这里，stop 才能调 task.cancel()。
_running_tasks: dict[str, asyncio.Task] = {}


def _reject_if_round_running(session_id: str, action: str) -> None:
    """FR-006 / EDGE-005 并发保护：同一 session 有未完成 round 时拒绝新请求。

    publish / send_message / finalize 任一在跑时，后到的并发请求返回 409，
    避免产生孤儿 task + checkpoint 竞态 + registry.json 丢失更新。
    已完成（done/cancelled）的旧 task 不阻塞——清理后放行。

    Args:
        session_id: session id
        action: 触发动作名（用于错误信息，如 '发版' / '发消息' / '拍板'）
    """
    task = _running_tasks.get(session_id)
    if task is not None and not task.done():
        raise HTTPException(
            status_code=409,
            detail=(
                f"session {session_id} 有未完成的进化 round，无法{action}。"
                f"等当前 round 完成或先 stop 后重试（FR-006 并发保护）。"
            ),
        )


def get_recorder() -> EvolutionTraceRecorder | None:
    """获取全局 recorder 实例（main.py lifespan 注入到 app.state）。"""
    from app.main import app
    return getattr(app.state, "trace_recorder", None)


class EvolveStartRequest(BaseModel):
    """进化会话启动请求（Agent 绑定模式，REQ-20261001-131018 DEC-002）。

    会话必须挂在进化 Agent（绑作品）下创建；自由启动入口已退役。
    """

    # FR-005（REQ-20260921-124733）：可选附带评测批次 id，inspect round 注入全局弱点视图。
    # 未附带或批次无数据 → 会话正常启动（降级不阻断）。
    benchmark_batch_id: str | None = None


class EvolveStartResponse(BaseModel):
    session_id: str
    trace_id: str
    status: str  # started_converse


# ── 触发 ────────────────────────────────────────────────────


@router.post(
    "/evolve/agents/{agent_id}/sessions",
    response_model=EvolveStartResponse,
    status_code=202,
)
async def start_agent_session(
    agent_id: str, req: EvolveStartRequest
) -> EvolveStartResponse:
    """在进化 Agent 下开启新会话（FR-002，DEC-002 入口统一）。

    流程：
      1. Agent 存在 + active + 作品未被删（FR-009：已删则拒绝开新会话）
      2. 组装开场注入快照（FR-004/DEC-005：作品概览 + trace/产物规模；
         概览拉取失败降级不阻断）
      3. 后台跑 inspect round（探查 + 开场白 → 转 conversing）

    探查/对话阶段可与其他会话并行（DEC-004）；落地互斥在 finalize 端点校验。
    """
    from app.evolve import agents_repo

    agent = agents_repo.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"进化 Agent {agent_id} 不存在")
    if agent.get("status") != "active":
        raise HTTPException(
            status_code=409,
            detail=f"进化 Agent 已归档，无法开新会话（先恢复或另建 Agent）",
        )
    if agent.get("work_deleted_at"):
        raise HTTPException(
            status_code=409,
            detail="绑定作品已被删除，无法开新会话（DEC-010；已有会话可继续基于历史数据分析）",
        )

    work_context = _assemble_work_context(agent)
    if work_context.get("probe") == "missing":
        # 探测发现作品已删（列表缓存未标记的场景）——补标记并拒绝
        agents_repo.mark_work_deleted(agent_id)
        raise HTTPException(
            status_code=409,
            detail="绑定作品已被删除，无法开新会话（DEC-010）",
        )

    session_id, ctx = _prepare_evolve_session(agent, work_context)

    # FR-006（REQ-20260921-124733）：附带评测批次时注入全局弱点视图（降级不阻断）
    if req.benchmark_batch_id:
        from app.benchmark import report as bench_report
        summary = bench_report.build_summary_for_evolve(req.benchmark_batch_id)
        if summary is not None:
            ctx.eval_snapshot["benchmark_report"] = summary
            ev_db.update_session(session_id, benchmark_batch_id=req.benchmark_batch_id)
            logger.info(
                "进化 session %s 附带评测批次 %s 弱点视图（维度 %d 个）",
                session_id, req.benchmark_batch_id, len(summary.get("weakest_dimensions", [])),
            )
        else:
            logger.warning(
                "评测批次 %s 无可聚合数据，进化会话正常启动（无全局视图）",
                req.benchmark_batch_id,
            )

    # 后台跑 inspect round（探查 + 开场白 → 转 conversing）
    from app.evolve.agent.agent import run_inspect_round
    task = asyncio.create_task(_run_round_bg(ctx, run_inspect_round, ctx.trace_id))
    _running_tasks[session_id] = task

    logger.info(
        "进化 session 启动（Agent=%s 作品=%s）: session=%s benchmark_batch=%s",
        agent_id, agent.get("workspace_id"), session_id, req.benchmark_batch_id,
    )
    return EvolveStartResponse(
        session_id=session_id, trace_id=ctx.trace_id,
        status="started_converse",
    )


def _assemble_work_context(agent: dict[str, Any]) -> dict[str, Any]:
    """组装开场注入快照（FR-004/DEC-005）：作品概览 + trace/产物规模。

    概览来自 executor（不可达时降级注明缺失，不阻断会话——FR-004 失败语义）；
    规模统计来自进化侧已摄入数据。进化点清单与发布摘要不入快照（prompt 组装时
    动态取，保证新鲜）。
    """
    from app.evolve.executor_client import (
        ExecutorUnavailableError,
        WorkNotFoundError,
        fetch_workspace,
    )

    overview: dict[str, Any] | None = None
    probe = "ok"
    try:
        overview = fetch_workspace(agent["workspace_id"])
    except WorkNotFoundError:
        probe = "missing"
    except ExecutorUnavailableError as exc:
        probe = "unreachable"
        logger.warning(
            "Agent %s 开场概览拉取失败（降级注入，不阻断）: %s",
            agent["agent_id"], exc,
        )

    # trace 规模（排除进化端自观测 trace——workspace 固定为 'evolution'，天然隔离）
    trace_row = db.query_one(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status IN ('failed','interrupted') THEN 1 ELSE 0 END) AS failed,
                  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
           FROM runs
           WHERE workspace_id = ? AND run_purpose NOT LIKE 'evolution%'""",
        (agent["workspace_id"],),
    )
    # 产物修订规模（进化侧摄入的版本史）
    artifact_row = db.query_one(
        """SELECT COUNT(DISTINCT a.logical_key) AS keys, COUNT(r.artifact_revision_id) AS revisions
           FROM artifacts a LEFT JOIN artifact_revisions r ON r.artifact_id = a.artifact_id
           WHERE a.workspace_id = ?""",
        (agent["workspace_id"],),
    )
    return {
        "probe": probe,
        "overview": overview,
        "workspace_id": agent["workspace_id"],
        "trace_stats": {
            "total": trace_row["total"] if trace_row else 0,
            "completed": trace_row["completed"] or 0 if trace_row else 0,
            "failed": trace_row["failed"] or 0 if trace_row else 0,
        },
        "artifact_stats": {
            "logical_keys": artifact_row["keys"] or 0 if artifact_row else 0,
            "revisions": artifact_row["revisions"] or 0 if artifact_row else 0,
        },
    }


def _prepare_evolve_session(
    agent: dict[str, Any], work_context: dict[str, Any],
) -> tuple[str, EvolveContext]:
    """创建进化会话 + 构建上下文（Agent 绑定模式，DEC-002）。"""
    if get_recorder() is None:
        raise HTTPException(status_code=503, detail={
            "message": "Trace recorder unavailable; evolution was not started",
            "integrity_status": "incomplete",
            "missing_fields": ["trace_recorder"],
        })
    session_id = uuid.uuid4().hex[:12]
    ev_db.create_session(
        session_id, case_id="", agent_id=agent["agent_id"], work_context=work_context,
    )
    ctx = _build_evolve_ctx(session_id)
    return session_id, ctx


async def _run_round_bg(
    ctx: EvolveContext,
    round_fn,
    *args,
) -> None:
    """通用后台 round 执行器（决策 T2 按需触发）。

    round 函数自己负责状态推进 + recorder 收尾，本函数只做异常兜底 + task 注册表清理。

    Args:
        ctx: 进化上下文
        round_fn: round 函数（run_inspect_round / run_converse_round / run_finalize_round）
        *args: 传给 round_fn 的位置参数（如 trace_id / user_message）
    """
    try:
        result = await round_fn(ctx, *args)
        # cancelled 是用户停止的合法终态，不算失败
        if result.get("status") not in (
            "done", "conversing", "pending_review", "cancelled", None,
        ):
            ev_db.update_session(ctx.session_id, status="failed")
    except asyncio.CancelledError:
        logger.info("进化 session %s round %s 被取消", ctx.session_id, round_fn.__name__)
        # round 函数自己处理 cancelled；这里兜底（取消在进入 round 前命中）
        ev_db.update_session(ctx.session_id, status="cancelled")
        raise
    except Exception as e:
        logger.exception("进化 session %s round %s 异常", ctx.session_id, round_fn.__name__)
        ev_db.update_session(ctx.session_id, status="failed")
    finally:
        _running_tasks.pop(ctx.session_id, None)


def _find_landing_session(exclude_session_id: str | None = None) -> dict[str, Any] | None:
    """查占用落地通道的会话（DEC-004：聊天并行、落地排队）。

    落地通道 = status ∈ LANDING_STATUSES（finalizing / pending_review）的会话，
    全局唯一。harness 工作目录与发布链共享一份，落地必须串行；探查/对话
    阶段（running/conversing）不受此限，可跨 Agent 多会话并行。

    Returns:
        占用方 session dict（含 session_id/agent_id/status），无占用返回 None。
    """
    sessions = ev_db.list_sessions(limit=50)
    for s in sessions:
        if not isinstance(s, dict) or s.get("status") not in LANDING_STATUSES:
            continue
        if exclude_session_id and s.get("session_id") == exclude_session_id:
            continue
        return s
    return None


def _build_evolve_ctx(session_id: str) -> EvolveContext:
    """构建进化上下文（Agent 绑定模式：从 session 行取 Agent/作品/注入快照）。

    业务证据来源：开场注入快照（work_context，DEC-005）+ 可选评测弱点视图
    （benchmark_report）+ Agent 探查所见 + 用户对话。
    """
    ctx = EvolveContext(session_id=session_id)
    ctx.recorder = get_recorder()
    ctx.trace_id = ""  # 无被测 trace；自观测录像走 trace_id_self
    ctx.origin_layer = None
    ctx.eval_snapshot = {}
    _bind_agent_fields(ctx)
    return ctx


def _bind_agent_fields(ctx: EvolveContext) -> None:
    """从 session 行回填 Agent 绑定字段（agent_id/workspace_id/work_context）。"""
    session = ev_db.get_session(ctx.session_id)
    if not session:
        return
    ctx.agent_id = session.get("agent_id") or ""
    ctx.workspace_id = ""
    ctx.work_context = session.get("work_context") or {}
    if ctx.agent_id:
        from app.evolve import agents_repo
        agent = agents_repo.get(ctx.agent_id)
        if agent:
            ctx.workspace_id = agent.get("workspace_id") or ""


def _resolve_origin_layer(trace_id: str) -> str | None:
    """查 trace 所属的数据集层（数据闭环 F1，golden|growing）。

    通过 manual_tests.origin_layer 反查（测试发起时写入）。
    非 benchmark/测试 trace（如用户原始 trace）返回 None。
    """
    row = db.query_one(
        "SELECT origin_layer FROM manual_tests WHERE trace_id=? AND origin_layer IS NOT NULL LIMIT 1",
        (trace_id,),
    )
    return row["origin_layer"] if row else None


def get_system_prompt() -> dict[str, Any]:
    """返回进化 Agent 的静态架构蓝图（决策 F/Q/R）。

    前端「架构蓝图」Tab 的数据源——打开进化页即可调用，不依赖任何 session。
    返回 STATIC_BLUEPRINT（7 段全景 + 角色定位 + 能力边界 + 对创作 Agent 的理解）。
    动态注入部分（session_id / eval_summary / reflections / memory）不在此返回。

    Returns:
        {blueprint: <markdown 字符串>, version: <服务版本>}
    """
    from app.evolve.agent.prompt import STATIC_BLUEPRINT
    return {
        "blueprint": STATIC_BLUEPRINT,
        "version": "v0.2.24",
    }


@router.get("/evolve/sessions/{session_id}/messages")
def get_messages(session_id: str, after_seq: int | None = None) -> dict[str, Any]:
    """列出 session 的对话消息（决策 H/T6，前端刷新恢复）。

    旧会话（无 evolve_messages 记录）返回空列表——前端据此识别"旧版会话"
    并提示用户（决策 S）。

    Args:
        session_id: session id
        after_seq: 增量拉取——只返回 seq > after_seq 的消息；None = 全量
    Returns:
        {messages: [EvolveMessage, ...]}
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")

    from app.evolve.evolve_repo import EvolveMessagesRepo
    messages = EvolveMessagesRepo.list_by_session(session_id, after_seq=after_seq)
    return {"messages": messages}


@router.get("/evolve/sessions/{session_id}/points")
def get_points(session_id: str) -> dict[str, Any]:
    """列出进化点清单（决策 M/T7，右侧浮窗数据源）。

    Agent 绑定模式（FR-008）：返回该会话所属 Agent 名下全部进化点（跨会话
    累积——会话 1 提的点在会话 2 的浮窗同样可见）。旧会话按 session 返回。

    Returns:
        {points: [EvolvePoint, ...], accepted_count: <int>}
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")

    from app.evolve.evolve_repo import EvolvePointsRepo
    if session.get("agent_id"):
        points = EvolvePointsRepo.list_by_agent(session["agent_id"])
    else:
        points = EvolvePointsRepo.list_by_session(session_id)
    accepted_count = sum(1 for p in points if p.get("status") == "accepted")
    return {"points": points, "accepted_count": accepted_count}


@router.get("/evolve/sessions")
def list_sessions(limit: int = 50) -> dict[str, Any]:
    """列出进化 session（最新在前）。"""
    sessions = ev_db.list_sessions(limit=limit)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/evolve/landing-channel")
def get_landing_channel() -> dict[str, Any]:
    """落地通道占用状态（FR-003：进化页呈现谁在占用、处于什么阶段）。

    Returns:
        {occupied: bool, occupier: {session_id, agent_id, status} | None}
    """
    occupier = _find_landing_session()
    return {
        "occupied": occupier is not None,
        "occupier": (
            {
                "session_id": occupier["session_id"],
                "agent_id": occupier.get("agent_id"),
                "status": occupier["status"],
            }
            if occupier else None
        ),
    }


@router.get("/evolve/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    """查单个 session 详情（含内联的 design_doc/change_log/eval_snapshot）。

    审查视图所需数据全部内联到这里，前端一次请求拿全：
      - design_doc：读盘 design_doc.md（解析 front matter → {meta, body}）
      - change_log：读盘 change_log.md（解析 front matter → {meta, body}）
      - eval_snapshot：通过 eval_ref 查 evaluation_sessions，取 findings + scores

    读盘/查询失败时对应字段设 null（R8：残缺不崩，前端走残缺渲染）。
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")

    # 内联 design_doc（方案子代理产出）
    session["design_doc"] = _try_read_doc(session.get("design_doc_path"))

    # 内联 change_log（执行子代理产出）
    session["change_log"] = _try_read_doc(session.get("change_log_path"))

    # 内联关联评估的 findings + scores（审查证据来源）
    session["eval_snapshot"] = _try_load_eval_snapshot(session.get("eval_ref"))

    return session


def _try_read_doc(path: str | None) -> dict[str, Any] | None:
    """读盘一个 markdown+YAML 文档，返回 {meta, body}。失败返回 None。

    复用 docs._load_doc 的解析逻辑（front matter 分割）。
    """
    if not path:
        return None
    try:
        from app.evolve.docs import _load_doc
        meta, body = _load_doc(path)
        return {"meta": meta, "body": body}
    except FileNotFoundError:
        logger.warning("文档不存在: %s", path)
        return None
    except Exception:
        logger.exception("文档解析失败: %s", path)
        return None


def _load_eval_session_row(eval_ref: str) -> dict[str, Any] | None:
    """直读 evaluation_sessions 表取历史评估行（休眠评估系统仅存的数据回显）。

    findings/scores 是 *_json 列，反序列化后返回；表随 DEC-006 保留。
    """
    row = db.query_one("SELECT * FROM evaluation_sessions WHERE eval_id=?", (eval_ref,))
    if row is None:
        return None
    import json as _json
    ev = dict(row)
    # scores/findings 存 *_json 列；report_md 是内联全文列（无 _json 后缀）
    for col in ("findings", "scores"):
        raw = ev.get(f"{col}_json")
        if raw:
            try:
                ev[col] = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                ev[col] = None
        else:
            ev[col] = None
    return ev


def _try_load_eval_snapshot(eval_ref: str | None) -> dict[str, Any] | None:
    """查关联评估的 findings + scores（审查证据来源，仅历史会话）。

    不带 report_md（太长，审查视图只需 finding 级证据 + 分数对比）。
    """
    if not eval_ref:
        return None
    try:
        ev = _load_eval_session_row(eval_ref)
        if not ev:
            return None
        return {
            "eval_id": ev.get("eval_id"),
            "trace_id": ev.get("trace_id"),
            "findings": ev.get("findings"),
            "scores": ev.get("scores"),
        }
    except Exception:
        logger.exception("查评估快照失败: eval_ref=%s", eval_ref)
        return None


# ── 停止 ────────────────────────────────────────────────────


@router.post("/evolve/sessions/{session_id}/stop")
async def stop_session(session_id: str) -> dict[str, Any]:
    """手动停止运行中的进化 session（FR-006 / NFR-001 / DEC-002）。

    立即标记 cancelling 并返回（DEC-002），后台 asyncio task 在 10 秒时限内
    收敛到 cancelled。

    必须是 async 端点（线上回归修复）：内部 asyncio.create_task 依赖运行中的
    事件循环；同步 def 会跑在线程池线程，create_task 抛 RuntimeError → 500，
    收敛协程 never awaited，会话永久卡 cancelling（RC3）。

    已知边界：Agent 若停在改源码中途，harnesses/repo/ 下可能留脏文件，
    本端点不清理（由用户手动 stash / 重置）。
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    current = session.get("status")
    if is_terminal(current):
        raise HTTPException(
            status_code=409,
            detail=f"session 状态为 {current}，已终态，无需停止",
        )
    # 可停止的非终态：running / conversing / finalizing（pending_review 走 publish/discard）。
    stoppable = {STATUS_RUNNING, STATUS_CONVERSING, STATUS_FINALIZING}
    if current not in stoppable:
        raise HTTPException(
            status_code=400,
            detail=f"session 状态为 {current}，只有 running/conversing/finalizing 可停止",
        )

    # 立即标记 cancelling（DEC-002）。
    ev_db.update_session(session_id, status="cancelling")

    # 后台 asyncio task 做 10 秒硬终止收敛。
    task = _running_tasks.get(session_id)
    asyncio.create_task(_converge_evolve_cancel(session_id, task))
    return {"status": "cancelling", "session_id": session_id}


async def _converge_evolve_cancel(session_id: str, task: asyncio.Task | None) -> None:
    """进化取消收敛：task.cancel → 等 10s → recorder 强制收敛 → 标 cancelled/cancel_timeout。

    CON-003/EDGE-007：超时未退出标 cancel_timeout（诚实告警，不谎报 cancelled）。
    """
    recorder = get_recorder()
    trace_id_self = recorder.get_trace_id_by_session(session_id) if recorder else None

    if task is not None and not task.done():
        task.cancel()

    # CON-003 真实停止确认：以 task.done() 为准——deadline 后仍未退出才 cancel_timeout。
    converged_in_time = True
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=HARD_STOP_DEADLINE_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
    if task is not None and not task.done():
        converged_in_time = False  # deadline 后仍未退出 → cancel_timeout

    if recorder and trace_id_self:
        recorder.cancel_run(trace_id_self, reason="user_stop")

    final_status = "cancelled" if converged_in_time else "cancel_timeout"
    ev_db.update_session(session_id, status=final_status)
    logger.info("进化 session %s 取消收敛完成 status=%s", session_id, final_status)


# ── SSE 实时流 ──────────────────────────────────────────────


# ── trace 稳定性重构：Pull 模式事件流（替代 SSE，设计 20260720_203000）──


class EvolveEventsSinceResponse(BaseModel):
    """进化 session 事件游标拉取响应（Pull 主导）。"""
    frames: list[dict[str, Any]]   # 从 run_meta 派生的 step/log/phase/proposal/finalizing/message_updated 帧
    max_seq: int                    # 本次返回的最大 sequence（前端下次 since_seq）；无事件时 = since_seq
    has_more: bool                  # 是否还有更多事件未拉（罕见，重构后事件密度低）
    session_status: str             # session 当前状态（running/conversing/...），前端据此判断是否继续轮询


@router.get("/evolve/sessions/{session_id}/events/since", response_model=EvolveEventsSinceResponse)
def get_session_events_since(
    session_id: str,
    since_seq: int = Query(0, ge=0, description="返回 sequence > since_seq 的事件"),
    limit: int = Query(500, ge=1, le=1000, description="单次返回上限"),
) -> EvolveEventsSinceResponse:
    """按 sequence 游标拉取进化 session 的事件帧（trace 重构 20260720_154825）。

    实现：从 evolve_sessions.self_trace_id 反查 trace_id → 查 event_payloads 表的
    run_meta 事件 → 用 _trace_event_to_sse 派生成 phase/proposal/finalizing/
    message_updated/step/log 帧。

    重构变更（D1/D3）：
      - 不再有 model_stream token 流帧（每 token 一行的污染源已移除）
      - 新增 message_updated 帧：Agent 消息已落 evolve_messages，前端据此调
        GET /messages 拉权威消息（按 after_seq 增量）
      - 事件密度大幅降低（每轮 LLM/工具只产 1-2 个 run_meta，而非 N 个 token）
      - 前端轮询间隔可放宽到 2s（无 token 流实时性要求）
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")

    self_trace_id = session.get("self_trace_id")
    frames: list[dict[str, Any]] = []
    max_seq = since_seq

    if self_trace_id:
        rows = db.query_all(
            """SELECT sequence, payload_json FROM event_payloads
               WHERE trace_id=? AND sequence>?
               ORDER BY sequence LIMIT ?""",
            (self_trace_id, since_seq, limit + 1),
        )
        has_more = len(rows) > limit
        rows = rows[:limit]

        for r in rows:
            seq = r["sequence"]
            if seq > max_seq:
                max_seq = seq
            try:
                from app.core.models import TraceLogEvent
                evt = TraceLogEvent.model_validate(json.loads(r["payload_json"]))
                frame = _trace_event_to_sse(evt)
                if frame:
                    frame["_seq"] = seq
                    frames.append(frame)
            except Exception:
                # 单条解析失败不阻断其它事件。
                continue
    else:
        has_more = False

    return EvolveEventsSinceResponse(
        frames=frames,
        max_seq=max_seq,
        has_more=has_more,
        session_status=session.get("status", "running"),
    )


def _trace_event_to_sse(event: Any) -> dict[str, Any] | None:
    """trace 事件 → 前端 Pull 帧派生（trace 重构 20260720_154825）。

    重构后只派生以下帧（移除了 sse_frame 桥接 / model_stream）：
      - type=llm_start           → {type:"activity", phase:"thinking"}（FR-005 思考信号）
      - type=tool_start          → {type:"activity", phase:"tool", label}（FR-005 工具信号）
      - tool="phase"            → {type:"phase", phase}（阶段切换）
      - tool="proposal"         → {type:"proposal", ...}（浮窗进化点状态变更）
      - tool="finalizing"       → {type:"finalizing", ...}（落地进度）
      - tool="message_updated"  → {type:"message_updated"}（前端据此调 loadMessages）
      - 含 message 字段（无 tool）→ {type:"log", message}（思考日志）
      - 含 tool 字段（其他）      → {type:"step", **data}（业务步骤，向后兼容）

    设计变更（D1/D3）：
      - 不再有 sse_frame 桥接：token 流不入 trace，事件数与 span 数对齐
      - 新增 message_updated 帧：消息已落 evolve_messages，前端拉权威存储
      - 前端不再维护临时消息 state，全部走 loadMessages 增量拉

    FR-005 活动信号：从 TraceMiddleware 已写入的 llm_start/tool_start span 事件
    派生，不新增事件写入（取证段 5-7 分钟内只读工具不落消息，若无可见信号
    用户无法区分「在跑」与「挂了」）。派生只读 event.type/tool_name 字段，
    不依赖 input（llm_start 的 input 已被 recorder 外化，可能为 null）。
    """
    if event.type == "llm_start":
        return {
            "type": "activity", "phase": "thinking", "label": "正在思考/撰写回复…",
            "ts": str(event.timestamp or ""),
        }
    if event.type == "tool_start":
        name = event.tool_name or "工具"
        return {
            "type": "activity", "phase": "tool", "label": f"正在调用 {name}",
            "ts": str(event.timestamp or ""),
        }

    if event.type != "run_meta" or not event.input:
        return None
    data = event.input if isinstance(event.input, dict) else {}
    tool = data.get("tool", "")

    # trace 重构：消息更新通知（前端调 loadMessages 增量拉）
    if tool == "message_updated":
        return {"type": "message_updated"}

    # 阶段切换事件
    if tool == "phase":
        phase = data.get("phase")
        if phase:
            return {"type": "phase", "phase": phase}
        return None

    # 进化点状态变更（决策 B/M 浮窗实时同步）
    if tool == "proposal":
        return {
            "type": "proposal",
            "action": data.get("action"),
            "point_id": data.get("point_id"),
            "seq": data.get("seq"),
            "target": data.get("target"),
            "chosen_option": data.get("chosen_option"),
        }

    # 落地进度事件（决策 W）
    if tool == "finalizing":
        return {
            "type": "finalizing",
            "event": data.get("status"),  # edit/validate/change_log
            "target": data.get("target"),
            "result": data.get("result"),
        }

    # 旧协议：log + step（保留向后兼容）
    if "message" in data and not tool:
        return {"type": "log", "message": data["message"]}
    if tool:
        return {"type": "step", **data}
    return None


# ── 发版 / 丢弃（Phase 4，S9/S12）────────────────────────────


@router.post("/evolve/sessions/{session_id}/publish")
def publish_session(session_id: str, request: Request) -> dict[str, Any]:
    """单阶段发版（Phase A 平台化）：冻结 candidate → Platform 门禁 → Platform 晋升。

    Phase A（REQ-20260919-202344）起发版原语归 Platform 服务，evolution 只做编排：
      1. 冻结 candidate：git commit 源码 + push bare repo（Platform probe 的 checkout 源）
      2. 门禁：release_gate.probe_candidate → POST {platform_url}/api/release/probe
      3. 晋升：release_gate.promote_release → POST {platform_url}/api/release/promote，
         Platform 内部自查 probe → 账本晋升 → 打包 artifact → 通知 executor reload
         （带重试），全包。
    本地 registry.json 写线退役（只读）：不再注册/晋升 candidate，不再直连
    executor reload——Platform 账本是版本仲裁源。
    保留的安全检查：PromoteResult.commit 与冻结的 source_commit 一致性断言。
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    if session.get("status") != "pending_review":
        raise HTTPException(
            status_code=400,
            detail=f"session 状态为 {session.get('status')}，只有 pending_review 可发版",
        )
    # FR-006 / EDGE-005：并发发版/round 保护——同一 session 有未完成 round 时拒绝，
    # 避免 candidate 冻结与 converse/finalize 竞态 + registry.json 丢失更新。
    _reject_if_round_running(session_id, "发版")

    from app.core import git_ops
    from app.versioning import registry_repo
    from app.versioning.release_gate import (
        ReleasePromoteError,
        probe_candidate,
        promote_release,
    )

    release_id = f"release-{session_id}"
    actor_user_id = getattr(request.state, "user_id", None)

    try:
        candidate = registry_repo.get_version_by_session(session_id)

        # ── 幂等：已发布（candidate 已是 production）直接返回，防重复点击 ──
        # Phase A 起发版不再写 registry（只读），此分支只覆盖切换前已在旧线
        # 落档的 session。
        if candidate is not None and candidate.get("status") == "production":
            ev_db.update_session(session_id, status="published")
            return {
                "status": "activated",
                "release_id": release_id,
                "snapshot_version": candidate["version"],
                "source_commit": candidate["commit_hash"],
                "snapshot_trace_id": candidate.get("snapshot_trace_id"),
            }

        # ── 1. 冻结 candidate（首次发版：commit 源码；legacy 已冻结则复用） ──
        if candidate is None:
            source_commit = git_ops.commit_candidate(
                f"冻结 Harness candidate: session={session_id}",
                required_paths=("middleware/artifact_snapshot.py",),
            )
        else:
            # 旧两阶段残留的已冻结未晋升 candidate：复用冻结时的 commit
            source_commit = candidate["commit_hash"]

        # ── 2. 门禁：Platform probe（干净 checkout 真实装配；rejected → 409） ──
        probe_candidate(source_commit)

        # ── 3. 晋升：Platform promote（账本 + artifact + executor reload 全包） ──
        try:
            promoted = promote_release(
                source_commit,
                version_note=f"进化 session {session_id} 产出的改动",
            )
        except ReleasePromoteError as exc:
            # 原激活失败错误路径：Platform promote 原子（失败不动账本），本地无
            # 可回滚物，不调 restore——session 保持 pending_review 可重试。
            logger.warning("Platform promote 失败: session=%s error=%s", session_id, exc)
            raise HTTPException(
                status_code=502,
                detail={
                    "message": f"candidate 晋升失败（Platform promote）: {exc}",
                    "release_id": release_id,
                    "release_status": "activation_failed",
                    "executor_restore_error": None,
                },
            ) from exc

        # ── 4. 一致性断言：Platform 晋升的 commit 必须等于冻结的 source_commit ──
        if promoted.commit != source_commit:
            raise RuntimeError(
                f"platform promote commit mismatch: {promoted.commit} != {source_commit}"
            )

        # 事件链在拿到 Platform 版本号后一次性补齐（version 由 Platform 账本
        # 晋升时分配，冻结时不可知）：committed → registry_promoted →
        # executor_refresh_ack → activated，与旧线事件形状保持一致。
        # 兼容旧线半途状态（如冻结后失败、激活失败重试的 session 已留有
        # committed/activation_failed 事件）：按最新状态只补发合法后缀，
        # 否则迁移校验会在 promote 成功后才炸。
        chain = ("committed", "registry_promoted", "executor_refresh_ack", "activated")
        prior_status = latest_release_status(release_id)
        if prior_status in chain:
            pending_events = chain[chain.index(prior_status) + 1:]
        elif prior_status == "activation_failed":
            # 旧线激活失败后的重试：activation_failed → registry_promoted 是合法迁移
            pending_events = chain[1:]
        else:
            pending_events = chain
        candidate_id = f"harness-version-{promoted.version}"
        for status in pending_events:
            append_release_event(
                release_id=release_id,
                status=status,
                candidate_id=candidate_id,
                actor_user_id=actor_user_id,
            )

        if not promoted.reload_notified:
            # 软失败：Platform 侧已尽力通知，executor 还有冷启动对账兜底
            logger.warning(
                "Platform 已晋升但 executor reload 通知未确认（冷启动对账兜底）: "
                "session=%s v%s", session_id, promoted.version,
            )

        # ── 5. 镜像推送（bare repo → 外部 git 仓库专用分支，软失败）──
        # 发版权威产物是 Platform 账本 + artifact；镜像只是 git 托管侧备份，
        # 失败不阻断发版（下次发版会带上全部积压 commit 一并推送）。
        mirror_push = "disabled"
        try:
            if git_ops.push_mirror() is not None:
                mirror_push = "pushed"
        except Exception:
            logger.exception(
                "harness 镜像推送失败（不影响发版结果，下次发版重试）: "
                "session=%s", session_id,
            )
            mirror_push = "failed"

        ev_db.update_session(session_id, status="published")

        logger.info(
            "进化 candidate 经 Platform 晋升成功: session=%s v%s commit=%s mirror=%s",
            session_id, promoted.version, source_commit, mirror_push,
        )
        return {
            "status": "activated",
            "release_id": release_id,
            "snapshot_version": promoted.version,
            "source_commit": source_commit,
            "mirror_push": mirror_push,
            "snapshot_trace_id": None,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        logger.info("candidate 发布门禁未通过: session=%s error=%s", session_id, exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("发版失败: session=%s", session_id)
        raise HTTPException(status_code=500, detail=f"发版失败：{exc}") from exc


@router.post("/evolve/sessions/{session_id}/discard")
async def discard_session(session_id: str) -> dict[str, Any]:
    """丢弃：回退 working 区到上一 production 版本（S9）+ 清 checkpoint（Phase 3）。

    流程：
      1. 校验 session 状态为 pending_review
      2. 取当前 production 快照的 source_commit
      3. git reset --hard 回退 working 区到该 commit
      4. 推进 status → discarded（working 区解锁）
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    if session.get("status") != "pending_review":
        raise HTTPException(
            status_code=400,
            detail=f"session 状态为 {session.get('status')}，只有 pending_review 可丢弃",
        )

    from app.core import git_ops
    from app.versioning import registry_repo

    try:
        # 取当前 production 的 commit（git log 推导）
        prod = registry_repo.get_production_version()
        if prod is None:
            raise HTTPException(
                status_code=409,
                detail="无 production 版本，无法回退（首次发版前不能丢弃）",
            )
        target_commit = registry_repo.get_version_commit(prod["version"])
        if not target_commit:
            raise HTTPException(
                status_code=409,
                detail=f"production v{prod['version']} 无对应 commit，无法回退",
            )

        # git reset --hard 回退 working 区
        import subprocess
        wd = git_ops.work_dir()
        result = subprocess.run(
            ["git", "reset", "--hard", target_commit],
            cwd=wd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git reset 失败: {result.stderr.strip()}")

        # FR-008 / EDGE-006：reset 只回退 tracked 文件，untracked 的承重中间件
        # （如 Agent finalize 落地产生的新文件、entrypoint 升级 cp 来的新文件）
        # 会残留，working tree 不等于目标 commit tree。补 git clean -fd 清理它们，
        # 让下次进化从干净状态开始。承重文件已在 HEAD tree（FR-001 已 tracked），
        # clean 不会删 tracked 文件——安全。
        clean_result = subprocess.run(
            ["git", "clean", "-fd"],
            cwd=wd, capture_output=True, text=True, timeout=30,
        )
        if clean_result.returncode != 0:
            # clean 失败不致命（最多留 untracked 残留），记日志继续
            logger.warning(
                "discard git clean 失败（不阻断）: session=%s stderr=%s",
                session_id, clean_result.stderr.strip(),
            )

        # 推进状态
        ev_db.update_session(session_id, status="discarded")

        # Phase 3：清理 checkpoint db（决策 I/T5）——discarded session 的对话状态
        # 不再需要，删文件释放空间。失败不影响主流程（最多留个孤儿文件）。
        await _cleanup_checkpoint(session_id)

        logger.info(
            "进化丢弃: session=%s reset to %s",
            session_id, target_commit,
        )
        return {
            "status": "discarded",
            "reset_to": target_commit,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("丢弃失败: session=%s", session_id)
        raise HTTPException(status_code=500, detail=f"丢弃失败：{e}")


# ── 对话式共创（Phase 3，决策 T2/T10）─────────────────────────


class EvolveMessageRequest(BaseModel):
    """用户发消息请求体。"""

    content: str  # 用户消息正文（markdown，决策 X）


@router.post("/evolve/sessions/{session_id}/messages", status_code=202)
async def send_message(session_id: str, req: EvolveMessageRequest) -> dict[str, Any]:
    """用户发消息，触发一轮对话（决策 T2 按需触发）。

    行为（决策 T2/H/J）：
      1. 校验 session 存在 + status=conversing
      2. 持久化用户消息到 evolve_messages（决策 H 完全持久化）
      3. 启动后台 task 跑 converse round（Agent 回复 + 可能调进化点工具）
      4. 立即返回 message_id（不阻塞，Agent 回复通过 SSE 推送）

    Args:
        session_id: session id
        req.content: 用户消息正文
    Returns:
        {message_id, seq, session_id, status}
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    if session.get("status") != STATUS_CONVERSING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"session 状态为 {session.get('status')}，"
                f"只有 conversing 可发消息（启动会话调 Agent 会话入口）"
            ),
        )
    # 未绑定旧会话只读（FR-002/DEC-002）：agent_id 为空 = 自由启动时代遗留
    if not session.get("agent_id"):
        raise HTTPException(
            status_code=403,
            detail="未绑定的旧进化会话已归档为只读（DEC-002），请在进化 Agent 下开新会话",
        )
    # FR-006 / EDGE-005：并发 round 保护——上一轮 converse/finalize 未完成时拒绝新消息，
    # 避免孤儿 task + checkpoint 竞态。
    _reject_if_round_running(session_id, "发消息")

    # 持久化用户消息（决策 H）
    from app.evolve.evolve_repo import EvolveMessagesRepo
    msg = EvolveMessagesRepo.append(
        session_id, role="user", content=req.content,
    )

    # 重建 ctx（按需触发模型——不持有进程内 ctx，每次从 DB 重建）
    ctx = _rebuild_ctx_from_db(session_id)
    if ctx is None:
        raise HTTPException(
            status_code=500,
            detail=f"重建 ctx 失败（session {session_id} 会话行缺失）",
        )

    # 通知前端"用户消息已落库"（前端轮询拉到 message_updated 帧即调 loadMessages，
    # 把乐观消息替换为权威消息）。_rebuild_ctx_from_db 已 resume trace 内存状态，
    # 此处同步写——必须在启动 task 之前，否则前端要等 Agent 回复才能看到刷新。
    if ctx.recorder and ctx.trace_id_self:
        try:
            ctx.recorder.append_business_event(
                ctx.trace_id_self, tool="message_updated", status="user",
            )
        except Exception:
            logger.exception(
                "session %s: 写用户消息 message_updated 失败（不阻断 converse）",
                session_id,
            )

    # 启动 converse round（不传整条对话历史——LangGraph 通过 thread_id 从 checkpoint 取）
    from app.evolve.agent.agent import run_converse_round
    task = asyncio.create_task(_run_round_bg(ctx, run_converse_round, req.content))
    _running_tasks[session_id] = task

    logger.info("session %s: 用户消息触发 converse round (seq=%d)", session_id, msg["seq"])
    return {
        "message_id": msg["id"],
        "seq": msg["seq"],
        "session_id": session_id,
        "status": "conversing",
    }


@router.post("/evolve/sessions/{session_id}/finalize", status_code=202)
async def finalize_session(session_id: str) -> dict[str, Any]:
    """用户拍板，触发落地（决策 C/D/T10）。

    前置（决策 C/A）：
      - session.status = conversing
      - 至少 1 个 accepted 进化点

    行为：
      1. 从 accepted 进化点生成 design_doc.md（决策 T3/U）
      2. status = finalizing（FlowGuard 解锁落地工具）
      3. 后台 task 跑 finalize round（Agent 落地 → validate → change_log）
      4. 成功 → pending_review → 前端自动跳 review-report（决策 AA，前端实现）
         失败 → failed

    Returns:
        {session_id, status, accepted_count, design_doc_path}
    """
    session = ev_db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    if session.get("status") != STATUS_CONVERSING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"session 状态为 {session.get('status')}，"
                f"只有 conversing 可拍板（先开 Agent 会话 + 对话）"
            ),
        )
    # 未绑定旧会话只读（FR-002/DEC-002）：不允许再触发落地
    if not session.get("agent_id"):
        raise HTTPException(
            status_code=403,
            detail="未绑定的旧进化会话已归档为只读（DEC-002），不能拍板落地",
        )
    # FR-006 / EDGE-005：并发 round 保护——converse 未完成时拒绝拍板，避免孤儿 task。
    _reject_if_round_running(session_id, "拍板")

    # FR-003 / DEC-004：落地通道全局互斥——finalizing/pending_review 独占
    # harness 工作目录与发布链。被占时明确拒绝并告知占用方（不静默排队）。
    landing_occupier = _find_landing_session(exclude_session_id=session_id)
    if landing_occupier:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"落地通道被占用：会话 {landing_occupier['session_id']} "
                    f"处于 {landing_occupier['status']}，请先发布或丢弃该会话"
                ),
                "occupied_by_session_id": landing_occupier["session_id"],
                "occupied_by_status": landing_occupier["status"],
                "occupied_by_agent_id": landing_occupier.get("agent_id"),
            },
        )

    # 校验至少 1 个 accepted 进化点（Agent 绑定模式按 Agent 取——跨会话累积，FR-008）
    from app.evolve.evolve_repo import EvolvePointsRepo
    if session.get("agent_id"):
        accepted_count = EvolvePointsRepo.count_accepted_by_agent(session["agent_id"])
    else:
        accepted_count = EvolvePointsRepo.count_accepted(session_id)
    if accepted_count == 0:
        raise HTTPException(
            status_code=400,
            detail="拍板失败：没有 accepted 进化点（至少需要 1 个，决策 A）",
        )

    # 重建 ctx
    ctx = _rebuild_ctx_from_db(session_id)
    if ctx is None:
        raise HTTPException(
            status_code=500,
            detail=f"重建 ctx 失败（session {session_id} 会话行缺失）",
        )

    # 启动 finalize round（内部会生成 design_doc + 切 finalizing + Agent 落地）
    from app.evolve.agent.agent import run_finalize_round
    task = asyncio.create_task(_run_round_bg(ctx, run_finalize_round))
    _running_tasks[session_id] = task

    logger.info(
        "session %s: 用户拍板触发 finalize round（%d 个 accepted 进化点）",
        session_id, accepted_count,
    )
    return {
        "session_id": session_id,
        "status": "finalizing",
        "accepted_count": accepted_count,
    }


def _load_bound_eval_dossier_snapshot(dossier_id: str) -> dict[str, Any] | None:
    """直读 evaluation_dossiers 表取历史绑定卷宗快照（DEC-006：表保留，仅回显）。

    只取 evolve 会话恢复所需的 findings/scores/report_md/trace_id；
    冻结证据与封存校验随休眠评估系统一并裁撤。
    """
    row = db.query_one(
        "SELECT * FROM evaluation_dossiers WHERE dossier_id=?", (dossier_id,),
    )
    if row is None:
        return None
    import json as _json
    dossier = dict(row)
    for col in ("findings", "scores"):
        raw = dossier.get(f"{col}_json")
        if raw:
            try:
                dossier[col] = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                dossier[col] = None
        else:
            dossier[col] = None
    return dossier


def _rebuild_ctx_from_db(session_id: str) -> EvolveContext | None:
    """从 DB 重建进化上下文（决策 T2 按需触发——每次请求都重建）。

    按需触发模型下，ctx 不在进程内常驻。每条用户消息/拍板请求都重建：
      - session 元数据（status / trace_id / design_doc_path 等）
      - eval_snapshot：历史会话从 bound_eval_dossier_id / eval_ref 直读旧表回填；
        自由启动的新会话为空 dict（可选含 benchmark_report）
      - recorder 注入

    session 行缺失时返回 None（调用方报 500）。
    """
    session = ev_db.get_session(session_id)
    if session is None:
        return None

    ctx = EvolveContext(session_id=session_id)
    ctx.recorder = get_recorder()
    ctx.design_doc_path = session.get("design_doc_path") or ""
    ctx.change_log_path = session.get("change_log_path") or ""
    ctx.session_status = session.get("status") or STATUS_RUNNING
    ctx.thread_id = session_id  # thread_id 始终 = session_id（决策 T1）
    ctx.eval_snapshot = {}
    # 自观测 trace id 无条件恢复（线上回归修复）：inspect round 创建 trace 后落
    # self_trace_id 列。此前只有旧版评估卷宗分支读回该列，自由启动/Agent 绑定的
    # 新会话走不到 → converse/finalize 轮 ctx.trace_id_self 恒空，recorder 静默、
    # message_updated/proposal 帧断流、trace 断档（RC1）。
    ctx.trace_id_self = session.get("self_trace_id") or ""
    _bind_agent_fields(ctx)

    # 历史会话（阶段 D 绑定过评估卷宗）：直读旧表回填快照，保对话上下文连续
    bound_eval_dossier_id = session.get("bound_eval_dossier_id")
    if bound_eval_dossier_id:
        dossier = _load_bound_eval_dossier_snapshot(bound_eval_dossier_id)
        if dossier is not None:
            ctx.trace_id = dossier.get("trace_id") or ""
            ctx.origin_layer = _resolve_origin_layer(ctx.trace_id) if ctx.trace_id else None
            ctx.eval_snapshot = {
                "eval_dossier_id": bound_eval_dossier_id,
                "trace_id": ctx.trace_id,
                "scores": dossier.get("scores"),
                "findings": dossier.get("findings"),
                "report_md": dossier.get("report_md"),
            }
            _ensure_trace_resumed(ctx)
            return ctx
        logger.warning("session %s 的历史评估卷宗 %s 不可用，降级重建",
                       session_id, bound_eval_dossier_id)

    # 旧 session（无 bound_eval_dossier_id）按 eval_ref + baseline_trace 重建
    ctx.trace_id = session.get("baseline_trace") or ""
    ctx.origin_layer = _resolve_origin_layer(ctx.trace_id) if ctx.trace_id else None
    eval_ref = session.get("eval_ref")
    if eval_ref:
        ev = _load_eval_session_row(eval_ref)
        if ev:
            ctx.eval_snapshot = {
                "eval_id": ev.get("eval_id"),
                "trace_id": ev.get("trace_id"),
                "scores": ev.get("scores"),
                "findings": ev.get("findings"),
                "report_md": ev.get("report_md"),
            }
            if not ctx.trace_id:
                ctx.trace_id = ev.get("trace_id") or ""

    _ensure_trace_resumed(ctx)
    return ctx


def _ensure_trace_resumed(ctx: EvolveContext) -> None:
    """重建 ctx 后，确保 recorder 对 trace_id_self 的内存状态已注册。

    converse / finalize round 每次请求都从 DB 重建 ctx，复用 inspect round 创建的
    同一个 trace_id_self。但 recorder 的 _locks/_sequences 是纯内存，进程重启即丢，
    导致 append_business_event 抛 KeyError（converse round 第一行 emit_log 就崩，
    message_updated 帧永不产出 → 前端只能靠切换 tab 全量拉消息）。

    resume_run 幂等：trace 内存状态已存在时 no-op，所以 inspect round 首次 create_run
    后再调也安全。

    FR-001 失败语义（review finding-1）：resume_run 返回 False（trace 行已被删，
    如 DELETE /traces）时同样必须「记 ERROR 且不阻断」——此时清空 trace_id_self
    让本轮降级为无录像可跑，否则后续每个 emit 都抛 KeyError、converse round
    直接 failed。
    """
    if ctx.recorder and ctx.trace_id_self:
        try:
            resumed = ctx.recorder.resume_run(ctx.trace_id_self, ctx.session_id)
        except Exception:
            # resume 失败不阻断请求（FR-001 失败语义）。异常路径同样降级清空：
            # 否则后续 emit 抛 KeyError、converse round 直接 failed（ctx 按请求
            # 重建，清空只影响本轮；下一请求会重试 resume）。
            logger.exception(
                "resume_run 异常 session=%s trace=%s —— 本轮降级为无自观测录像",
                ctx.session_id, ctx.trace_id_self,
            )
            ctx.trace_id_self = ""
            return
        if resumed is False:
            logger.error(
                "resume_run 返回 False（trace 行缺失）session=%s trace=%s —— "
                "本轮降级为无自观测录像继续运行（FR-001 失败语义）",
                ctx.session_id, ctx.trace_id_self,
            )
            ctx.trace_id_self = ""


async def _cleanup_checkpoint(session_id: str) -> None:
    """清理 session 的 checkpoint db（决策 I/T5）。

    discarded/failed session 不再需要对话状态，删文件释放空间。
    失败不影响主流程（最多留个孤儿文件，下次进程重启或手动清理）。
    """
    try:
        from app.evolve.agent.checkpoint_pool import get_checkpoint_pool
        await get_checkpoint_pool().drop(session_id)
    except Exception:
        logger.warning("清理 checkpoint 失败: session=%s", session_id, exc_info=True)


__all__ = ["router"]
