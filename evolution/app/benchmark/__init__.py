"""评测（benchmark）主链路模块（REQ-20260919-172934 重构为评测驱动架构）。

跨版本 harness 在同一 golden 数据集上跑全 case × seed → LLM 评测 → 弱点报告/CI 三态对比。

  repo.py      — benchmark_runs 表 CRUD + 矩阵查询 + 指纹绑定
  runner.py    — 后台异步执行（case × 版本 × seed → 调 executor → 轮询 → 评测评分 → 写表）
  rubric_v3.py — 评测评分标准（5 主观维 + 交付完整规则项，DEC-009/010/011）
  scorer.py    — 评分引擎（ArtifactRevision 直读 + judge 调用 + 契约校验）
  manifest.py  — 运行装配指纹（harness + 被测模型 + judge 配置，DEC-012/015）
  stats.py     — Welch CI 三态版本对比（DEC-007/014）
  report.py    — 弱点报告聚合（FR-005/006）
  api.py       — 触发 run / 查 leaderboard / 查批次 / 弱点报告 / 版本对比

触发点：
  1. 开发迭代手动触发「跑评测」（DEC-002 自用导向）
  2. golden 升级后手动触发「重跑最近 K=3 版本」
"""
