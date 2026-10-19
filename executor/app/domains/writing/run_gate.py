"""会话串行门（REQ-20261019-222403 FR-006/DEC-006/DEC-009）。

同一会话（客户端 thread）同一时刻只跑一轮生成：后到请求在门外 FIFO 排队
（各自的 SSE 连接由调用方保持心跳）。用户停止 → 清空队列（epoch 递增 +
取消等待者，等待者据此把消息标 cancelled 而非 paused）；排队中连接断开 →
等待者自行把消息标 paused（保留待发，客户端回访后按序补跑，不烧无人
观看的 token）。

注册表按会话 id 全局常驻（进程内）；门对象极小，不做过期回收。
"""
from __future__ import annotations

import asyncio


class ConversationGate:
    """单会话的执行权门：FIFO 锁 + 等待者登记 + 停止纪元。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._waiters: set[asyncio.Task] = set()
        # 停止清队纪元：等待者被唤醒时发现纪元已变 → 本轮作废（cancelled）。
        self.epoch = 0

    async def try_acquire(self) -> bool:
        """非阻塞获取（未锁时立即拿到）；True = 调用方负责 finally release。"""
        if self._lock.locked():
            return False
        await self._lock.acquire()  # 未锁 fast-path 不让出控制权，检查与获取原子
        return True

    async def wait_acquire(self) -> None:
        await self._lock.acquire()

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()

    def register_waiter(self, task: asyncio.Task) -> None:
        self._waiters.add(task)

    def unregister_waiter(self, task: asyncio.Task) -> None:
        self._waiters.discard(task)

    def clear_for_stop(self) -> int:
        """用户停止：纪元递增并取消全部等待者；返回被取消数。

        拿到锁的正主不被触碰（由 /stop 的 trace 级取消处理）。
        """
        self.epoch += 1
        cancelled = 0
        for task in list(self._waiters):
            if not task.done():
                task.cancel()
                cancelled += 1
        self._waiters.clear()
        return cancelled


_gates: dict[str, ConversationGate] = {}


def get_conversation_gate(conversation_id: str) -> ConversationGate:
    gate = _gates.get(conversation_id)
    if gate is None:
        gate = ConversationGate()
        _gates[conversation_id] = gate
    return gate
