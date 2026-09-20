"""专家代理服务模块（v7 瘦身后仅保留 services）。

v7 架构切换（REQ-20260920-150149 FR-102）删除了 executor 内硬编码的
多代理装配（agents/evaluators/prompts/skills/factory/types/meta 编排）——
Agent 定义统一收敛到 evolution harness 包（assemble(ctx) 单参数契约）。

本模块保留领域服务（character / storyline_graph），供 live 路径消费：
  - services/storyline_graph.py — 故事线图谱生成（desktop ScriptPanel 消费）
  - services/character.py — 人物服务
"""
