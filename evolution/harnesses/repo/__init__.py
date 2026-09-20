"""Writer 创作 Agent 包 —— v7 单故事专家架构（REQ-20260920-150149 FR-001）。

包 = 自包含的 Agent 定义单元。执行端通过 assemble(ctx) 一行调用装配完整 agent。
运行时值（model/backend/checkpointer/workspace/trace）由 ctx 注入，不进包。

v7 架构切换（自 v6 多 Agent 流水线）：
  - 装配产物 = 单故事专家 Agent（剧情大纲设计）+ reviewer，无 meta 编排、
    无 interview / detail_outline / writing 子代理、无多级委托
  - 故事专家复用 storybuilding 方法论资产（prompt / skills / 中间件护栏），
    由原 meta 下的子代理提升为顶层装配（build_storybuilding_deep_subagent）
  - 输入 = demand.md（表单模板化生成，ContextAssembler 注入；DEC-009 表单直入）
  - 产物 = 大纲三件套 storyline / character / worldview，走 ArtifactRevision 冻结
  - 记忆系统（NWM）按 DEC-003 冻结保留：要素文件在包内，不挂载、不装配
  - 积分挂载沿用 v6 语义，P2（FR-007 免计费）再移除

assemble 职责：
  - 实例化故事专家 middleware 栈（ErrorRecovery → ReadCache → PathGuard →
    EncodingGuard → FileStateTracker → FileWriteSerialize → WriteResultInspector
    → Trace/Credits 注入 → ArtifactSnapshot → Storyline 单线护栏 → RevisionLimit
    → ArtifactValidation）
  - 调 build_storybuilding_deep_subagent 装配故事专家 + reviewer 闭环
  - 返回编译图（含 checkpointer，P2 修订对话线程持久化依赖）
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
    """装配单故事专家 Agent（剧情大纲设计）+ reviewer。

    入参 ctx 含全部运行时值（model/backend/checkpointer/workspace/trace/owner +
    trace_recorder + trace_middleware_cls）。包内只读 ctx，不依赖执行端其他状态。

    Args:
        ctx: RuntimeContext（运行时值）

    Returns:
        编译图（create_deep_agent 产物，含 checkpointer）。顶层即故事专家本体，
        review 作为其唯一子代理（task 工具委托），RevisionLimit 强制单次审查修订。
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
    from .subagents.storybuilding import build_storybuilding_deep_subagent

    workspace_path = ctx.workspace_path
    styles = ctx.styles or {}

    # ── middleware 工厂（故事专家 + reviewer 共用骨架）──
    # 装配顺序（外层→内层）：
    #   ErrorRecovery（捕异常/重试，最外层）
    #   → ReadCache（命中短路，最外层拦截 read_file）
    #   → FilesystemPathGuard（路径白名单 + 规范化）
    #   → EncodingGuard（写入后编码+完整性校验；在 PathGuard 之后拿规范化路径）
    #   → FileStateTracker（edit_file 前 old_string 预检）
    #   → FileWriteSerialize（按 file_path 串行化写）
    #   → WriteResultInspector（在串行化内、ErrorRecovery 内，转抛 WriteFailedError）
    #   → ArtifactSnapshot（最内层，写盘成功才快照进 ArtifactRevision）
    # （v7 删除 MetaReadOnly / GoalMiddleware——meta 层概念随 meta 一起退役；
    #   Storyline 单线护栏与 RevisionLimit 在故事专家装配内追加）
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
        # CreditsMiddleware 挂载（AD2/AD6：积分制，类由 ctx 注入，包内实例化）。
        # 仅在有 owner_id（用户创作）且有 credits_service 时挂载。P2（FR-007）移除。
        if ctx.credits_service is not None and ctx.credits_middleware_cls and ctx.owner_id:
            mw.insert(1, ctx.credits_middleware_cls(
                ctx.credits_service, ctx.trace_id, ctx.owner_id,
                ctx.workspace_path, agent_name,
            ))
        # ArtifactSnapshotMiddleware 挂载（第二期证据采集，2026-07）。
        # 装在 WriteResultInspector 之后（最内层），只有写盘成功的才快照。
        artifact_cb = _make_artifact_snapshot_callback(ctx)
        if artifact_cb is not None:
            mw.append(ArtifactSnapshotMiddleware(artifact_cb, workspace_path, agent_name))
        # Trace V2：技能目录按 scope 冻结（v7 仅故事专家 scope）
        if ctx.trace_recorder is not None and ctx.trace_id:
            catalog_scopes = {"storybuilding-subagent": "storybuilding"}
            scope = catalog_scopes.get(agent_name)
            if scope:
                base = PACKAGE_DIR / "skills"
                paths = [str(base / "storybuilding-initial"), str(base / "storybuilding-expand")]
                from app.platform.agent.runtime import compose_skills_backend
                _, runtime_sources = compose_skills_backend(ctx.backend, paths)
                record_catalog = getattr(ctx.trace_recorder, "record_skill_catalog", None)
                if callable(record_catalog):
                    record_catalog(ctx.trace_id, agent_name, paths, runtime_sources)
            record_stack = getattr(ctx.trace_recorder, "record_middleware_assembly", None)
            if callable(record_stack) and agent_name not in catalog_scopes:
                record_stack(ctx.trace_id, agent_name, mw)
        return mw

    # ── 故事专家装配（顶层）──
    # demand.md 注入：表单模板化生成的需求（DEC-009 表单直入，无访谈环节）。
    story_expert = build_storybuilding_deep_subagent(
        workspace_path,
        ctx.model,
        ctx.backend,
        middleware_factory,
        style_suffix=styles.get("storybuilding"),
        context_file_paths=["demand.md"],
        checkpointer=ctx.checkpointer,
    )
    return story_expert["runnable"]


__all__ = ["assemble"]
