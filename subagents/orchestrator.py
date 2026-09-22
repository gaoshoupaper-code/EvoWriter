"""v14 多 Agent 编排装配（REQ-20260922-162823 FR-001，DEC-003/005/006/008/009）。

拓扑：orchestrator（顶层，task 委托调度）+ 3 领域 SubAgent
（worldview / character / storyline）+ review-storybuilding 审查子代理。

- 共享工作区全量可读（DEC-006），写入按领域 permissions 隔离（FilesystemPermission）
- QuotaConvergenceMiddleware 挂 orchestrator 驱动增量循环——与 v13 单 Agent 版
  挂载同一份实现（DEC-011 同一判定器/同一终止语义）
- RevisionLimitMiddleware(max_revisions=2, review_name="review-storybuilding")
  与 v13 的 review 调用上限语义一致
- StorylineSingleLineLimit 挂 storyline 领域代理（写线文件的代理），预算口径同 v13
- 领域 SubAgent system_prompt = 公共规则（prompts/v14/common_rules.md，与 v13
  逐字同源段落）+ 领域职责（domain_*.md，原文逐字提取），FR-003 对齐
- skills 全量 compose 进共享 backend（compose 顺序固定 → /_skills_{i}/ 前缀稳定，
  各 prompt 内指引路径）；orchestrator 的 SkillsMiddleware 注入调度 skill 描述
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware

from app.platform.agent.runtime import (
    FilesystemPermission,
    SubAgent,
    compose_skills_backend,
    create_deep_agent,
)

from .reviewers.storybuilding import build_storybuilding_reviewer
from .types import apply_style_suffix
from ..middleware.quota_convergence import (
    DEFAULT_MAX_MODEL_CALLS,
    QuotaConvergenceMiddleware,
)
from ..middleware.revision_limit import RevisionLimitMiddleware
from ..middleware.storyline_single_line_limit import (
    StorylineSingleLineLimitMiddleware,
)
from .storybuilding import resolve_line_budget

V14_PROMPTS = Path(__file__).resolve().parent.parent / "prompts" / "v14"
SKILLS_PATH = Path(__file__).resolve().parent.parent / "skills"

# skills compose 顺序（决定 /_skills_{i}/ 前缀，与 prompt 内指引一一对应）
SKILL_DIRS = [
    str(SKILLS_PATH / "v14" / "orchestrator-cycling"),   # /_skills_0/
    str(SKILLS_PATH / "v14" / "worldview-build"),         # /_skills_1/
    str(SKILLS_PATH / "v14" / "character-build"),         # /_skills_2/
    str(SKILLS_PATH / "v14" / "storyline-build"),         # /_skills_3/
]

# 领域写权限（DEC-006：读全量共享；写按领域隔离）
_DOMAIN_WRITE_PATHS: dict[str, list[str]] = {
    "worldview": ["/worldview.md"],
    "character": ["/character/*.md"],
    "storyline": ["/storyline.md", "/storyline/*.md"],
}

_DOMAIN_PROMPT_FILE: dict[str, str] = {
    "worldview": "domain_worldview.md",
    "character": "domain_character.md",
    "storyline": "domain_storyline.md",
}

_DOMAIN_DESCRIPTION: dict[str, str] = {
    "worldview": (
        "适用：构建或深化世界观（worldview.md）。初构产出基础世界观框架；"
        "增量在出现新势力/新地点时同步写入。只写 worldview.md。"
    ),
    "character": (
        "适用：构建人物档案（character/*.md）。初构产出主角+核心人物（核心人物≤2）；"
        "增量新增一个人物并融入现有故事（不新增故事线）。只写 character/*.md。"
    ),
    "storyline": (
        "适用：构建故事线（storyline.md 索引 + storyline/S{XX}-*.md 详情 + timeline.md）。"
        "初构仅产主线骨架（1 条主线+故事核心）；增量新增一条完整故事线。"
        "只写 storyline.md 与 storyline/*.md。"
    ),
}


def _read_prompt(name: str) -> str:
    return (V14_PROMPTS / name).read_text(encoding="utf-8").strip()


def _domain_permissions(write_paths: list[str]) -> list[FilesystemPermission]:
    perms: list[FilesystemPermission] = [
        FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
    ]
    for p in write_paths:
        perms.append(FilesystemPermission(operations=["write"], paths=[p], mode="allow"))
    perms.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"))
    return perms


def _build_domain_subagent(
    domain: str,
    middleware: list[AgentMiddleware],
) -> SubAgent:
    """构建领域 SubAgent：公共规则 + 领域职责拼接，写权限按领域隔离。"""
    system_prompt = (
        _read_prompt("common_rules.md")
        + "\n\n---\n\n"
        + _read_prompt(_DOMAIN_PROMPT_FILE[domain])
    )
    return SubAgent(
        name=domain,
        description=_DOMAIN_DESCRIPTION[domain],
        system_prompt=system_prompt,
        permissions=_domain_permissions(_DOMAIN_WRITE_PATHS[domain]),
        middleware=middleware,
    )


def build_orchestrator_agent(
    workspace_root: Path,
    model: object,
    backend: object,
    middleware_factory: Callable[[str], list[AgentMiddleware]],
    *,
    style_suffix: str | None = None,
    context_file_paths: list[str] | None = None,
    checkpointer: object | None = None,
) -> object:
    """装配 v14 多 Agent 编排（orchestrator + 3 领域 + review）。

    Args:
        workspace_root:     工作区根目录
        model:              聊天模型
        backend:            文件系统后端（领域代理共享，全量可读）
        middleware_factory: 中间件工厂（按 agent_name 生成通用骨架）
        style_suffix:       风格 suffix（作用于 orchestrator prompt）
        context_file_paths: 需 ContextAssembler 注入 orchestrator 的文件（demand.md）
        checkpointer:       checkpoint saver（顶层装配传入）

    Returns:
        编译图（create_deep_agent 产物）。
    """
    from contracts.storybuilding_quota import parse_demand_quota

    demand_path = workspace_root / "demand.md"
    demand_md = demand_path.read_text(encoding="utf-8") if demand_path.exists() else ""
    quota_target = parse_demand_quota(demand_md)

    # ---- orchestrator middleware：通用骨架 + 循环导航 + review 上限 ----
    orchestrator_mw: list[AgentMiddleware] = list(middleware_factory("orchestrator"))
    orchestrator_mw.append(QuotaConvergenceMiddleware(
        workspace_root, quota_target, max_model_calls=DEFAULT_MAX_MODEL_CALLS,
    ))
    orchestrator_mw.append(RevisionLimitMiddleware(
        max_revisions=2, review_name="review-storybuilding",
    ))
    if context_file_paths:
        from app.platform.agent.middleware import ContextAssemblerMiddleware

        orchestrator_mw.append(ContextAssemblerMiddleware(
            workspace_root, file_paths=context_file_paths, context_label="创作需求",
        ))

    # ---- storyline 领域代理追加单线护栏（预算口径与 v13 一致）----
    # reset_per_invocation=False：计数跨 task 委托累计，max_new_lines 成为整个
    # 运行的新增绝对上限（v14 中 orchestrator 会多次委托 storyline 加线，
    # 按委托重置会使运行级上限失效——review correctness-P2）。
    storyline_mw: list[AgentMiddleware] = list(middleware_factory("storyline-subagent"))
    storyline_mw.append(StorylineSingleLineLimitMiddleware(
        workspace_root,
        max_new_lines=resolve_line_budget(quota_target),
        reset_per_invocation=False,
    ))

    domain_specs = [
        _build_domain_subagent(
            "worldview", list(middleware_factory("worldview-subagent"))),
        _build_domain_subagent(
            "character", list(middleware_factory("character-subagent"))),
        _build_domain_subagent("storyline", storyline_mw),
    ]

    review_spec = build_storybuilding_reviewer(
        workspace_root, middleware_factory("storybuilding-review-subagent"),
    )
    review = SubAgent(
        name="review-storybuilding",
        description=(
            "统一审查所有故事维度（人物、世界观、故事核心、故事线、事件组）的跨维度一致性。"
            "自主读取所有产物，写入 review/storybuilding.md，返回评分和修订建议。"
        ),
        system_prompt=review_spec["system_prompt"],
        permissions=review_spec.get("permissions"),
        middleware=review_spec.get("middleware"),
    )

    orchestrator_prompt = apply_style_suffix(
        _read_prompt("orchestrator_system.md"), style_suffix,
    )

    effective_backend, skill_sources = compose_skills_backend(backend, SKILL_DIRS)

    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=orchestrator_prompt,
        subagents=[*domain_specs, review],
        middleware=orchestrator_mw,
        backend=effective_backend,
        checkpointer=checkpointer,
        skills=skill_sources,
    )


__all__ = ["SKILL_DIRS", "build_orchestrator_agent"]
