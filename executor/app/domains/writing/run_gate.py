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


_gates: dict[tuple[object, str], ConversationGate] = {}


def get_conversation_gate(conversation_id: str) -> ConversationGate:
    """取会话门。注册表按（事件循环, 会话 id）隔离：

    asyncio.Lock 绑定首个竞争它的循环，跨循环复用会 RuntimeError。生产
    单进程单循环下等价于全局唯一；测试/多循环环境各自拿各自的门，
    不串台（review R-loop：模块级单键注册表曾让测试间泄漏锁绑定）。
    """
    loop = asyncio.get_running_loop()
    key = (loop, conversation_id)
    gate = _gates.get(key)
    if gate is None:
        gate = ConversationGate()
        _gates[key] = gate
    return gate
