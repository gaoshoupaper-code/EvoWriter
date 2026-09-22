"""Writer 创作 Agent 包 —— v14 多 Agent 领域分工架构（REQ-20260922-162823 FR-001）。

包 = 自包含的 Agent 定义单元。执行端通过 assemble(ctx) 一行调用装配完整 agent。
运行时值（model/backend/checkpointer/workspace/trace）由 ctx 注入，不进包。

v14 架构（双架构实验的多 Agent 臂，基于 v13 连续增量版之上）：
  - 装配产物 = orchestrator（主控编排）+ 3 领域 SubAgent（worldview / character /
    storyline）+ review-storybuilding，全部经 task 委托调度
  - 领域分工（DEC-003）：世界观→人物→故事线依赖序初构；增量按 R 值分流
  - 共享工作区全量可读、写入按领域 permissions 隔离（DEC-006）
  - QuotaConvergenceMiddleware 驱动连续增量循环至配比达标（DEC-011 与 v13
    挂载同一份实现；判定器唯一实现于 contracts.storybuilding_quota）
  - reviewer 审查后修订按领域分派（DEC-009），review 调用上限 2 次与 v13 一致
  - 记忆系统（NWM）维持冻结不挂载；免计费不变

assemble 职责：
  - 实例化各 agent 通用 middleware 骨架（ErrorRecovery → ReadCache → PathGuard →
    EncodingGuard → FileStateTracker → FileWriteSerialize → WriteResultInspector
    → Trace 注入 → ArtifactSnapshot）
  - 调 build_orchestrator_agent 装配编排（领域代理 + review + skills + 导航）
  - 返回编译图（含 checkpointer）
"""
from __future__ import annotations

import logging
from pathlib import Path

from contracts.runtime_context import RuntimeContext

logger = logging.getLogger("harness_package")

# 包目录（本 __init__.py 所在目录）
PACKAGE_DIR = Path(__file__).resolve().parent


def _make_artifact_snapshot_callback(ctx: RuntimeContext):
    """构造不可变 ArtifactRevision 回调。"""
    if ctx.artifact_snapshot_callback is not None:
        return ctx.artifact_snapshot_callback

    recorder = ctx.trace_recorder
    trace_id = ctx.trace_id
    if recorder is None or not trace_id:
        return None
    record_revision = getattr(recorder, "record_artifact_revision", None)
    if not callable(record_revision):
        return None

    def _callback(snapshot_data: dict) -> None:
        try:
            record_revision(
                trace_id,
                snapshot_data.get("agent_name", "unknown"),
                file_path=snapshot_data["file_path"],
                content=snapshot_data.get("content", ""),
                tool_name=snapshot_data.get("tool"),
                tool_call_id=snapshot_data.get("tool_call_id"),
                content_hash=snapshot_data.get("fingerprint"),
            )
        except Exception:
            pass

    return _callback


def _make_intervention_callback(ctx: RuntimeContext, agent_name: str):
    recorder = ctx.trace_recorder
    trace_id = ctx.trace_id
    if recorder is None or not trace_id:
        return None

    def _callback(
        *, action: str, hook: str, affected_fields: list[str], reason: str | None = None,
        before=None, after=None,
    ) -> None:
        try:
            recorder.record_intervention(
                trace_id,
                agent_name,
                action=action,
                hook=hook,
                affected_fields=affected_fields,
                reason=reason,
                before=before,
                after=after,
            )
        except Exception:
            pass

    return _callback


def _retry_runner_for(ctx: RuntimeContext):
    """从 ctx 构造模型重试运行器（可观测）。

    writer_retry_runner_factory 是 RuntimeContext 声明字段（默认 None）；None 时返回 None，
    TraceMiddleware 走原"只看外边界"行为（向后兼容）。
    """
    factory = ctx.writer_retry_runner_factory
    return factory() if factory is not None else None


def assemble(ctx: RuntimeContext):
    """装配 v14 多 Agent 编排（orchestrator + 3 领域 SubAgent + reviewer）。

    入参 ctx 含全部运行时值（model/backend/checkpointer/workspace/trace/owner +
    trace_recorder + trace_middleware_cls）。包内只读 ctx，不依赖执行端其他状态。

    Args:
        ctx: RuntimeContext（运行时值）

    Returns:
        编译图（create_deep_agent 产物，含 checkpointer）。顶层为 orchestrator，
        worldview / character / storyline 领域代理与 review 经 task 委托调度；
        QuotaConvergence 驱动连续增量循环（与 v13 同一中间件实现）。
    """
    from .middleware.error_recovery import ErrorRecoveryMiddleware
    from .middleware.path_guard import FilesystemPathGuardMiddleware
    from .middleware.file_write_serialize import FileWriteSerializeMiddleware
    # A2 加固中间件（原孤儿代码，重新装配 + 修复设计缺陷）
    from .middleware.read_cache import ReadCacheMiddleware
    from .middleware.encoding_guard import EncodingGuardMiddleware
    from .middleware.file_state_tracker import FileStateTrackerMiddleware
    from .middleware.write_result_inspector import WriteResultInspectorMiddleware
    from .middleware.artifact_snapshot import ArtifactSnapshotMiddleware

    # ── v14 多 Agent 编排装配（REQ-20260922-162823 FR-001）──
    # demand.md 注入：orchestrator 经 ContextAssembler 读取需求（表单直入，DEC-009）。
    from .subagents.orchestrator import SKILL_DIRS, build_orchestrator_agent

    workspace_path = ctx.workspace_path
    styles = ctx.styles or {}

    def middleware_factory(agent_name: str) -> list:
        intervention_callback = _make_intervention_callback(ctx, agent_name)
        mw = [
            ErrorRecoveryMiddleware(
                intervention_callback=intervention_callback,
                tool_replay_policy=ctx.tool_replay_policy,  # CON-005 task 防重放
            ),
            ReadCacheMiddleware(intervention_callback=intervention_callback),
            FilesystemPathGuardMiddleware(
                workspace_path,
                intervention_callback=intervention_callback,
            ),
            EncodingGuardMiddleware(),
            FileStateTrackerMiddleware(),
            FileWriteSerializeMiddleware(intervention_callback=intervention_callback),
            WriteResultInspectorMiddleware(),
        ]
        if ctx.trace_recorder is not None and ctx.trace_id and ctx.trace_middleware_cls:
            mw.insert(1, ctx.trace_middleware_cls(
                ctx.trace_recorder, ctx.trace_id, agent_name, retry_runner=_retry_runner_for(ctx),
            ))
        # FR-007（REQ-20260920-150149）：免计费——CreditsMiddleware 不装配。
        artifact_cb = _make_artifact_snapshot_callback(ctx)
        if artifact_cb is not None:
            mw.append(ArtifactSnapshotMiddleware(artifact_cb, workspace_path, agent_name))
        # Trace V2：orchestrator 按 scope 冻结 v14 skill 目录（4 个：调度+3 领域）
        if ctx.trace_recorder is not None and ctx.trace_id:
            if agent_name == "orchestrator":
                from app.platform.agent.runtime import compose_skills_backend

                _, runtime_sources = compose_skills_backend(ctx.backend, SKILL_DIRS)
                record_catalog = getattr(ctx.trace_recorder, "record_skill_catalog", None)
                if callable(record_catalog):
                    record_catalog(ctx.trace_id, agent_name, SKILL_DIRS, runtime_sources)
            else:
                record_stack = getattr(ctx.trace_recorder, "record_middleware_assembly", None)
                if callable(record_stack):
                    record_stack(ctx.trace_id, agent_name, mw)
        return mw

    return build_orchestrator_agent(
        workspace_path,
        ctx.model,
        ctx.backend,
        middleware_factory,
        style_suffix=styles.get("storybuilding"),
        context_file_paths=["demand.md"],
        checkpointer=ctx.checkpointer,
    )


__all__ = ["assemble"]
