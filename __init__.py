"""Writer 创作 Agent 包（Phase 7 harness 包化重构，D1=B 路径 B）。

包 = 自包含的 Agent 定义单元。执行端通过 assemble(ctx) 一行调用装配完整 agent。
运行时值（model/backend/checkpointer/workspace/trace）由 ctx 注入，不进包。

当前迁移状态（Phase 1 薄包装阶段）：
  - prompts/skills：已物理迁移进包（assemble 读包内文件）
  - middleware/subagents：薄包装 re-export 执行端现有实现（架构验证后物理迁移）
  - assemble：编排入口，复刻 _assemble_via_manifest 逻辑，数据源从 manifest 换成包目录

assemble 职责：
  - 读 prompts/*.md → system_prompt
  - 调执行端 build_deep_subagent / build_interview_deep_subagent 构建 subagent
  - 实例化 meta 层 middleware（含 TraceMiddleware，类由 ctx 注入，T2 设计）
  - 调 create_deep_agent 返回 CompiledAgent

设计依据：设计文档 D1=B / D5=② / D9 / T2。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from contracts.runtime_context import RuntimeContext

logger = logging.getLogger("harness_package")

# 包目录（本 __init__.py 所在目录）
PACKAGE_DIR = Path(__file__).resolve().parent


def _read_prompt(name: str) -> str:
    """读包内 prompts/<name>.md 文本。"""
    return (PACKAGE_DIR / "prompts" / f"{name}.md").read_text(encoding="utf-8").strip()


def _skill_abs_paths(scope: str) -> list[str]:
    """取某 scope 的 skill 绝对路径列表（供 create_deep_agent 的 skills 参数）。

    scope='meta' → skills/meta/*
    scope='storybuilding' → skills/storybuilding-initial, storybuilding-expand
    scope='detail-outline' → skills/detail_outline/*
    scope='writing' → skills/writing/*
    """
    base = PACKAGE_DIR / "skills"
    if scope == "meta":
        return [str(base / "meta" / "auto-pipeline"), str(base / "meta" / "interactive-gating")]
    if scope == "storybuilding":
        return [str(base / "storybuilding-initial"), str(base / "storybuilding-expand")]
    if scope == "detail-outline":
        return [str(base / "detail_outline" / "detail-planning")]
    if scope == "writing":
        return [str(base / "writing" / "chapter-writing")]
    return []


def assemble(ctx: RuntimeContext):
    """装配完整创作 Agent（meta + 4 个 subagent）。

    入参 ctx 含全部运行时值（model/backend/checkpointer/workspace/trace/owner +
    trace_recorder + trace_middleware_cls）。包内只读 ctx，不依赖执行端其他状态。

    TraceMiddleware 挂载（T2）：ctx.trace_middleware_cls 有值时实例化并插入 middleware
    列表（index 1，紧跟 ErrorRecovery）。类定义在执行端（不进包，D2'）。

    Args:
        ctx: RuntimeContext（运行时值）

    Returns: create_deep_agent 返回的 CompiledStateGraph。
    """
    # middleware + subagent + evaluator 已物理进包（包内相对 import）
    from .middleware.error_recovery import ErrorRecoveryMiddleware
    from .middleware.path_guard import FilesystemPathGuardMiddleware
    from .middleware.file_write_serialize import FileWriteSerializeMiddleware
    from .middleware.artifact_prerequisite import ArtifactPrerequisiteMiddleware, ArtifactPrerequisite
    from .middleware.meta_readonly import MetaReadOnlyMiddleware
    from .middleware.goal import GoalMiddleware
    from .subagents.interview import build_interview_deep_subagent
    from .subagents.types import apply_style_suffix
    # runtime 是 DeepAgents SDK 隔离层（执行端平台能力，包依赖它如同依赖 deepagents）
    from app.platform.agent.runtime import (
        GENERAL_PURPOSE_SUBAGENT, SubAgent, compose_skills_backend, create_deep_agent,
    )

    workspace_path = ctx.workspace_path
    styles = ctx.styles or {}

    # ── meta middleware（硬编码，从包内类实例化）──
    meta_middleware = [
        ErrorRecoveryMiddleware(),
        MetaReadOnlyMiddleware(),
        FilesystemPathGuardMiddleware(workspace_path),
        FileWriteSerializeMiddleware(),
        GoalMiddleware(),
    ]
    meta_prompt = apply_style_suffix(_read_prompt("meta_system"), styles.get("meta"))
    meta_skills = _skill_abs_paths("meta")

    # TraceMiddleware 挂载（T2：类由 ctx 注入，包内实例化）
    if ctx.trace_recorder is not None and ctx.trace_id and ctx.trace_middleware_cls:
        meta_middleware.insert(1, ctx.trace_middleware_cls(
            ctx.trace_recorder, ctx.trace_id, "meta-agent",
        ))

    # CreditsMiddleware 挂载（AD2/AD6：积分制，类由 ctx 注入，包内实例化）
    # 仅在有 owner_id（用户创作）且有 credits_service 时挂载。
    # meta agent 挂载：负责预扣触发（tool_call 检测 storybuilding）+ model_call 计费。
    if ctx.credits_service is not None and ctx.credits_middleware_cls and ctx.owner_id:
        meta_middleware.insert(
            1,
            ctx.credits_middleware_cls(
                ctx.credits_service, ctx.trace_id, ctx.owner_id,
                ctx.workspace_path, "meta-agent",
            ),
        )

    # ── meta skills backend 组合 ──
    effective_backend, skill_sources = compose_skills_backend(ctx.backend, meta_skills)

    # ── subagent middleware 工厂 ──
    def middleware_factory(agent_name: str) -> list:
        mw = [
            ErrorRecoveryMiddleware(),
            FilesystemPathGuardMiddleware(workspace_path),
            FileWriteSerializeMiddleware(),
        ]
        if ctx.trace_recorder is not None and ctx.trace_id and ctx.trace_middleware_cls:
            mw.insert(1, ctx.trace_middleware_cls(
                ctx.trace_recorder, ctx.trace_id, agent_name,
            ))
        # CreditsMiddleware 挂载到创作类子代理（AD6），不挂 interview（访谈免费）。
        if (
            ctx.credits_service is not None
            and ctx.credits_middleware_cls
            and ctx.owner_id
            and agent_name != "interview-subagent"
        ):
            mw.insert(1, ctx.credits_middleware_cls(
                ctx.credits_service, ctx.trace_id, ctx.owner_id,
                ctx.workspace_path, agent_name,
            ))
        return mw

    # ── subagent 装配 ──
    subagents: list = []

    # 1. general-purpose（DeepAgents 自带规格）
    gp_spec = SubAgent(**GENERAL_PURPOSE_SUBAGENT)
    gp_spec["middleware"] = middleware_factory("general-purpose-subagent")
    subagents.append(gp_spec)

    # 2. interview（custom，无 review）
    subagents.append(build_interview_deep_subagent(
        workspace_path, ctx.model, ctx.backend, middleware_factory,
    ))

    # 3-5. deep subagents（各自完整装配：prompt/skills/专属middleware/reviewer）
    # 调用包内 build_*_deep_subagent，它们含全部专属逻辑（storyline 单线约束 /
    # context 注入 / skills 路径 / style suffix），assemble 不重复这些。
    from .subagents.storybuilding import build_storybuilding_deep_subagent
    from .subagents.detail_outline import build_detail_outline_deep_subagent
    from .subagents.writing import build_writing_deep_subagent

    subagents.append(build_storybuilding_deep_subagent(
        workspace_path, ctx.model, ctx.backend, middleware_factory,
        style_suffix=styles.get("storybuilding"),
    ))
    subagents.append(build_detail_outline_deep_subagent(
        workspace_path, ctx.model, ctx.backend, middleware_factory,
        style_suffix=styles.get("detail-outline"),
        context_file_paths=["outline.md", "character/*.md", "storyline.md", "storyline/*.md"],
    ))
    subagents.append(build_writing_deep_subagent(
        workspace_path, ctx.model, ctx.backend, middleware_factory,
        style_suffix=styles.get("writing"),
        context_file_paths=["outline.md", "storyline.md", "storyline/*.md", "character/*.md"],
    ))

    # ── 装配 meta agent ──
    return create_deep_agent(
        model=ctx.model,
        tools=[],
        system_prompt=meta_prompt,
        subagents=subagents,
        backend=effective_backend,
        checkpointer=ctx.checkpointer,
        middleware=meta_middleware,
        skills=skill_sources,
    )


__all__ = ["assemble"]
