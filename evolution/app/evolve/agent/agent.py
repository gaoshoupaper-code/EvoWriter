"""单体进化 Agent 构建 + 运行（决策 S1/S3/S5/S11）。

重构后的单体进化 Agent，替代原 driver + plan/execute 三体结构：
  - 单体 Agent（无子代理），自己一把跑完：探查 → 设计 → 落地 → 校验 → 产出
  - 15 工具（4 探查 + 5 写 + 1 edit + 5 流程）
  - middleware：NoFilesystemToolsMiddleware（禁框架 fs）+ FlowGuardMiddleware（产出约束）+ TraceMiddleware（自观测）
  - backend：FilesystemBackend（专用写工具内部调用，virtual_mode 路径安全）

架构（S1/S3）：
  create_deep_agent(
      tools=make_evolve_tools(backend),       # 15 工具
      subagents=None,                          # 单体无子代理
      middleware=[NoFS, FlowGuard, Trace?],    # 禁 fs + 产出约束 + trace
      backend=FilesystemBackend(...),          # 写工具落盘
  )

输入：Agent 绑定作品（REQ-20261001-131018）——作品 trace/产物是主证据；
     可选评测弱点视图（ctx.eval_snapshot.benchmark_report）+ Agent 探查 harness 要素 + 用户对话共创
产出：harnesses/repo/ 代码改动 + design_doc.md + change_log.md → 待审（pending_review）
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langgraph.errors import GraphRecursionError

from app.core.settings import settings
from app.common.middleware.no_fs import NoFilesystemToolsMiddleware
from app.common.model_factory import build_agent_model
from app.evolve.agent.middleware.flow_guard import FlowGuardMiddleware
from app.evolve.agent.prompt import evolve_system_prompt
from app.evolve.agent.tools import make_evolve_tools
from app.evolve.ctx import EvolveContext, set_tool_context
from app.trace import TraceMiddleware, TraceCallbackHandler

logger = logging.getLogger("evolution.evolve.agent")


def _start_evolution_trace(ctx: EvolveContext, endpoint: str) -> None:
    """为一次进化工作流创建唯一 Trace；后续对话与落地轮次复用它。"""
    if not ctx.recorder or ctx.trace_id_self:
        return
    benchmark = (ctx.eval_snapshot or {}).get("benchmark_report") or {}
    handle = ctx.recorder.create_run(
        session_id=ctx.session_id,
        run_purpose="evolution_evolve",
        endpoint=endpoint,
        session_type="evolve",
        workload="evolution",
        external_refs={
            "benchmark_batch_id": str(benchmark.get("batch_id") or ""),
        },
    )
    ctx.trace_id_self = handle.trace_id


async def _run_agent_streamed(
    agent: Any,
    user_input: str,
    config: dict[str, Any],
    ctx: "EvolveContext",
) -> None:
    """astream_events + EvolveEventSink → 消息持久化 + 流式广播（REQ-20261019-183921）。

    sink 帧派生三路副作用：
      1. 持久化消息到 evolve_messages（assistant / tool）——权威消息源
      2. run_meta 通知帧（message_updated）走 trace 通道（观测 + Pull 兜底）
      3. 行协议帧（row）与信号帧经 stream_hub 广播，前端流式渲染
         （思考块/正文逐段生长/工具行；行帧不落 trace——D1 原则不变）

    消息派生规则（D2）：
      - model_output 且无 tool_calls → 落 assistant 消息（对话回复）
      - model_output 且有 tool_calls → 不落文本（工具单独走 tool 消息）
      - tool_output 工具属于白名单 → 落 tool 消息
      - 其他工具的 tool_output → 不落（只读工具不污染对话历史）

    行快照：round 结束时把 sink.rows（思考/正文/工具行终态）挂到本轮
    最后一条 assistant 消息（rows_json），历史回看时间线不降级。
    """
    from app.evolve.agent.event_sink import EvolveEventSink
    from app.evolve.evolve_repo import EvolveMessagesRepo
    from app.evolve.stream_hub import get_stream_hub

    hub = get_stream_hub()
    sink = EvolveEventSink(session_id=ctx.session_id)
    last_assistant_msg_id: str | None = None
    agent_events = agent.astream_events(
        {"messages": [{"role": "user", "content": user_input}]},
        config=config,
        version="v2",
    )

    async for event in agent_events:
        try:
            frame_dicts = await sink.on_event_dicts(event)
        except Exception:
            logger.exception(
                "session %s: sink 转换事件异常，跳过",
                ctx.session_id,
            )
            continue

        for frame in frame_dicts:
            frame_type = frame.get("type")
            if not frame_type:
                continue
            try:
                if frame_type == "row":
                    # 行协议帧：只广播（不落库、不入 trace）
                    hub.publish(ctx.session_id, frame)
                    continue
                if frame_type in ("tool_call", "tool_error"):
                    hub.publish(ctx.session_id, frame)
                    continue
                if frame_type == "tool_output":
                    # 工具结果信号帧广播（FR-004 契约声明）；不 continue——
                    # 后续落库判断照跑（白名单工具落 tool 消息）
                    hub.publish(ctx.session_id, frame)
                persisted = _persist_message_from_frame(ctx, frame, EvolveMessagesRepo)
                if persisted is not None:
                    if persisted.get("role") == "assistant":
                        last_assistant_msg_id = persisted["id"]
                    # 落库成功 → 广播 message_updated（前端增量拉权威消息）
                    hub.publish(ctx.session_id, {"type": "message_updated"})
                _emit_notification_frame(ctx, frame)
            except Exception:
                # 消息持久化失败是严重问题（会让前端看不到 Agent 动作），
                # 但不能中断 agent 主流程——ERROR 日志 + 继续跑，让 round 函数的
                # 产出检查兜底（如 design_doc 未产 → failed）。
                logger.exception(
                    "session %s: 消息派生失败 frame_type=%s（不中断 agent）",
                    ctx.session_id, frame_type,
                )

    # 行快照收尾：挂到本轮最后一条 assistant 消息（历史回看时间线）。
    # 取消/异常路径不写——半截时间线不值得回看，落库消息仍完整。
    if sink.rows and last_assistant_msg_id:
        try:
            # 归一化：找不到终态的工具行等残留 streaming——落库前置 interrupted，
            # 否则历史回看呈永久「正在思考」实时态
            now_ms = int(time.time() * 1000)
            normalized = [
                {**row, "status": "interrupted", "endedAt": now_ms}
                if row.get("status") == "streaming" else row
                for row in sink.rows
            ]
            EvolveMessagesRepo.attach_rows(last_assistant_msg_id, normalized)
        except Exception:
            logger.exception(
                "session %s: 行快照持久化失败（不影响消息）", ctx.session_id,
            )

    # 循环结束后写 step_stats run_meta（DD5+DD8）：观测实际跑了多少个 superstep。
    if ctx.recorder and ctx.trace_id_self and sink.max_superstep > 0:
        try:
            ctx.recorder.append_business_event(
                ctx.trace_id_self,
                tool="step_stats",
                status="done",
                max_superstep=sink.max_superstep,
            )
        except Exception:
            logger.exception(
                "session %s: 写 step_stats 失败（不影响主流程）",
                ctx.session_id,
            )


# ── 消息持久化派生（D2）──────────────────────────────────────────

# 需要落 tool 消息的工具白名单（D2）：
#   - 进化点工具：propose/update/reject（浮窗权威状态 + 对话可见）
#   - 落地工具：write_*/edit_source（落地进度，finalizing 阶段可见）
#   - 校验工具：validate_changes（落地结果）
# 其他工具（list_work_traces/list_elements 等只读探查）不落消息，
# 避免污染对话历史。
_MESSAGE_TOOLS = frozenset({
    # 进化点工具
    "propose_evolution_point", "update_evolution_point", "reject_evolution_point",
    # 落地写工具（必须与 writers.py 实际注册名 + flow_guard.FINALIZE_ONLY_TOOLS 对齐，
    # 否则 finalizing 阶段 write_* 落地前端收不到 message_updated 通知——EVD-005）
    "write_prompt", "write_middleware", "write_tool", "write_skill", "write_subagent",
    "write_design_doc", "write_change_log",
    "edit_source",
    # 校验工具
    "validate_changes",
})


def _persist_message_from_frame(
    ctx: "EvolveContext",
    frame: dict[str, Any],
    messages_repo: Any,
) -> dict[str, Any] | None:
    """根据 sink 帧派生持久化消息到 evolve_messages（D2）。

    规则：
      - model_output 无 tool_calls → assistant 消息（对话回复）
      - tool_output 工具在白名单 → tool 消息（含 related_points 关联进化点）
      - 其他帧（model_output 有 tool_calls / tool_call / tool_error）→ 不落

    Returns:
        落库的消息 dict；未落库返回 None。
    """
    frame_type = frame.get("type")

    if frame_type == "model_output":
        text = frame.get("text", "")
        tool_calls = frame.get("tool_calls") or []
        # 有工具调用 → 不落文本消息（工具单独走 tool 消息，避免重复）
        if tool_calls or not text.strip():
            return None
        # 纯文本回复 → assistant 消息
        return messages_repo.append(
            ctx.session_id, role="assistant", content=text,
        )

    if frame_type == "tool_output":
        tool_name = frame.get("tool_name", "")
        if tool_name not in _MESSAGE_TOOLS:
            return None
        output_summary = frame.get("output_summary", "")
        if not output_summary:
            return None

        # 进化点工具的 related_points 关联（前端双向高亮联动用）
        related_points: list[str] | None = None
        if tool_name in {"propose_evolution_point", "update_evolution_point", "reject_evolution_point"}:
            # 从 output_summary 提取 point_id（propose 返回值含 id=xxx）
            import re
            m = re.search(r"id=([a-f0-9]+)", output_summary)
            if m:
                related_points = [m.group(1)]

        tool_events = [{
            "tool": tool_name,
            "result_excerpt": output_summary[:200],
        }]
        # 渲染为可读的 tool 消息内容
        content = f"[工具·{tool_name}] {output_summary[:300]}"
        return messages_repo.append(
            ctx.session_id, role="tool", content=content,
            tool_events=tool_events, related_points=related_points,
        )
    return None


def _emit_notification_frame(ctx: "EvolveContext", frame: dict[str, Any]) -> None:
    """把 sink 帧派生为通知帧走 run_meta 通道（前端 Pull 拉到后刷消息）。

    重构后的通知帧类型（D1/D3）：
      - model_output → 通知前端"有新 assistant 消息，调 loadMessages 增量拉"
      - tool_output（白名单工具）→ 通知前端"有新 tool 消息，刷 loadMessages"

    不再传完整帧内容（消息已落库，前端拉权威存储即可），只传最小信号。
    token 流（model_stream）完全移除。
    """
    if not (ctx.recorder and ctx.trace_id_self):
        return

    frame_type = frame.get("type")
    if frame_type == "model_output":
        text = frame.get("text", "")
        tool_calls = frame.get("tool_calls") or []
        # 只在有实际内容要持久化时通知（与 _persist_message_from_frame 对齐）
        if tool_calls or not text.strip():
            return
        ctx.recorder.append_business_event(
            ctx.trace_id_self,
            tool="message_updated",
            status="assistant",
        )
        return

    if frame_type == "tool_output":
        tool_name = frame.get("tool_name", "")
        if tool_name not in _MESSAGE_TOOLS:
            return
        ctx.recorder.append_business_event(
            ctx.trace_id_self,
            tool="message_updated",
            status="tool",
            tool_name=tool_name,
        )
        return


# 不设总超时护栏（asyncio.wait_for）——进化时长不设上限，让它自然跑完。
# recursion_limit 显式设 200（避免 LangChain 框架默认 25 误杀正常进化）。
# GraphRecursionError 分支是兜底：模型陷入死循环（反复调工具不收尾）时强制收敛。


async def _handle_recursion_error(ctx: "EvolveContext", round_name: str) -> None:
    """GraphRecursionError 兜底（DD7）：标 session failed + recorder 收尾 + emit_log。

    各 round 的 ``except GraphRecursionError`` 分支调用本函数完成副作用收敛，
    然后各自 ``return`` 对应的状态 dict——helper 只管副作用，round 管返回值。
    语义：recursion error 属于异常（Agent 没收敛），归 failed；
    cancelled 只属于用户主动 stop，不与此混淆。

    Args:
        ctx:        进化上下文
        round_name: 哪个 round 触顶（converse/finalize），用于日志定位
    """
    from app.evolve import db as ev_db

    logger.warning(
        "session %s: %s round 步数触顶（recursion_limit=200），未收敛",
        ctx.session_id, round_name,
    )
    ev_db.update_session(ctx.session_id, status="failed")
    if ctx.recorder and ctx.trace_id_self:
        ctx.recorder.fail_run(
            ctx.trace_id_self, f"{round_name} round 步数触顶（未收敛）"
        )
    ctx.emit_log(
        f"{round_name} 阶段消耗过多步数仍未完成"
        "（可能反复调用工具未收尾）。请重试，或检查模型是否稳定。"
    )


async def build_evolve_agent(ctx: EvolveContext):
    """构建进化 Agent（决策 S1/S3/S5/S11 + Phase 2A T1/T2）。

    Phase 2A 改造：加 checkpointer（per-session AsyncSqliteSaver），为对话式
    共创工作台的多轮对话铺地基。thread_id = session_id，LangGraph 据此从
    checkpoint 自动恢复对话史。

    Args:
        ctx: 进化上下文（自由启动；eval_snapshot 可含 benchmark_report）

    Returns:
        编译后的 CompiledStateGraph（可 ainvoke/astream）
    """
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    set_tool_context(ctx)

    model = build_agent_model(temperature=0.2)

    # FilesystemBackend：专用写工具内部调用它落盘到 harnesses/repo/
    # virtual_mode=True：root_dir 作为虚拟根，阻止绝对路径 / .. 越界（S5/S13）
    backend = FilesystemBackend(
        root_dir=str(settings.harness_work_dir_path),
        virtual_mode=True,
    )

    # 13 工具（inspect 4 + writers 6 + flow 3），writers 需 backend
    tools = make_evolve_tools(backend=backend)

    system_prompt = evolve_system_prompt(
        session_id=ctx.session_id,
        trace_id=ctx.trace_id,
        input_summary=_format_input_summary(ctx),
        work_binding=_format_work_binding(ctx),
    )

    # middleware：禁框架 fs + 产出约束 + 自观测 trace
    trace_middleware = TraceMiddleware(
        recorder=ctx.recorder,
        trace_id=ctx.trace_id_self,
        agent_name="evolve-agent",
    ) if ctx.recorder and ctx.trace_id_self else None

    middleware_list: list = [
        NoFilesystemToolsMiddleware(),
        FlowGuardMiddleware(),
    ]
    if trace_middleware:
        middleware_list.append(trace_middleware)

    # Phase 2A：checkpointer 从 pool 取（per-session，决策 T5）。
    # 对话式共创下每轮 ainvoke 复用同 thread_id 的 checkpoint，自动恢复对话史。
    # 单体模式下 checkpoint 仍会落盘（无害——单次 ainvoke 只产 1 个 checkpoint）。
    from app.evolve.agent.checkpoint_pool import get_checkpoint_pool
    checkpointer = await get_checkpoint_pool().get(ctx.session_id)

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware_list,
        subagents=None,
        backend=backend,
        checkpointer=checkpointer,
    )
    logger.info(
        "进化 Agent 构建完成: session=%s trace=%s thread_id=%s",
        ctx.session_id, ctx.trace_id, ctx.thread_id,
    )
    return agent


async def run_converse_round(ctx: EvolveContext, user_message: str) -> dict[str, Any]:
    """对话共创 round（决策 T2，单条用户消息触发一轮）。

    按需触发模型（决策 T2）——每条用户消息启动一次 ainvoke，跑完即止。
    LangGraph 通过 thread_id + checkpoint 自动恢复完整对话史（决策 T1）。
    Agent 在本轮里可以：
      - 自由文本探讨（不进浮窗）
      - 调 propose/update/reject 进化点工具（状态权威变更，进浮窗）
      - 调只读探查工具补充信息
    不能调落地工具（FlowGuard 在 conversing 阶段拦截，决策 T9）。

    Args:
        ctx: 进化上下文（session_status 必须是 conversing）
        user_message: 用户输入的消息内容（markdown）

    Returns:
        {"status": "conversing"|"failed"|"cancelled", "session_id": ...}
    """
    from app.evolve import db as ev_db
    from app.evolve.ctx import STATUS_CONVERSING, STATUS_FAILED, STATUS_CANCELLED

    # 状态校验：只 conversing 状态能跑对话 round
    ctx.reload_session_status()
    if ctx.session_status != STATUS_CONVERSING:
        return {
            "status": "failed",
            "error": f"当前状态 {ctx.session_status} 不能跑对话 round（需 conversing）",
            "session_id": ctx.session_id,
        }

    agent = await build_evolve_agent(ctx)
    config: dict[str, Any] = {
        "configurable": {"thread_id": ctx.thread_id},
        "recursion_limit": 200,  # 显式放开（避免 LangChain 框架默认 25 误杀）
    }
    if ctx.recorder and ctx.trace_id_self:
        config["callbacks"] = [TraceCallbackHandler(ctx.recorder, ctx.trace_id_self)]

    logger.info("session %s: converse round 启动", ctx.session_id)
    ctx.emit_log("用户消息触发对话 round。")

    try:
        await _run_agent_streamed(agent, user_message, config, ctx)
        logger.info("session %s: converse round 完成", ctx.session_id)
        return {"status": "conversing", "session_id": ctx.session_id}

    except GraphRecursionError:
        # 步数触顶（recursion_limit=200）：对话轮陷入死循环（异常 failed，
        # 与用户主动 stop 的 cancelled 语义区分开——recursion 是 Agent 自身没收敛）。
        await _handle_recursion_error(ctx, "converse")
        return {
            "status": "failed", "session_id": ctx.session_id,
            "error": "converse round 步数触顶（未收敛）",
        }
    except asyncio.CancelledError:
        # 用户停止输出（决策 L）：会话保留，status 不变
        logger.info("session %s: converse round 被用户停止（会话保留）", ctx.session_id)
        return {"status": "cancelled", "session_id": ctx.session_id}
    except Exception as e:
        logger.exception("session %s: converse round 失败", ctx.session_id)
        return {"status": "failed", "error": str(e), "session_id": ctx.session_id}


async def run_finalize_round(ctx: EvolveContext) -> dict[str, Any]:
    """落地 round（决策 T2/T10/D，拍板后触发）。

    流程：
      1. 从 accepted 进化点生成 design_doc.md（决策 T3/U）
      2. status = finalizing（FlowGuard 解锁落地工具）
      3. Agent 跑：按 design_doc 落地（write_*/edit_source）→ validate → change_log
      4. 成功 → pending_review（Phase 3 自动跳 review-report，决策 AA）
         失败 → failed（用户丢弃重开，决策 I）

    无用户交互——一个 finalizing task 跑完即终态（决策 D）。

    Returns:
        {"status": "pending_review"|"failed"|"cancelled", "session_id": ...}
    """
    from app.evolve import db as ev_db
    from app.evolve.ctx import (
        STATUS_FINALIZING, STATUS_PENDING_REVIEW, STATUS_FAILED, STATUS_CANCELLED,
    )
    from app.evolve.docs import generate_design_doc_from_points
    from app.evolve.evolve_repo import EvolvePointsRepo

    # 前置：必须有未发版的 accepted 进化点（Agent 绑定模式按 Agent 取——跨会话累积，
    # FR-008；已发版 shipped 点不重复落地，REQ-20261004-212948 FR-002）
    if ctx.agent_id:
        accepted_count = EvolvePointsRepo.count_accepted_by_agent(ctx.agent_id)
    else:
        accepted_count = EvolvePointsRepo.count_accepted(ctx.session_id)
    if accepted_count == 0:
        return {
            "status": "failed",
            "error": "拍板失败：没有待落地的新采纳进化点（已发版的点不重复落地，至少需要 1 个未发版的已采纳点）",
            "session_id": ctx.session_id,
        }

    # 从 accepted 进化点生成 design_doc（Agent 模式下覆盖 Agent 级共识）
    design_path = generate_design_doc_from_points(
        ctx.session_id, agent_id=ctx.agent_id or None,
    )
    if not design_path:
        return {
            "status": "failed",
            "error": "生成 design_doc 失败（无 accepted 进化点）",
            "session_id": ctx.session_id,
        }
    ctx.design_doc_path = design_path
    ev_db.update_session(ctx.session_id, design_doc_path=design_path)

    # 切到 finalizing（FlowGuard 解锁落地工具）
    ctx.session_status = STATUS_FINALIZING
    ev_db.update_session(ctx.session_id, status=STATUS_FINALIZING)
    ctx.emit_phase("finalizing")
    ctx.emit_log("进入落地阶段，按已拍板的进化点开始改代码。")

    agent = await build_evolve_agent(ctx)
    config: dict[str, Any] = {
        "configurable": {"thread_id": ctx.thread_id},
        "recursion_limit": 200,  # 显式放开（避免 LangChain 框架默认 25 误杀）
    }
    if ctx.recorder and ctx.trace_id_self:
        config["callbacks"] = [TraceCallbackHandler(ctx.recorder, ctx.trace_id_self)]

    logger.info("session %s: finalize round 启动", ctx.session_id)

    # system 触发消息：指示 Agent 按 design_doc 落地
    if ctx.agent_id:
        accepted = EvolvePointsRepo.list_by_agent_status(ctx.agent_id, "accepted")
    else:
        accepted = EvolvePointsRepo.list_by_status(ctx.session_id, "accepted")
    user_input = (
        f"用户已拍板 {len(accepted)} 个进化点，design_doc 已生成：{design_path}\n"
        f"现在进入落地阶段。请：\n"
        f"1. 按 design_doc 的改动清单，逐个用 write_*（新建）或 edit_source（修改）落地\n"
        f"2. 全部落地后调 validate_changes 校验\n"
        f"3. 校验后调 write_change_log 产出记录（FlowGuard 要求 design_doc 在前，已满足）\n"
        f"4. 完成后流程结束，进入 pending_review 等用户发布"
    )

    try:
        await _run_agent_streamed(agent, user_input, config, ctx)

        # 产出检查
        if ctx.change_log_path:
            ctx.session_status = STATUS_PENDING_REVIEW
            ev_db.update_session(ctx.session_id, status=STATUS_PENDING_REVIEW)
            ctx.emit_phase("pending_review")
            ctx.emit_log("落地完成，进入 pending_review 等待发布审查。")
            if ctx.recorder and ctx.trace_id_self:
                ctx.recorder.complete_run(ctx.trace_id_self)
            return {"status": "pending_review", "session_id": ctx.session_id}
        else:
            ctx.emit_log("落地结束但未产出 change_log。")
            ev_db.update_session(ctx.session_id, status=STATUS_FAILED)
            if ctx.recorder and ctx.trace_id_self:
                ctx.recorder.fail_run(ctx.trace_id_self, "未产出 change_log")
            return {"status": "failed", "error": "未产出 change_log", "session_id": ctx.session_id}

    except GraphRecursionError:
        # 步数触顶（recursion_limit=200）：落地阶段陷入死循环。
        await _handle_recursion_error(ctx, "finalize")
        return {
            "status": "failed", "session_id": ctx.session_id,
            "error": "finalize round 步数触顶（未收敛）",
        }
    except asyncio.CancelledError:
        logger.info("session %s: finalize round 被用户停止", ctx.session_id)
        ev_db.update_session(ctx.session_id, status=STATUS_CANCELLED)
        if ctx.recorder and ctx.trace_id_self:
            ctx.recorder.cancel_run(ctx.trace_id_self, reason="user_stop")
        return {"status": "cancelled", "session_id": ctx.session_id}
    except Exception as e:
        logger.exception("session %s: finalize round 失败", ctx.session_id)
        ev_db.update_session(ctx.session_id, status=STATUS_FAILED)
        if ctx.recorder and ctx.trace_id_self:
            ctx.recorder.fail_run(ctx.trace_id_self, e)
        return {"status": "failed", "error": str(e), "session_id": ctx.session_id}


# ── prompt 摘要辅助（从 driver/agent.py 搬来）─────────────────────


def _format_work_binding(ctx: EvolveContext) -> str:
    """把作品绑定上下文格式化成 system prompt 的「作品绑定」段（FR-004/DEC-005）。

    三路内容：作品概览（会话创建时快照）+ 既有进化点清单（动态取，含状态）+
    历次发布摘要（动态取）。旧会话（未绑定）返回空串。
    """
    if not ctx.agent_id:
        return ""
    from app.evolve import db as ev_db
    from app.evolve.evolve_repo import EvolvePointsRepo

    wc = ctx.work_context or {}
    overview = wc.get("overview") or {}
    stats = wc.get("trace_stats") or {}
    art_stats = wc.get("artifact_stats") or {}
    lines: list[str] = []
    if overview:
        lines.append(
            f"- 作品「{overview.get('title', '?')}」"
            f"（owner: {overview.get('owner_username', '?')}，"
            f"写作会话 {overview.get('session_count', 0)} 个）"
        )
    else:
        # FR-004 失败语义：概览拉取失败降级注明，不阻断
        lines.append("- 作品概览暂缺（executor 拉取失败，可用证据工具自行探查）")
    lines.append(
        f"- 已摄入会话记录(trace) {stats.get('total', 0)} 条"
        f"（完成 {stats.get('completed', 0)} / 失败 {stats.get('failed', 0)}），"
        f"产物版本 {art_stats.get('revisions', 0)} 个"
        f"（{art_stats.get('logical_keys', 0)} 类）"
    )

    # 既有进化点（动态——跨会话累积，会话 2 能看到会话 1 的点，FR-008）。
    # 每行带 id + 全局序号（FR-002）：新会话看不到旧会话的工具返回值，
    # 不注入 id 则模型更新旧点时只能瞎猜引用。
    points = EvolvePointsRepo.list_by_agent(ctx.agent_id)
    if points:
        by_status: dict[str, int] = {}
        for p in points:
            by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        stat_desc = "、".join(f"{k} {v}" for k, v in by_status.items())
        lines.append(
            f"- 既有进化点 {len(points)} 个（{stat_desc}）。"
            f"更新/否决时 point_id 用清单里的 id 或 #序号："
        )
        # 已发版点带版本号（REQ-20261004-212948 FR-004：Agent 感知已处理问题）
        for p in points[:10]:
            tag = f"[{p['status']}"
            if p["status"] == "shipped":
                tag += f" v{p.get('version') or '?'}"
            tag += "]"
            lines.append(
                f"  • {tag} #{p['seq']}（id={p['id']}）"
                f"{p['target']}: {p['problem'][:60]}"
            )
        if by_status.get("shipped"):
            lines.append(
                "  （[shipped] 已发版 = 已处理并随版本发布，勿重复 propose；"
                "同目标需要再优化（新问题/回归）时请新提进化点并说明与上版差异）"
            )
    else:
        lines.append("- 既有进化点：无（本 Agent 名下尚未提出）")

    # 历次发布摘要（动态）。两路口径缺一不可：
    #   本 Agent 名下发布（agent_id 隔离的档案视角）
    #   全局发版流水（Platform 账本 = harness 真实演进史）。新 Agent 名下无发布
    #   ≠ harness 是原始形态——缺全局口径会让 Agent 误判改动基线、
    #   重复提出已修复的问题（线上实测：进化 Agent 误称「harness 还是 v7 原始形态」）。
    published = [
        s for s in ev_db.list_sessions_by_agent(ctx.agent_id, limit=50)
        if s.get("status") == "published"
    ]
    if published:
        lines.append(
            f"- 本 Agent 名下发布 {len(published)} 次"
            f"（最近一次 {published[0].get('updated_at', '?')}）"
        )
    else:
        lines.append("- 本 Agent 名下发布：无（harness 全局演进史见下，勿当成原始形态）")

    from app.versioning import platform_ledger
    try:
        ledger = platform_ledger.fetch_ledger()
        items = sorted(ledger.get("items") or [], key=lambda v: v["version"], reverse=True)
        prod = ledger.get("production_version")
        if items:
            lines.append(
                f"- 全局发版流水（Platform 账本，harness 实际演进史）："
                f"当前生产版本 v{prod}，累计 {len(items)} 个版本。最近发版："
            )
            for v in items[:5]:
                date = (v.get("created_at") or "?")[:10]
                note = (v.get("note") or "").strip() or "（无摘要）"
                lines.append(f"  • v{v['version']}（{date}）：{note[:80]}")
            lines.append("  （提进化点前先对照上述摘要——已修复过的问题不要重复提）")
        else:
            lines.append("- 全局发版流水：账本为空（harness 确为原始形态）")
    except RuntimeError:
        lines.append("- 全局发版流水暂缺（Platform 账本不可达，可用证据工具自行探查）")
    return "\n".join(lines)


def _format_input_summary(ctx: EvolveContext) -> str:
    """把会话可用输入格式化成 system prompt 的摘要（自由启动，DEC-004）。

    输入来源（按可用性）：
      - 评测弱点视图（附带 benchmark_batch_id 时，ctx 重建时恢复注入）
      - 历史评估快照（旧会话恢复时直读旧表回填）
      - 都没有 → 提示以探查 + 对话为准
    """
    snap = ctx.eval_snapshot or {}
    lines: list[str] = []

    benchmark = snap.get("benchmark_report")
    if benchmark:
        dims = benchmark.get("weakest_dimensions") or []
        lines.append(f"- 评测弱点视图: 批次 {benchmark.get('batch_id')}"
                     f"（校准状态 {benchmark.get('calibration')}）")
        if dims:
            dim_desc = ", ".join(
                d.get("dimension", "?") if isinstance(d, dict) else str(d) for d in dims[:8]
            )
            lines.append(f"  最弱维度: {dim_desc}")

    findings = snap.get("findings") or []
    if findings:
        # 历史会话恢复：旧评估快照仍有归因价值
        lines.append(f"- 历史评估诊断条目数: {len(findings)}")
        high = [f for f in findings if isinstance(f, dict) and f.get("severity") == "high"]
        if high:
            lines.append(f"- 高优先级问题（{len(high)} 条）:")
            for h in high[:3]:
                lines.append(f"  • [{h.get('dimension', '?')}] {h.get('finding', '')[:80]}")

    if not lines:
        return "(本会话绑定作品：无附带评测弱点视图。证据按需取证——讨论到具体问题时调证据工具查 trace/产物，不要凭空推断)"
    if ctx.trace_id:
        lines.append(f"- 被测 trace_id: {ctx.trace_id}")
    return "\n".join(lines)


# key = query 文本的 md5，value = embedding 向量。进程级，查询幂等故无 session 隔离必要。
_embedding_cache: dict[str, list[float]] = {}


