"""进化 Agent 工具集聚合入口（决策 S2 + T2.5 进化点工具）。

按功能类型分 5 模块，本 __init__.py 聚合为 make_evolve_tools(backend)：
  - inspect.py   5 探查工具（只读，给认知；含 hook 协议查询，REQ-20261001-225509）
  - writers.py   5 写 + 1 edit（受控写，封装 backend）
  - flow.py      3 流程工具（产出 + 校验）
  - points.py    4 进化点工具（对话式共创，决策 T2.5）
  - evidence.py  7 作品证据工具（trace/产物/自身历史，REQ-20261001-131018）

工具总数 25。调用方：
  from app.evolve.agent.tools import make_evolve_tools
  tools = make_evolve_tools(backend=backend)
"""
from __future__ import annotations

from app.evolve.agent.tools.evidence import make_evidence_tools
from app.evolve.agent.tools.flow import make_flow_tools
from app.evolve.agent.tools.inspect import make_inspect_tools
from app.evolve.agent.tools.points import make_points_tools
from app.evolve.agent.tools.writers import make_writer_tools


def make_evolve_tools(backend) -> list:
    """构建进化 Agent 的完整工具集（聚合 5 子模块，25 工具）。

    Args:
        backend: FilesystemBackend 实例（writers 工具内部调用它落盘）。
                 None 时报错——writers 必须有 backend 才能工作。

    Returns:
        25 个 BaseTool 实例的列表。
    """
    tools: list = []
    tools.extend(make_inspect_tools())     # 5 探查（不需 backend）
    tools.extend(make_writer_tools(backend))  # 5 写 + 1 edit（需 backend）
    tools.extend(make_flow_tools())        # 3 流程（不需 backend）
    tools.extend(make_points_tools())      # 4 进化点（决策 T2.5，不需 backend）
    tools.extend(make_evidence_tools())    # 7 作品证据（REQ-20261001-131018，不需 backend）
    return tools


__all__ = ["make_evolve_tools"]
