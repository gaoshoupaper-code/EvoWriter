"""隔离执行边界（FR-006 / NFR-001 / DEC-002）。

把不可抢占的长执行（DeepAgent stream 内的长 LLM / 长工具）放进独立子进程，
父进程持有句柄，取消时可在十秒时限内可靠终止（process.kill），不受 C 层阻塞影响。

DeepAgent 框架本身不变（CON-005），只是被装进隔离子进程跑。
"""

from __future__ import annotations

__all__ = ["IsolatedGenerationWorker", "WorkerResult"]


def __getattr__(name):
    if name in __all__:
        from . import worker_process
        return getattr(worker_process, name)
    raise AttributeError(name)
