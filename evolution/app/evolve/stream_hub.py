"""会话级流式帧广播（REQ-20261019-183921 FR-004，DEC-001 流式路线）。

per-session 订阅队列集合：round 执行侧（_run_agent_streamed / _run_round_bg /
send_message）把行协议帧与信号帧 publish 进来，SSE 端点
（GET /evolve/sessions/{id}/stream）订阅后逐帧转发前端。

与观测总线（view/events.py EventBus）的区别：
- 按 session 隔离（对话流不跨会话广播）
- 无回放缓冲：行帧是过程性展示，断线后以落库消息收敛（FR-006 失败语义），
  不依赖 SSE 补帧——前端重连后拉一次消息 + 续订即可
- 帧为 dict（序列化在 SSE 端点统一做）

线程模型：publish 通常发生在事件循环内（agent round / API handler），但
recorder 回调等线程边缘路径存在，沿用 EventBus 的 call_soon_threadsafe 分发。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger("evolution.evolve.stream_hub")

# 慢消费者保护：队列满丢最老帧（行帧丢失可接受，终态以落库为准）。
_SUBSCRIBER_QUEUE_SIZE = 512


class SessionStreamHub:
    """进程内 per-session 帧广播器。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        """注册一个订阅队列（必须在事件循环内调用）。"""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        self._loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """摘除订阅；会话最后一个订阅者离开时清掉整个桶。"""
        with self._lock:
            subs = self._subscribers.get(session_id)
            if subs is None:
                return
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(session_id, None)

    def publish(self, session_id: str, frame: dict[str, Any]) -> None:
        """向该会话的全部订阅者广播一帧。无订阅者时静默丢弃（行帧不落任何存储）。"""
        with self._lock:
            queues = list(self._subscribers.get(session_id, ()))
        if not queues:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, queues, frame)
        except RuntimeError:
            # loop 关闭竞态：丢弃，客户端重连后靠落库消息对齐。
            pass

    def _fanout(self, queues: list[asyncio.Queue[dict[str, Any]]], frame: dict[str, Any]) -> None:
        for queue in queues:
            if queue.full():
                # 慢消费者：丢最老帧保总线不阻塞；终态帧（round_end 等）随后到达
                # 仍能解锁前端，中间行丢失由落库收敛。
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(frame)


_hub = SessionStreamHub()


def get_stream_hub() -> SessionStreamHub:
    """进程级单例。"""
    return _hub


__all__ = ["SessionStreamHub", "get_stream_hub"]
