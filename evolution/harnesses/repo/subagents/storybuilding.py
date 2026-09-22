"""Storybuilding 子代理 — 双层渐进式故事构建。

架构：
- 索引文件 storyline.md：故事核心 + 故事线一览表
- 故事线详情 storyline/S{XX}-{名}.md：每条线一个文件，含事件组与事件详情
- 人物（character/*.md）和世界观（worldview.md）由单代理统一维护

事件以事件组为单位插入故事线，按三幕式比例编排。
每次产出后自动调用 review 进行跨维度统一审查，单次审查修订（仅调用 1 次 review）。

导出的公共 API：
  - build_storybuilding_subagent():        构建故事构建子代理规格
  - build_storybuilding_deep_subagent():   构建 DeepAgent 版本（含统一审查循环）
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware

from app.platform.agent.runtime import (
    CompiledSubAgent,
    FilesystemPermission,
    SubAgent,
)

from .factory import build_deep_subagent
from .reviewers.storybuilding import build_storybuilding_reviewer
from .types import apply_style_suffix
from ..middleware.quota_convergence import (
    DEFAULT_MAX_MODEL_CALLS,
    QuotaConvergenceMiddleware,
)
from ..middleware.storyline_single_line_limit import (
    StorylineSingleLineLimitMiddleware,
)
from app.platform.agent.middleware import ContextAssemblerMiddleware

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "storybuilding_system.md"
SKILLS_PATH_BASE = Path(__file__).resolve().parent.parent / "skills"

# 连续增量负载（REQ-20260922-162823 FR-002）：
#   - 有配比（full/semi）：单线护栏预算 = 目标线总数 + 余量（防中途废稿重写卡护栏）
#   - 无配比（minimal / 生产缺字段，DEC-013 软终止）：固定宽松上限防失控
#     （与 QuotaConvergence 的模型调用预算构成双层防护）
_MINIMAL_LINE_BUDGET = 8
_LINE_BUDGET_MARGIN = 2


def resolve_line_budget(target) -> int:
    """按 demand 目标配比计算本次运行的新增故事线预算（护栏 max_new_lines）。"""
    if target is None:
        return _MINIMAL_LINE_BUDGET
    return target.total() + _LINE_BUDGET_MARGIN


def build_storybuilding_subagent(
    workspace_root: Path,
    middleware: list[AgentMiddleware] | None = None,
    style_suffix: str | None = None,
) -> SubAgent:
    """构建故事构建子代理规格。

    写入权限覆盖：character/, worldview.md, storyline.md, storyline/*.md

    Args:
        workspace_root: 工作区根目录
        middleware:     额外中间件列表（可选）
        style_suffix:   风格 SUFFIX 文本（可选）

    Returns:
        故事构建子代理规格字典
    """
    system_prompt = apply_style_suffix(
        PROMPT_PATH.read_text(encoding="utf-8").strip(),
        style_suffix,
    )

    permissions = [
        # 读取：允许读取所有文件
        FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
        # 写入：允许写入 3 个维度
        FilesystemPermission(operations=["write"], paths=["/character/*.md"], mode="allow"),
        FilesystemPermission(operations=["write"], paths=["/worldview.md"], mode="allow"),
        FilesystemPermission(operations=["write"], paths=["/storyline.md"], mode="allow"),
        FilesystemPermission(operations=["write"], paths=["/storyline/*.md"], mode="allow"),
        # 拒绝：禁止写入其他所有文件
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]

    spec = SubAgent(
        name="storybuilding",
        description=(
            "适用：需要构建或扩展小说故事世界时调用——包括人物、世界观、"
            "故事核心、故事线（含事件组）。"
            "增量迭代：按人物/故事线的比值分流——人物充足(≥3)新增一条故事线，"
            "人物不足(<3)新增一个人物并融入现有故事(不新增故事线)；"
            "每次调用只执行一种模式，可循环多次调用。"
        ),
        system_prompt=system_prompt,
        permissions=permissions,
    )
    if middleware is not None:
        spec["middleware"] = middleware
    return spec


def build_storybuilding_deep_subagent(
    workspace_root: Path,
    model: object,
    backend: object,
    middleware_factory: Callable[[str], list[AgentMiddleware]],
    style_suffix: str | None = None,
    context_file_paths: list[str] | None = None,
    checkpointer: object | None = None,
) -> CompiledSubAgent:
    """构建基于 DeepAgent 的 storybuilding 子代理（含统一审查循环）。

    v7（REQ-20260920-150149 FR-001）：本装配即「单故事专家 Agent」——原 v6 中
    storybuilding 是 meta 编排下的子代理，v7 删除多 Agent 编排后将其提升为
    顶层装配（assemble 直接返回本装配的 runnable），行为与产物契约不变。

    子代理自主决策：产出/扩展各维度 → 调用 review 统一审查 → 根据反馈修订。
    默认不使用 ContextAssemblerMiddleware——agent 根据任务自主读取所需文件。
    但可通过 context_file_paths 注入指定文件（如 demand.md）。

    Args:
        workspace_root:      工作区根目录
        model:               聊天模型
        backend:             DeepAgents 后端（文件系统）
        middleware_factory:  中间件工厂函数，按 agent_name 生成对应中间件列表
        style_suffix:        风格 SUFFIX 文本（可选）
        context_file_paths:  需要通过中间件自动注入的文件路径列表（可选）
        checkpointer:        checkpoint saver（顶层装配时传入；子代理场景 None）

    Returns:
        编译后的子代理字典 {name, description, runnable}
    """
    # ---- 主代理 middleware ----
    storybuilding_middleware = list(middleware_factory("storybuilding-subagent"))
    # 连续增量护栏（REQ-20260922-162823 FR-002，DEC-011 同一判定器）：
    #   1. 单线护栏按 demand 目标配比放宽为线数预算（原 v12 固定 max_new_lines=1，
    #      连续增量负载下会在第 2 条线被硬拦，见需求查证事实）；
    #   2. QuotaConvergence 每轮注入「继续增量/达标收尾/预算耗尽」导航指令，
    #      与 v14 多 Agent 版挂载同一份实现，终止语义一致。
    # 注意：ReadCache 已由 middleware_factory 全 agent 装配（A2-D2），此处不重复装配。
    demand_path = workspace_root / "demand.md"
    demand_md = (
        demand_path.read_text(encoding="utf-8") if demand_path.exists() else ""
    )
    from contracts.storybuilding_quota import parse_demand_quota

    quota_target = parse_demand_quota(demand_md)
    storybuilding_middleware.append(StorylineSingleLineLimitMiddleware(
        workspace_root,
        max_new_lines=resolve_line_budget(quota_target),
    ))
    storybuilding_middleware.append(QuotaConvergenceMiddleware(
        workspace_root,
        quota_target,
        max_model_calls=DEFAULT_MAX_MODEL_CALLS,
    ))
    if context_file_paths:
        storybuilding_middleware.append(ContextAssemblerMiddleware(
            workspace_root,
            file_paths=context_file_paths,
            context_label="创作需求",
        ))
    primary_spec = build_storybuilding_subagent(
        workspace_root, storybuilding_middleware, style_suffix
    )

    # ---- 统一审查子代理规格（审查器自主读取所有文件） ----
    review_spec = build_storybuilding_reviewer(
        workspace_root,
        middleware_factory("storybuilding-review-subagent"),
    )

    # ---- 构建 review SubAgent dict ----
    review = SubAgent(
        name="review",
        description=(
            "统一审查所有故事维度（人物、世界观、故事核心、故事线、事件组）的跨维度一致性。"
            "自主读取所有产物，写入 review/storybuilding.md，返回评分和修订建议。"
        ),
        system_prompt=review_spec["system_prompt"],
        permissions=review_spec.get("permissions"),
        middleware=review_spec.get("middleware"),
    )

    # ---- Skill 路径：初构 + 增量 ----
    skills = [
        str(SKILLS_PATH_BASE / "storybuilding-initial"),
        str(SKILLS_PATH_BASE / "storybuilding-expand"),
    ]

    # ---- 调用工厂 ----
    return build_deep_subagent(
        name="storybuilding",
        description=(
            "适用：需要构建或扩展小说故事世界时调用——包括人物、世界观、"
            "故事核心、故事线（含事件组）。"
            "双层架构：storyline.md 留故事核心+故事线一览表（索引），"
            "每条故事线详情（含事件组）拆到 storyline/S{XX}-{名}.md，一条一个文件。"
            "事件以事件组为单位插入，按三幕式比例编排。"
            "增量迭代：按人物/故事线比值分流两种互斥模式——"
            "人物充足(≥3)新增一条故事线，人物不足(<3)新增一个人物并融入现有故事、不新增故事线；"
            "每次调用只执行一种模式，可循环多次调用。"
            "内置统一审查：产出后调用 review 审查跨维度一致性，单次审查修订（仅 1 次）。"
            "委托时必须说明：使用初构还是增量 Skill、本轮焦点、用户扩展方向。"
        ),
        model=model,
        system_prompt=primary_spec["system_prompt"],
        review_spec=review,
        subagent_middleware=primary_spec.get("middleware"),
        backend=backend,
        artifact_paths=[workspace_root / "storyline.md", workspace_root / "storyline", workspace_root / "storyline" / "timeline.md"],
        max_revisions=2,
        skills=skills,
        checkpointer=checkpointer,
    )
