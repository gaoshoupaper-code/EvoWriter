"""架构清单解释器（REQ-20261006-130414 FR-001，DEC-007 M2 完整清单）。

harness 包的通用装配引擎：读 architecture.json → 实例化 → 组装。
包根 __init__.py 的 assemble(ctx) 薄委托到本模块；包内不再持有
「挂了什么」的事实——清单是唯一真相源（executor 装配 / 桌面投影同源）。

静态/动态边界（DEC-007）：
  - 清单管「挂什么」：agent 集合、role、prompt、domain middleware（有序）、
    skills、委托关系、写入权限、context 注入文件、产物校验路径。
  - 解释器管「怎么挂」：
    * 基础中间件链（BASE_CHAIN_MODULES，全 agent 统一，按 ctx 接线
      trace 回调 / 快照回调 / 重放策略——运行时才能确定的逻辑不进清单）
    * 审查闭环模式：有委托的 agent 固定追加 RevisionLimit（上限取清单
      max_revisions）+ ArtifactValidation（取清单 artifact_paths）
    * 命名派生：trace 名 = 架构名下划线转连字符 + "-subagent"（对齐
      v7 既有 trace 埋点名，历史曲线不断档）

挂载约定：清单引用的 middleware/tools 模块暴露顶层 build(abc) 工厂，
abc 是本模块的 AgentBuildCtx（统一钩子入口，携带 ctx / agent 声明 /
workspace / 回调）。参数化逻辑（如按 demand 配比算预算）留在各模块
build 内——解释器对领域参数零知识。
"""
from __future__ import annotations

import functools
import importlib
import logging
from pathlib import Path
from typing import Any

from contracts.architecture_manifest import (
    BASE_CHAIN_MODULES,
    AgentSpec,
    ArchitectureManifest,
    ManifestError,
    load_architecture_manifest,
    validate_architecture_manifest,
)
from contracts.runtime_context import RuntimeContext

from app.platform.agent.middleware import (
    ArtifactValidationMiddleware,
    ContextAssemblerMiddleware,
)
from app.platform.agent.runtime import (
    CompiledSubAgent,
    FilesystemPermission,
    SubAgent,
    compose_skills_backend,
    create_deep_agent,
)

logger = logging.getLogger("writer.architecture")

# ContextAssembler 注入块的展示标签（v7 既有口径）
CONTEXT_LABEL = "创作需求"


class AgentBuildCtx:
    """单 agent 构建上下文：清单挂载模块 build(abc) 钩子的统一入参。

    ctx              RuntimeContext（model/backend/checkpointer/trace 等运行时值）
    agent            该 agent 的清单声明（模块可读 max_revisions 等自身字段）
    manifest         全量清单（模块原则上只读自身声明，跨 agent 事实以清单为准）
    package          harness 包注册模块名（importlib 挂载子模块用）
    trace_name       trace 埋点名（命名派生见模块 docstring）
    intervention_callback / artifact_callback   懒构造 + 记忆化的 trace 回调
    """

    def __init__(
        self,
        ctx: RuntimeContext,
        manifest: ArchitectureManifest,
        agent: AgentSpec,
        package: str,
        pkg_dir: Path,
    ):
        self.ctx = ctx
        self.manifest = manifest
        self.agent = agent
        self.package = package
        self.pkg_dir = pkg_dir
        self.trace_name = f"{agent.name.replace('_', '-')}-subagent"
        self.workspace_path = Path(ctx.workspace_path)

    @functools.cached_property
    def intervention_callback(self):
        recorder = self.ctx.trace_recorder
        trace_id = self.ctx.trace_id
        if recorder is None or not trace_id:
            return None

        def _callback(
            *, action: str, hook: str, affected_fields: list[str],
            reason: str | None = None, before=None, after=None,
        ) -> None:
            try:
                recorder.record_intervention(
                    trace_id, self.trace_name,
                    action=action, hook=hook, affected_fields=affected_fields,
                    reason=reason, before=before, after=after,
                )
            except Exception:  # noqa: BLE001 — trace 回调永不阻断装配
                pass

        return _callback

    @functools.cached_property
    def artifact_callback(self):
        outer = self.ctx.artifact_snapshot_callback
        if outer is not None:
            return outer
        recorder = self.ctx.trace_recorder
        trace_id = self.ctx.trace_id
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
            except Exception:  # noqa: BLE001
                pass

        return _callback


def _retry_runner(ctx: RuntimeContext):
    factory = ctx.writer_retry_runner_factory
    return factory() if factory is not None else None


def _import_mount(abc: AgentBuildCtx, name: str, subdir: str = "middleware"):
    """按约定路径加载挂载模块（middleware/{name}.py / tools/{name}.py）。"""
    return importlib.import_module(f"{abc.package}.{subdir}.{name}")


def _record_catalog_or_stack(abc: AgentBuildCtx, mw: list) -> None:
    """Trace V2 埋点：有 skills 的 agent 记技能目录，无 skills 的记中间件栈。

    与 v7 旧装配口径一致（catalog_scopes 语义）：技能目录按 scope 冻结、
    栈记录覆盖无技能 agent——历史曲线可比。
    """
    ctx = abc.ctx
    if ctx.trace_recorder is None or not ctx.trace_id:
        return
    if abc.agent.skills:
        base = abc.pkg_dir / "skills"
        paths = [str(base / s) for s in abc.agent.skills]
        _, runtime_sources = compose_skills_backend(ctx.backend, paths)
        record_catalog = getattr(ctx.trace_recorder, "record_skill_catalog", None)
        if callable(record_catalog):
            record_catalog(ctx.trace_id, abc.trace_name, paths, runtime_sources)
    else:
        record_stack = getattr(ctx.trace_recorder, "record_middleware_assembly", None)
        if callable(record_stack):
            record_stack(ctx.trace_id, abc.trace_name, mw)


def _build_base_chain(abc: AgentBuildCtx) -> list:
    """基础中间件链：BASE_CHAIN_MODULES 顺序 + Trace 注入位次 + ArtifactSnapshot 条件挂载。"""
    mw: list[Any] = []
    for name in BASE_CHAIN_MODULES:
        if name == "artifact_snapshot" and abc.artifact_callback is None:
            continue  # 无产物快照回调（无 trace）则不挂，同 v7 旧口径
        mw.append(_import_mount(abc, name).build(abc))
    if abc.ctx.trace_recorder is not None and abc.ctx.trace_id and abc.ctx.trace_middleware_cls:
        mw.insert(1, abc.ctx.trace_middleware_cls(
            abc.ctx.trace_recorder, abc.ctx.trace_id, abc.trace_name,
            retry_runner=_retry_runner(abc.ctx),
        ))
    _record_catalog_or_stack(abc, mw)
    return mw


def _build_domain_middleware(abc: AgentBuildCtx) -> list:
    """清单声明的 domain middleware（有序，外→内）：逐模块 build(abc)。"""
    return [_import_mount(abc, name).build(abc) for name in abc.agent.middleware]


def _build_tools(abc: AgentBuildCtx) -> list:
    """清单声明的 agent 专属工具：tools/{name}.py 的 build(abc) 钩子。"""
    return [_import_mount(abc, name, subdir="tools").build(abc) for name in abc.agent.tools]


def _system_prompt(abc: AgentBuildCtx) -> str:
    text = (abc.pkg_dir / abc.agent.prompt).read_text(encoding="utf-8").strip()
    suffix = (abc.ctx.styles or {}).get(abc.agent.name)
    return f"{text}\n\n{suffix}" if suffix else text


def _build_permissions(agent: AgentSpec) -> list[FilesystemPermission]:
    """读写权限：读全放行 + 清单写 glob 放行 + 其余写全拒（v7 既有模式）。"""
    perms = [FilesystemPermission(operations=["read"], paths=["/**"], mode="allow")]
    for glob in agent.write_permissions:
        perms.append(FilesystemPermission(operations=["write"], paths=[glob], mode="allow"))
    perms.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"))
    return perms


def _build_subagent_spec(abc: AgentBuildCtx) -> SubAgent:
    """无委托的 agent → SubAgent 规格（挂在父 agent 的 task 委托下）。"""
    return SubAgent(
        name=abc.agent.runtime_name or abc.agent.name,
        description=abc.agent.description,
        system_prompt=_system_prompt(abc),
        permissions=_build_permissions(abc.agent),
        middleware=_build_base_chain(abc) + _build_domain_middleware(abc),
    )


def _build_compiled(abc: AgentBuildCtx) -> CompiledSubAgent:
    """main 或有委托的 agent → DeepAgent 编译图（清单解释器核心路径）。"""
    agent = abc.agent
    middleware = _build_base_chain(abc) + _build_domain_middleware(abc)
    if agent.context_files:
        middleware.append(ContextAssemblerMiddleware(
            abc.workspace_path,
            file_paths=agent.context_files,
            context_label=CONTEXT_LABEL,
        ))
    # 审查闭环模式：RevisionLimit 硬上限（清单 max_revisions）
    middleware.append(_import_mount(abc, "revision_limit").build(abc))
    # 产物校验（清单 artifact_paths，相对 workspace 根）
    if agent.artifact_paths:
        middleware.append(ArtifactValidationMiddleware(
            [abc.workspace_path / p for p in agent.artifact_paths]
        ))

    # 委托子代理规格（递归：子代理自身有委托则升格为编译图）
    sub_specs = [_build_agent(abc.ctx, abc.manifest, abc.manifest.agent(d),
                              abc.package, abc.pkg_dir)
                 for d in agent.delegates]

    # Skills 后端组合（SKILL.md 目录路由）
    effective_backend = abc.ctx.backend
    skill_sources: list[str] = []
    if agent.skills:
        base = abc.pkg_dir / "skills"
        skill_paths = [str(base / s) for s in agent.skills]
        effective_backend, skill_sources = compose_skills_backend(abc.ctx.backend, skill_paths)

    graph = create_deep_agent(
        model=abc.ctx.model,
        tools=_build_tools(abc),
        system_prompt=_system_prompt(abc),
        subagents=sub_specs,
        middleware=middleware,
        backend=effective_backend,
        # checkpointer 仅 main（子代理在父 task 调用内执行，父已捕获完整历史）
        checkpointer=abc.ctx.checkpointer if agent.role == "main" else None,
        skills=skill_sources,
    )

    # 最终栈埋点：装配定序后记录完整链（对齐旧工厂 record_middleware_assembly）
    ctx = abc.ctx
    if ctx.trace_recorder is not None and ctx.trace_id:
        record_stack = getattr(ctx.trace_recorder, "record_middleware_assembly", None)
        if callable(record_stack):
            record_stack(ctx.trace_id, abc.trace_name, middleware)

    return CompiledSubAgent(
        name=agent.name,
        description=agent.description,
        runnable=graph,
    )


def _build_agent(
    ctx: RuntimeContext,
    manifest: ArchitectureManifest,
    agent: AgentSpec,
    package: str,
    pkg_dir: Path,
):
    """按清单声明分派：main 或有委托 → 编译图；纯叶子 → SubAgent 规格。"""
    abc = AgentBuildCtx(ctx, manifest, agent, package, pkg_dir)
    if agent.role == "main" or agent.delegates:
        return _build_compiled(abc)
    return _build_subagent_spec(abc)


def assemble_from_manifest(ctx: RuntimeContext, package: str):
    """清单驱动装配入口（harness 包 __init__.assemble 的委托目标）。

    Raises:
        ManifestError: 清单缺失 / 解析失败 / 强校验不过（引用悬空、委托环等）。
    """
    pkg_dir = Path(importlib.import_module(package).__file__).resolve().parent
    manifest = load_architecture_manifest(pkg_dir)
    validation = validate_architecture_manifest(pkg_dir, manifest)
    if not validation.ok:
        raise ManifestError(
            "架构清单校验失败: " + "; ".join(validation.errors)
        )
    main = manifest.main_agents[0]
    logger.info(
        "清单装配: main=%s subagents=%s domain_mw=%s",
        main.name,
        [s.name for s in manifest.sub_agents],
        {a.name: a.middleware for a in manifest.agents},
    )
    compiled = _build_agent(ctx, manifest, main, package, pkg_dir)
    return compiled["runnable"]


__all__ = [
    "AgentBuildCtx",
    "CONTEXT_LABEL",
    "assemble_from_manifest",
]
