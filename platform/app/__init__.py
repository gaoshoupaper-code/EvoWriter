"""Writer platform 控制面服务（REQ-20260919-202344 Phase A）。

独立控制面：版本账本、发版门禁（probe/promote）、artifact 分发、
Run 绑定签发、resume 兼容判定。

依赖边界铁律：只依赖 contracts/ + 三方库，禁止 import executor/ 与
evolution/ 的任何代码（scripts/check_layering.py 扫描）。
"""
