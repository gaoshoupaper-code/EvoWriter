"""Writer 创作 Agent 包 —— 清单驱动装配（REQ-20261018-115508，DEC-001 单 agent）。

包 = 自包含的 Agent 定义单元。装配真相源是包根 architecture.json：agent
集合、role、prompt、domain middleware（有序）、tools、写入权限全部由清单
声明；本文件不持有「挂了什么」的事实。

五段制（单 agent 技能切换）：清单只声明一个 main（author）+ 常驻骨架提示词
prompts/author_base.md；段流程提示词（stage_theme/zonggang/juanggang/
zhanggang/zhengwen.md）由 executor 管线层按当前段经 ctx.styles 通道注入
（DEC-002 扫描装配）——styles 机制是解释器既有的 suffix 注入口，复用它注入
段规程，避免为「按段换提示词」扩清单 schema。

运行时值（model/backend/checkpointer/workspace/trace）由 ctx 注入，不进包。
基础中间件链是解释器固有的平台不变量（见 executor app.platform.agent.architecture）。

行为契约：assemble(ctx, agent_name=None) ——缺省装配首个 main（author）。
返回编译图（含 checkpointer）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from contracts.runtime_context import RuntimeContext
from contracts.writing_artifacts import (
    STAGE_CHAPTER,
    STAGE_OUTLINE,
    STAGE_TEXT,
    STAGE_THEME,
    STAGE_VOLUME,
)

logger = logging.getLogger("harness_package")

# 包目录（本 __init__.py 所在目录）
PACKAGE_DIR = Path(__file__).resolve().parent

# 大段 → 段流程提示词文件（executor 按当前段读取后经 styles 注入）
STAGE_PROMPT_FILES: dict[str, str] = {
    STAGE_THEME: "stage_theme.md",
    STAGE_OUTLINE: "stage_zonggang.md",
    STAGE_VOLUME: "stage_juangang.md",
    STAGE_CHAPTER: "stage_zhanggang.md",
    STAGE_TEXT: "stage_zhengwen.md",
}


def stage_prompt(stage: str) -> str | None:
    """读当前段的流程提示词文本（DEC-002：executor 每轮装配时调用）。

    未知段返回 None（调用方不注入，仅常驻骨架生效）。
    """
    filename = STAGE_PROMPT_FILES.get(stage)
    if not filename:
        return None
    path = PACKAGE_DIR / "prompts" / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("段提示词读取失败: stage=%s file=%s", stage, path)
        return None


def assemble(ctx: RuntimeContext, agent_name: str | None = None):
    """清单驱动装配：薄委托架构清单解释器（读 architecture.json 实例化）。

    Args:
        agent_name: 按名装配的 main agent；None = 首个 main（author）。
    """
    from app.platform.agent.architecture import assemble_from_manifest

    return assemble_from_manifest(ctx, package=__package__, agent_name=agent_name)


__all__ = ["PACKAGE_DIR", "STAGE_PROMPT_FILES", "assemble", "stage_prompt"]
