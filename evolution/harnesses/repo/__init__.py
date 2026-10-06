"""Writer 创作 Agent 包 —— 清单驱动装配（REQ-20261006-130414 FR-001，DEC-007 M2）。

包 = 自包含的 Agent 定义单元。装配真相源是包根 architecture.json（M2 完整
清单）：agent 集合、role、prompt、domain middleware（有序）、skills、委托
关系、写入权限全部由清单声明；本文件不持有「挂了什么」的事实。

运行时值（model/backend/checkpointer/workspace/trace）由 ctx 注入，不进包。
基础中间件链与审查闭环模式是解释器固有的平台不变量（静态/动态边界见
executor app.platform.agent.architecture 模块 docstring）。

行为契约不变：assemble(ctx) 单参数、返回编译图（含 checkpointer）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from contracts.runtime_context import RuntimeContext

logger = logging.getLogger("harness_package")

# 包目录（本 __init__.py 所在目录）
PACKAGE_DIR = Path(__file__).resolve().parent


def assemble(ctx: RuntimeContext):
    """清单驱动装配：薄委托架构清单解释器（读 architecture.json 实例化）。"""
    from app.platform.agent.architecture import assemble_from_manifest

    return assemble_from_manifest(ctx, package=__package__)


__all__ = ["assemble"]
