"""观测事件流 SSE 通道（REQ-20260920-193428 FR-004/DEC-004/DEC-005）。

通用推送总线 + SSE 端点：run_started / run_finished 事件广播给所有订阅客户端。
本期只接大盘（FR-005）；详情页/会话流后续可复用同一通道（DEC-005 边界）。

事件生产者：
- active poller diff（executor 源 started + 活跃列表消失的兜底 finished）→ view/active.py
- ingestion notify（executor 源 finished，即时带终态摘要）→ ingestion/ingestion.py
- evolution recorder（本进程会话 started/finished，进程内直发）→ trace/recorder.py

协议：text/event-stream；帧三件套 event/id/data，id=全局单调 seq。
?since=<seq> 断线重连回放（环形缓冲 _REPLAY_CAPACITY 帧；溢出后客户端检测
seq 跳变应重拉快照对齐，不依赖回放完整）。
心跳：空闲 _HEARTBEAT_SECONDS 发 ": ping" 注释帧（nginx proxy_read_timeout 24h）。

线程模型：publish 可从任意线程调用（active poller 跑在 to_thread 工作线程、
recorder 回调在业务线程），经 loop.call_soon_threadsafe 转回事件循环分发；
subscribe 只在事件循环内（SSE 端点）执行并捕获 loop。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger("evolution.events")

router = APIRouter(tags=["events"])

_HEARTBEAT_SECONDS = 15.0
_REPLAY_CAPACITY = 256
_SUBSCRIBER_QUEUE_SIZE = 512


class EventBus:
    """进程内广播总线：publish（任意线程）→ 所有 SSE 订阅者。"""

    def __init__(self) -> None:
        self._seq = 0
        self._lock = threading.Lock()
        self._buffer: deque[tuple[int, str]] = deque(maxlen=_REPLAY_CAPACITY)
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue[str]:
        """注册一个订阅队列（必须在事件循环内调用）。"""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        self._loop = asyncio.get_running_loop()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    def current_seq(self) -> int:
        with self._lock:
            return self._seq

    def replay(self, since_seq: int) -> list[str]:
        """回放缓冲里 seq > since_seq 的帧（断线重连补齐）。"""
        with self._lock:
            return [frame for seq, frame in self._buffer if seq > since_seq]

    def publish(self, event_type: str, data: dict[str, Any]) -> int:
        """发布一条事件，返回分配的 seq。线程安全。"""
        with self._lock:
            self._seq += 1
            seq = self._seq
            envelope = {
                "type": event_type,
                "seq": seq,
                "emitted_at": datetime.now(UTC).isoformat(),
                "data": data,
            }
            frame = (
                f"event: {event_type}\n"
                f"id: {seq}\n"
                f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"
            )
            self._buffer.append((seq, frame))
        self._dispatch(frame)
        return seq

    def _dispatch(self, frame: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # 从无订阅者（SSE 端点未被使用），事件只进缓冲供回放。
        try:
            loop.call_soon_threadsafe(self._fanout, frame)
        except RuntimeError:
            pass  # loop 关闭竞态：丢弃，客户端重连后靠 replay/快照对齐。

    def _fanout(self, frame: str) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                # 慢消费者：丢最老帧保总线不阻塞；客户端检测 seq 跳变后重拉快照。
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(frame)


_bus = EventBus()


def get_event_bus() -> EventBus:
    """进程级单例（与 active poller / recorder / ingestion 共用同一总线）。"""
    return _bus


@router.get("/events/stream")
async def event_stream(since: int = Query(0, ge=0)) -> StreamingResponse:
    """观测事件 SSE 流（大盘订阅入口）。鉴权由 SSOAuthMiddleware 全局覆盖。"""
    queue = _bus.subscribe()

    async def _generate():
        try:
            for frame in _bus.replay(since):
                yield frame
            while True:
                try:
                    frame = await asyncio.wait_for(
                        queue.get(), timeout=_HEARTBEAT_SECONDS
                    )
                    yield frame
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _bus.unsubscribe(queue)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 双保险：nginx 全局已 proxy_buffering off，此处按响应再声明一次。
            "X-Accel-Buffering": "no",
        },
    )
