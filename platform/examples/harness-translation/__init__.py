"""翻译领域最小 harness 包（第二垂直领域示例，AC-010）。

包结构与写作领域（evolution/harnesses/repo）同构：__init__.py + prompts/ +
middleware/ + skills/。Platform 只消费 assemble(ctx) 契约，不感知领域内容
——本包走完整链路（probe / promote / 绑定 / 兼容判定）零改动平台代码。

薄包装模式（与写作领域一致）：运行时构件（create_deep_agent /
compose_skills_backend）import executor 的 runtime 隔离层，领域内容
（prompt / skill / middleware 薄包装）在包内。规模最小化：单 agent，
无子代理编排。
"""
from __future__ import annotations

from pathlib import Path

from contracts.runtime_context import RuntimeContext

# 包目录（本 __init__.py 所在目录）——按 __file__ 定位，checkout 到任意
# 临时目录装载都能找到包内文件（与写作领域同手法）
PACKAGE_DIR = Path(__file__).resolve().parent


def _read_prompt(name: str) -> str:
    """读包内 prompts/<name>.md 文本。"""
    return (PACKAGE_DIR / "prompts" / f"{name}.md").read_text(encoding="utf-8").strip()


def assemble(ctx: RuntimeContext):
    """装配单层翻译 agent，返回 create_deep_agent 编译后的图。

    与写作领域 assemble 的差异只在规模：不构建 subagent 编排、不挂领域
    专属中间件族。契约面完全一致——只读 ctx（model/backend/checkpointer/
    trace），不依赖执行端其他状态。
    """
    # 薄包装：运行时构件来自 executor 的 runtime 隔离层（换框架只改那边）
    from app.platform.agent.runtime import compose_skills_backend, create_deep_agent

    from .middleware.artifact_snapshot import ArtifactSnapshotMiddleware

    skills = [str(PACKAGE_DIR / "skills" / "translator")]
    # virtual_mode backend 下为 skills 路由 CompositeBackend（同写作领域）
    effective_backend, skill_sources = compose_skills_backend(ctx.backend, skills)

    # 产物快照取证：recorder/trace_id 齐（probe 与真实 Run 都注入）才挂载
    middleware = []
    if ctx.trace_recorder is not None and ctx.trace_id:
        middleware.append(ArtifactSnapshotMiddleware(
            recorder=ctx.trace_recorder,
            trace_id=ctx.trace_id,
            workspace_root=ctx.workspace_path,
            agent_name="translator-agent",
            strict=False,  # 领域层尽力采集；严格取证由 executor 平台 scope 强制
        ))

    return create_deep_agent(
        model=ctx.model,
        tools=[],
        system_prompt=_read_prompt("translator_system"),
        subagents=[],  # 单 agent：翻译领域无子代理编排
        backend=effective_backend,
        checkpointer=ctx.checkpointer,
        middleware=middleware,
        skills=skill_sources,
    )


__all__ = ["assemble"]
