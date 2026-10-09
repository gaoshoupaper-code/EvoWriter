"""需求澄清提问工具挂载（REQ-20261009-224433 FR-003）。

清单驱动装配：architecture.json 声明 storybuilding 挂载本工具后，故事专家
获得向用户提问并等待回答（HITL interrupt → Command(resume) 续跑）的能力，
用于首次构建前的需求澄清（系统提示词 §〇）。

实现薄委托 executor 平台通用 build_ask_user_tool（与 image 域同源），
interrupt/resume 协议单点维护，不在包内复制机制代码。
"""
from __future__ import annotations


def build(abc):  # noqa: ANN001, ARG001 —— 签名是清单挂载契约（tools/{name}.py 的 build(abc) 钩子），本工具不依赖装配上下文
    from app.platform.tools import build_ask_user_tool

    return build_ask_user_tool()
