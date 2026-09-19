"""versioning 包 —— harness 版本管理（去 DB 重构）。

harness 版本以独立 git 仓库为内容真相源。Phase A（REQ-20260919-202344）起
registry.json 只读（Platform 账本是仲裁源），发版原语（probe 门禁 / promote
晋升）经 release_gate 调 Platform 服务。

模块：
  registry_repo.py     registry.json 读写层（Phase A 起只读消费，写线待 Phase B 清理）
  release_gate.py      发版原语客户端（Platform probe / promote）
  snapshot_api.py      版本查询 API + 回滚（读 registry + 调 Platform promote）
  elements_api.py      Harness 要素展示（从 git 源文件读取 prompt/skills/tools/middleware）

设计依据：.claude/md/20260713_003000_harness版本机制去DB重构设计.md。
"""
