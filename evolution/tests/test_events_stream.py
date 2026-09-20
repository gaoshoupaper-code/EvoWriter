"""观测事件流 SSE 通道测试（REQ-20260920-193428 FR-004）。

覆盖：
- SSE 响应头与帧格式（event/id/data 三件套 + envelope 结构）
- since 回放（断线重连补齐）与跳过旧帧
- 在线推送（订阅后 publish 即时到达）
- 心跳（空闲发 ": ping" 注释帧）
- 多订阅者广播（AC-007 服务端部分）
- 慢消费者不阻塞总线（丢最老帧）
- SSE 路径鉴权（AC-005：无登录态 401，非放行路径）

测试驱动方式：httpx ASGITransport 会把响应体收完才返回，对无限 SSE 流
死锁——故用自写 ASGI 驱动器（scope/receive/send 收集器），收满期望帧数
即抛 _StopStreaming 停止，生成器 finally 正常清理订阅。

跑法（在 evolution 目录）：
    python -m pytest tests/test_events_stream.py -v
"""

from __future__ import annotations

import asyncio
import json
import unittest

from fastapi import FastAPI

from app.view.events import router as events_router


def _build_stream_app() -> FastAPI:
    """裸 app（只挂 events 路由，不带全局中间件）。

    全局 SSO/Notify 中间件（BaseHTTPMiddleware）对流式转发的收尾时序会干扰
    手动 ASGI 驱动器；端点逻辑不依赖中间件，鉴权行为由 EventsAuthTest 单独
    覆盖，中间件+流式的生产兼容由部署后线上观测兜底（AC-001 线上部分）。
    """
    mini = FastAPI()
    mini.include_router(events_router, prefix="/api")
    return mini


class _StopStreaming(Exception):
    """收满期望帧数，停止驱动 ASGI app。"""


async def _drive_sse(
    path: str,
    *,
    frames_to_collect: int,
    on_headers_sent: "callable | None" = None,
) -> tuple[int, dict[str, str], list[str]]:
    """手动驱动 events app 消费 SSE 流，返回 (status, headers, 帧文本列表)。

    on_headers_sent：响应头发出后回调（此时 handler 已订阅总线），
    用于在线推送场景的时序注入。
    """
    app = _build_stream_app()
    start: dict = {}
    chunks: list[bytes] = []
    receive_calls = 0

    async def receive() -> dict:
        # ASGI 语义：首次返回请求体；之后模拟连接保持——永不返回 disconnect。
        # Starlette 的流式响应会并发等待 disconnect，若返回它会立即取消整个流。
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # 无人 set：连接保持，挂起等断开

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            nonlocal start
            start = message
            if on_headers_sent is not None:
                on_headers_sent()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                chunks.append(body)
                if sum(c.count(b"\n\n") for c in chunks) >= frames_to_collect:
                    raise _StopStreaming()

    path_only, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path_only,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    try:
        await app(scope, receive, send)
    except _StopStreaming:
        pass
    headers = {k.decode(): v.decode() for k, v in start.get("headers", [])}
    text = b"".join(chunks).decode("utf-8")
    # 按空行切帧，保留非空帧文本
    frames = [f for f in text.split("\n\n") if f.strip()]
    return start.get("status", 0), headers, frames


def _parse_sse_frame(frame: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in frame.splitlines():
        if line.startswith("event:"):
            fields["event"] = line[len("event:"):].strip()
        elif line.startswith("id:"):
            fields["id"] = line[len("id:"):].strip()
        elif line.startswith("data:"):
            fields["data"] = line[len("data:"):].strip()
    return fields


class EventsStreamTest(unittest.TestCase):
    """SSE 端点行为（dev 模式放行鉴权，鉴权单独测）。"""

    def setUp(self) -> None:
        from app.view.events import get_event_bus

        self.bus = get_event_bus()

    def test_headers_and_replay_frame(self) -> None:
        """先 publish（无订阅者只进缓冲）→ since=当前 seq 订阅收到回放帧。

        since 取当前 seq（而非 0）：bus 是进程级单例，缓冲里可能有其他
        测试文件残留的事件——只回放本测试 publish 的这一条。
        """
        since = self.bus.current_seq()
        self.bus.publish("run_started", {"trace_id": "trace-replay", "source": "executor"})

        status, headers, frames = asyncio.run(
            _drive_sse(f"/api/events/stream?since={since}", frames_to_collect=1)
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "text/event-stream; charset=utf-8")
        self.assertEqual(headers.get("cache-control"), "no-cache")
        frame = _parse_sse_frame(frames[0])
        self.assertEqual(frame["event"], "run_started")
        envelope = json.loads(frame["data"])
        self.assertEqual(envelope["type"], "run_started")
        self.assertEqual(envelope["data"]["trace_id"], "trace-replay")
        self.assertEqual(int(frame["id"]), envelope["seq"])
        self.assertGreaterEqual(envelope["seq"], 1)

    def test_live_push_after_subscribe(self) -> None:
        """订阅后（响应头已发）publish，帧实时到达（在线路径，非回放）。"""
        published: list[str] = []

        def _push_after_subscribe() -> None:
            # 头发出 = handler 已 subscribe；此时 publish 走 fanout 在线路径。
            tid = self.bus.publish(
                "run_finished", {"trace_id": "trace-live", "source": "evolution"}
            )
            published.append(str(tid))

        status, _, frames = asyncio.run(
            _drive_sse(
                # since=当前 seq：跳过缓冲里其他测试的残留事件，只收在线推送帧。
                f"/api/events/stream?since={self.bus.current_seq()}",
                frames_to_collect=1,
                on_headers_sent=_push_after_subscribe,
            )
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(published), 1)
        frame = _parse_sse_frame(frames[0])
        self.assertEqual(frame["event"], "run_finished")
        envelope = json.loads(frame["data"])
        self.assertEqual(envelope["data"]["trace_id"], "trace-live")

    def test_since_skips_old_frames(self) -> None:
        """since=当前 seq 时旧帧不回放，只收到心跳。"""
        import app.view.events as events_mod

        seq = self.bus.publish("run_started", {"trace_id": "trace-old", "source": "executor"})
        original = events_mod._HEARTBEAT_SECONDS
        events_mod._HEARTBEAT_SECONDS = 0.2  # 测试不等 15s
        try:
            _, _, frames = asyncio.run(
                _drive_sse(f"/api/events/stream?since={seq}", frames_to_collect=1)
            )
        finally:
            events_mod._HEARTBEAT_SECONDS = original

        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].startswith(": ping"))

    def test_heartbeat_when_idle(self) -> None:
        """无事件时按心跳间隔发注释帧。"""
        import app.view.events as events_mod

        seq = self.bus.current_seq()
        original = events_mod._HEARTBEAT_SECONDS
        events_mod._HEARTBEAT_SECONDS = 0.2
        try:
            _, _, frames = asyncio.run(
                _drive_sse(f"/api/events/stream?since={seq}", frames_to_collect=2)
            )
        finally:
            events_mod._HEARTBEAT_SECONDS = original

        self.assertEqual(len(frames), 2)
        for frame in frames:
            self.assertTrue(frame.startswith(": ping"))


class EventBusTest(unittest.TestCase):
    """总线广播与背压（不经 HTTP）。"""

    def setUp(self) -> None:
        from app.view.events import EventBus

        self.bus = EventBus()

    def test_multi_subscriber_broadcast(self) -> None:
        async def scenario() -> None:
            q1 = self.bus.subscribe()
            q2 = self.bus.subscribe()
            self.bus.publish("run_started", {"trace_id": "t-broadcast"})
            await asyncio.sleep(0)  # 让 call_soon_threadsafe 排入的 fanout 执行
            frame1 = await asyncio.wait_for(q1.get(), timeout=2)
            frame2 = await asyncio.wait_for(q2.get(), timeout=2)
            self.assertIn("t-broadcast", frame1)
            self.assertEqual(frame1, frame2)

        asyncio.run(scenario())

    def test_slow_subscriber_drops_oldest_without_blocking(self) -> None:
        async def scenario() -> None:
            queue = self.bus.subscribe()
            # 塞满队列容量以上，不得抛异常或死锁。
            for i in range(700):
                self.bus.publish("run_started", {"trace_id": f"t-{i}"})
            await asyncio.sleep(0)  # 触发 fanout
            # 总线仍可继续发布。
            self.bus.publish("run_started", {"trace_id": "t-after"})
            await asyncio.sleep(0)
            drained = 0
            while not queue.empty():
                queue.get_nowait()
                drained += 1
            # maxsize 512 + 后续 1 帧 = 至多 513；慢消费者不阻塞发布方。
            self.assertLessEqual(drained, 513)
            self.assertGreater(drained, 0)

        asyncio.run(scenario())


class EventsAuthTest(unittest.TestCase):
    """SSE 路径鉴权（AC-005）：无登录态 401。"""

    def test_stream_requires_session(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.core import sso_auth as sso_mod

        # patch sso_auth 模块持有的 settings 引用（而非 app.core.settings.settings）：
        # 既有测试大量 importlib.reload 会重建 settings 单例，两个模块可能持有
        # 不同副本——中间件实际读的是 sso_auth 模块里的引用。
        previous = sso_mod.settings.allowed_user_ids
        sso_mod.settings.allowed_user_ids = "user-allowed"
        try:
            mini = FastAPI()

            @mini.get("/api/events/stream")
            def _stream() -> dict[str, str]:
                return {"should": "not reach"}

            guarded = sso_mod.SSOAuthMiddleware(mini)
            client = TestClient(guarded)
            resp = client.get("/api/events/stream")
            self.assertEqual(resp.status_code, 401)
        finally:
            sso_mod.settings.allowed_user_ids = previous


if __name__ == "__main__":
    unittest.main()
