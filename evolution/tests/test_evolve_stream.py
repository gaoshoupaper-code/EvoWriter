"""会话级流式通道测试（REQ-20261019-183921 FR-004/FR-005/FR-006）。

覆盖：
- SessionStreamHub：订阅/广播/慢消费者/桶清理（AC-003 服务端部分）
- EvolveEventSink 行装配：流式 text/reasoning、非流式兜底、工具行终态、
  快照 rows 维护（AC-003 降级设计：无 reasoning 自动只有 text 行）
- SSE 端点：404、snapshot 帧、在线推送、心跳（AC-003）
- _run_agent_streamed 集成：假 agent 事件序列 → 落库消息 + hub 帧序 +
  rows_json 快照挂载（AC-003/AC-005 服务端部分）

跑法（在 evolution 目录）：
    python -m pytest tests/test_evolve_stream.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

import app.core.db as db
from app.core.settings import settings

_old_db = settings.evolution_db


def setUpModule() -> None:
    settings.evolution_db = _tmp_db.name
    db._conn = None
    db.init_db()


def tearDownModule() -> None:
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    settings.evolution_db = _old_db


# ── 测试辅助 ─────────────────────────────────────────────────


class _FakeAgent:
    """假 CompiledStateGraph：astream_events 回放预置事件序列。"""

    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def astream_events(self, input, config=None, version=None):
        async def gen():
            for e in self._events:
                yield e
        return gen()


def _stream_chunk(content: str = "", reasoning: str = "", run_id: str = "r1") -> dict:
    chunk = types.SimpleNamespace(
        content=content,
        additional_kwargs={"reasoning_content": reasoning} if reasoning else {},
    )
    return {"event": "on_chat_model_stream", "run_id": run_id, "data": {"chunk": chunk}}


def _model_end(text: str, tool_calls=None, run_id: str = "r1") -> dict:
    output = types.SimpleNamespace(
        content=text, tool_calls=tool_calls or [],
    )
    return {"event": "on_chat_model_end", "run_id": run_id, "data": {"output": output}}


def _tools_start(calls: list[dict]) -> dict:
    return {"event": "on_chain_start", "name": "tools", "data": {"input": calls}}


def _tool_end(name: str, call_id: str, output: str) -> dict:
    out = types.SimpleNamespace(content=output, tool_call_id=call_id)
    return {"event": "on_tool_end", "name": name, "data": {"output": out}}


def _tool_error(name: str, call_id: str, err: str) -> dict:
    return {"event": "on_tool_error", "name": name,
            "data": {"error": err, "call_id": call_id}}


# ── SessionStreamHub ─────────────────────────────────────────


class SessionStreamHubTest(unittest.TestCase):
    """hub 广播与背压（不经 HTTP）。"""

    def setUp(self):
        from app.evolve.stream_hub import SessionStreamHub
        self.hub = SessionStreamHub()

    def test_publish_reaches_subscriber(self):
        async def scenario():
            q = self.hub.subscribe("s1")
            self.hub.publish("s1", {"type": "row", "op": "delta", "text": "x"})
            await asyncio.sleep(0)  # 让 call_soon_threadsafe 排入的 fanout 执行
            frame = await asyncio.wait_for(q.get(), timeout=2)
            self.assertEqual(frame["text"], "x")

        asyncio.run(scenario())

    def test_publish_without_subscriber_is_silent(self):
        self.hub.publish("s-ghost", {"type": "row"})  # 不得抛异常
        self.assertNotIn("s-ghost", self.hub._subscribers)

    def test_session_isolation(self):
        async def scenario():
            q1 = self.hub.subscribe("s-a")
            q2 = self.hub.subscribe("s-b")
            self.hub.publish("s-a", {"type": "row"})
            await asyncio.sleep(0)
            self.assertEqual(q1.qsize(), 1)
            self.assertEqual(q2.qsize(), 0)

        asyncio.run(scenario())

    def test_slow_subscriber_drops_oldest(self):
        async def scenario():
            q = self.hub.subscribe("s-slow")
            for i in range(600):  # 超过 maxsize=512
                self.hub.publish("s-slow", {"type": "row", "seq": i})
            await asyncio.sleep(0)
            self.assertLessEqual(q.qsize(), 512)
            self.assertGreater(q.qsize(), 0)

        asyncio.run(scenario())

    def test_unsubscribe_cleans_bucket(self):
        async def scenario():
            q = self.hub.subscribe("s-clean")
            self.hub.unsubscribe("s-clean", q)
            self.assertNotIn("s-clean", self.hub._subscribers)

        asyncio.run(scenario())


# ── EvolveEventSink 行装配 ───────────────────────────────────


class SinkRowAssemblyTest(unittest.TestCase):
    """行协议帧装配（与 executor WritingEventSink 同构语义）。"""

    def setUp(self):
        from app.evolve.agent.event_sink import EvolveEventSink
        self.sink = EvolveEventSink(session_id="s-row")

    def _frames(self, events: list[dict]) -> list[dict]:
        out: list[dict] = []
        for e in events:
            out.extend(asyncio.run(self.sink.on_event_dicts(e)))
        return out

    def test_streamed_text_and_end_with_full_snapshot(self):
        frames = self._frames([
            _stream_chunk(content="你"), _stream_chunk(content="好"),
            _model_end("你好", run_id="r1"),
        ])
        rows = [f for f in frames if f.get("type") == "row"]
        opens = [f for f in rows if f["op"] == "open"]
        deltas = [f for f in rows if f["op"] == "delta"]
        ends = [f for f in rows if f["op"] == "end"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["kind"], "text")
        self.assertEqual([d["text"] for d in deltas], ["你", "好"])
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]["state"], "complete")
        # end 带全量快照（前端整体替换去重）
        self.assertEqual(ends[0]["text"], "你好")

    def test_reasoning_row_when_model_supports_it(self):
        frames = self._frames([
            _stream_chunk(reasoning="先想想", run_id="r1"),
            _stream_chunk(content="答案", run_id="r1"),
            _model_end("答案", run_id="r1"),
        ])
        rows = [f for f in frames if f.get("type") == "row"]
        kinds = [f.get("kind") for f in rows if f["op"] == "open"]
        self.assertIn("reasoning", kinds)
        self.assertIn("text", kinds)
        # 快照里两种行都有，reasoning 行不吞正文
        snapshot_kinds = [r["kind"] for r in self.sink.rows]
        self.assertEqual(snapshot_kinds.count("reasoning"), 1)
        self.assertEqual(snapshot_kinds.count("text"), 1)

    def test_non_streaming_model_opens_row_with_full_text(self):
        """非流式调用（无 stream 事件）：model_end 开行即全量（必丢路径兜底）。"""
        frames = self._frames([_model_end("一次性全量回复", run_id="r9")])
        rows = [f for f in frames if f.get("type") == "row"]
        self.assertEqual(len(rows), 2)  # open + end
        self.assertEqual(rows[0]["op"], "open")
        self.assertEqual(rows[0]["kind"], "text")
        self.assertEqual(rows[1]["op"], "end")
        self.assertEqual(rows[1]["text"], "一次性全量回复")

    def test_tool_row_lifecycle(self):
        frames = self._frames([
            _tools_start([{"name": "list_elements", "id": "call-1", "args": {}}]),
            _tool_end("list_elements", "call-1", "[meta_system.md]"),
        ])
        rows = [f for f in frames if f.get("type") == "row"]
        opens = [f for f in rows if f["op"] == "open"]
        ends = [f for f in rows if f["op"] == "end"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["kind"], "tool")
        self.assertEqual(opens[0]["tool"], "list_elements")
        self.assertEqual(ends[0]["state"], "complete")
        # 快照含工具行 + 工具名
        tool_rows = [r for r in self.sink.rows if r["kind"] == "tool"]
        self.assertEqual(tool_rows[0]["toolName"], "list_elements")
        self.assertEqual(tool_rows[0]["status"], "complete")

    def test_tool_error_row_failed_state(self):
        frames = self._frames([
            _tools_start([{"name": "read_source", "id": "call-2", "args": {}}]),
            _tool_error("read_source", "call-2", "boom"),
        ])
        ends = [f for f in frames if f.get("type") == "row" and f["op"] == "end"]
        self.assertEqual(ends[0]["state"], "failed")

    def test_tool_without_call_id_falls_back_by_name(self):
        frames = self._frames([
            _tools_start([{"name": "read_source", "id": "", "args": {}}]),
            _tool_end("read_source", "", "ok"),
        ])
        ends = [f for f in frames if f.get("type") == "row" and f["op"] == "end"]
        self.assertEqual(len(ends), 1)  # 按名回退命中，不留 streaming 行

    def test_snapshot_rows_are_camelcase_frontend_shape(self):
        self._frames([
            _stream_chunk(content="hi", run_id="r1"),
            _model_end("hi", run_id="r1"),
        ])
        row = self.sink.rows[0]
        self.assertIn("startedAt", row)
        self.assertIn("status", row)
        self.assertEqual(row["text"], "hi")
        self.assertIn("endedAt", row)

    def test_model_output_frame_still_emitted_for_persistence(self):
        """轮次帧（model_output/tool_call/tool_output）照旧产出——消息落库依赖。"""
        frames = self._frames([
            _model_end("回复", run_id="r1"),
            _tools_start([{"name": "t", "id": "c1", "args": {}}]),
            _tool_end("t", "c1", "out"),
        ])
        types_out = {f["type"] for f in frames}
        self.assertIn("model_output", types_out)
        self.assertIn("tool_call", types_out)
        self.assertIn("tool_output", types_out)


# ── _run_agent_streamed 集成（假 agent → 落库 + hub + rows_json）──


class RunAgentStreamedIntegrationTest(unittest.TestCase):
    """AC-003/AC-005 服务端部分：事件序列 → 消息落库 + hub 帧 + 行快照挂载。"""

    def setUp(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_messages WHERE session_id LIKE 's-int-%'")

    def test_full_round_persists_messages_and_broadcasts(self):
        import app.evolve.agent.agent as agent_mod
        from app.evolve.ctx import EvolveContext
        from app.evolve.evolve_repo import EvolveMessagesRepo
        from app.evolve.stream_hub import get_stream_hub

        events = [
            _stream_chunk(reasoning="思考A", run_id="r1"),
            _stream_chunk(content="我来看看", run_id="r1"),
            _model_end("我来看看", tool_calls=[
                {"name": "propose_evolution_point", "args": {}, "id": "call-1"},
            ], run_id="r1"),
            _tools_start([{
                "name": "propose_evolution_point", "id": "call-1",
                "args": {"target": "要素", "problem": "问题"},
            }]),
            _tool_end("propose_evolution_point", "call-1",
                      "id=deadbeef01 提案已记录"),
            _model_end("结论回复", run_id="r2"),
        ]
        agent = _FakeAgent(events)
        ctx = EvolveContext("s-int-1")
        ctx.recorder = None  # 无 recorder：run_meta 通道静默，hub 照常广播

        async def scenario():
            hub = get_stream_hub()
            q = hub.subscribe("s-int-1")
            await agent_mod._run_agent_streamed(
                agent, "用户问题", {"configurable": {"thread_id": "s-int-1"}}, ctx,
            )
            await asyncio.sleep(0)  # 排空 fanout
            received: list[dict] = []
            while not q.empty():
                received.append(q.get_nowait())
            hub.unsubscribe("s-int-1", q)
            return received

        received = asyncio.run(scenario())

        # 消息落库：首段文本带 tool_calls 不落；propose 落 tool 消息；末段纯文本落 assistant
        msgs = EvolveMessagesRepo.list_by_session("s-int-1")
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["tool", "assistant"])
        tool_msg = msgs[0]
        self.assertEqual(tool_msg["related_points"], ["deadbeef01"])

        # hub 帧：行帧（open/delta/end）+ message_updated
        row_frames = [f for f in received if f.get("type") == "row"]
        self.assertGreater(len(row_frames), 0)
        msg_updates = [f for f in received if f.get("type") == "message_updated"]
        self.assertEqual(len(msg_updates), 2)  # tool + assistant 落库各一

        # 行快照挂到最后一条 assistant 消息（含 reasoning/text/tool 行）
        last_assistant = msgs[-1]
        self.assertIsNotNone(last_assistant.get("rows"))
        snap_kinds = [r["kind"] for r in last_assistant["rows"]]
        self.assertIn("reasoning", snap_kinds)
        self.assertIn("text", snap_kinds)
        self.assertIn("tool", snap_kinds)

    def test_no_assistant_message_no_rows_attached(self):
        """取消/异常路径（无 assistant 消息）：不写行快照，不抛错。"""
        import app.evolve.agent.agent as agent_mod
        from app.evolve.ctx import EvolveContext
        from app.evolve.evolve_repo import EvolveMessagesRepo

        # 只有 tool_call 意图的 model_output：不落 assistant 消息
        events = [
            _model_end("", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
        ]
        ctx = EvolveContext("s-int-2")
        ctx.recorder = None
        asyncio.run(agent_mod._run_agent_streamed(
            _FakeAgent(events), "q", {"configurable": {}}, ctx,
        ))
        self.assertEqual(EvolveMessagesRepo.list_by_session("s-int-2"), [])


# ── SSE 端点（ASGI 驱动，模式同 test_events_stream.py）──────────


def _build_stream_app():
    from fastapi import FastAPI
    from app.evolve.api import router as evolve_router

    mini = FastAPI()
    mini.include_router(evolve_router, prefix="/api")
    return mini


class _StopStreaming(Exception):
    pass


async def _drive_sse(path: str, *, frames_to_collect: int,
                     on_headers_sent=None):
    app = _build_stream_app()
    start: dict = {}
    chunks: list[bytes] = []
    receive_calls = 0

    async def receive() -> dict:
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

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
        "raw_path": path_only.encode(),
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
    frames = [f for f in text.split("\n\n") if f.strip()]
    return start.get("status", 0), headers, frames


def _parse_data(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    return {}


class StreamEndpointTest(unittest.TestCase):
    """GET /evolve/sessions/{id}/stream（AC-003 服务端部分）。"""

    def setUp(self):
        from app.evolve import db as ev_db
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_messages WHERE session_id LIKE 's-sse-%'")
        ev_db.create_session("s-sse-1", status="conversing")

    def test_404_for_unknown_session(self):
        from fastapi.testclient import TestClient
        client = TestClient(_build_stream_app())
        r = client.get("/api/evolve/sessions/ghost/stream")
        self.assertEqual(r.status_code, 404)

    def test_snapshot_then_live_push(self):
        from app.evolve.stream_hub import get_stream_hub

        def _push_after_subscribe() -> None:
            get_stream_hub().publish("s-sse-1", {"type": "round_start", "round": "run_converse_round"})

        status, headers, frames = asyncio.run(_drive_sse(
            "/api/evolve/sessions/s-sse-1/stream",
            frames_to_collect=2,
            on_headers_sent=_push_after_subscribe,
        ))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "text/event-stream; charset=utf-8")
        first = _parse_data(frames[0])
        self.assertEqual(first["type"], "snapshot")
        self.assertEqual(first["session_status"], "conversing")
        # round_active：无未完成轮次（FR-006 断线复位信号）
        self.assertFalse(first["round_active"])
        second = _parse_data(frames[1])
        self.assertEqual(second["type"], "round_start")

    def test_snapshot_round_active_true_when_task_registered(self):
        """round 进行中连接：snapshot.round_active=true（断线重连复位输入锁的依据）。"""
        from app.evolve import api as evolve_api

        class _FakeTask:
            def done(self):
                return False

        evolve_api._running_tasks["s-sse-1"] = _FakeTask()
        try:
            _, _, frames = asyncio.run(_drive_sse(
                "/api/evolve/sessions/s-sse-1/stream", frames_to_collect=1,
            ))
            first = _parse_data(frames[0])
            self.assertTrue(first["round_active"])
        finally:
            evolve_api._running_tasks.pop("s-sse-1", None)

    def test_heartbeat_when_idle(self):
        import app.evolve.api as api_mod
        original = api_mod._STREAM_HEARTBEAT_SECONDS
        api_mod._STREAM_HEARTBEAT_SECONDS = 0.2
        try:
            _, _, frames = asyncio.run(_drive_sse(
                "/api/evolve/sessions/s-sse-1/stream", frames_to_collect=2,
            ))
        finally:
            api_mod._STREAM_HEARTBEAT_SECONDS = original
        self.assertEqual(len(frames), 2)
        # 首帧固定是 snapshot（连接建立即发），其后空闲只发心跳
        self.assertEqual(_parse_data(frames[0])["type"], "snapshot")
        self.assertEqual(_parse_data(frames[1])["type"], "heartbeat")


class StopSemanticsTest(unittest.TestCase):
    """FR-005（REQ-20261019-183921）：停止后可继续对话——停止语义按轮次类型分流。"""

    def setUp(self):
        from app.evolve import db as ev_db
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_messages WHERE session_id LIKE 's-stop-%'")
        self.recorder_stub = mock.Mock()
        self.recorder_stub.get_trace_id_by_session.return_value = None

    def _mk(self, sid: str, status: str):
        from app.evolve import db as ev_db
        ev_db.create_session(sid, status=status)

    def test_stop_conversing_round_restores_conversing(self):
        """对话轮停止：会话恢复 conversing（可继续输入）+ 广播 round_end。"""
        from app.evolve import api as evolve_api
        from app.evolve import db as ev_db
        from app.evolve.stream_hub import get_stream_hub

        self._mk("s-stop-1", "conversing")

        async def scenario():
            hub = get_stream_hub()
            q = hub.subscribe("s-stop-1")
            # 模拟一个「进行中」的轮次 task：cancel 后立即结束
            async def _round():
                await asyncio.sleep(60)
            task = asyncio.create_task(_round())
            evolve_api._running_tasks["s-stop-1"] = task
            try:
                await evolve_api._converge_evolve_cancel(
                    "s-stop-1", task, pre_status="conversing",
                )
            finally:
                evolve_api._running_tasks.pop("s-stop-1", None)
            await asyncio.sleep(0)
            received = []
            while not q.empty():
                received.append(q.get_nowait())
            hub.unsubscribe("s-stop-1", q)
            return received

        with mock.patch.object(__import__(
            "app.evolve.api", fromlist=["get_recorder"]), "get_recorder",
            return_value=self.recorder_stub,
        ):
            received = asyncio.run(scenario())

        sess_status = ev_db.get_session("s-stop-1")["status"]
        self.assertEqual(sess_status, "conversing")  # 回 conversing：可继续对话
        end_frames = [f for f in received if f.get("type") == "round_end"]
        self.assertEqual(len(end_frames), 1)
        self.assertEqual(end_frames[0]["status"], "cancelled")  # 轮次被取消

    def test_stop_finalizing_round_lands_cancelled(self):
        """落地轮停止：流程不可续，落 cancelled 终态。"""
        from app.evolve import api as evolve_api
        from app.evolve import db as ev_db

        self._mk("s-stop-2", "finalizing")

        async def scenario():
            async def _round():
                await asyncio.sleep(60)
            task = asyncio.create_task(_round())
            evolve_api._running_tasks["s-stop-2"] = task
            try:
                await evolve_api._converge_evolve_cancel(
                    "s-stop-2", task, pre_status="finalizing",
                )
            finally:
                evolve_api._running_tasks.pop("s-stop-2", None)

        with mock.patch.object(__import__(
            "app.evolve.api", fromlist=["get_recorder"]), "get_recorder",
            return_value=self.recorder_stub,
        ):
            asyncio.run(scenario())

        self.assertEqual(ev_db.get_session("s-stop-2")["status"], "cancelled")

    def test_stop_idempotent_when_round_already_finished(self):
        """停止请求与收敛之间轮次自然完成并推进状态：尊重之不覆盖（幂等）。"""
        from app.evolve import api as evolve_api
        from app.evolve import db as ev_db

        self._mk("s-stop-3", "finalizing")

        async def scenario():
            # task 已 done（finalize 刚完成并把状态推进到 pending_review）
            async def _round():
                return None
            task = asyncio.create_task(_round())
            await task
            # 轮次收尾把状态推进到 pending_review（竞态：停止晚了一步）
            ev_db.update_session("s-stop-3", status="pending_review")
            evolve_api._running_tasks["s-stop-3"] = task
            try:
                await evolve_api._converge_evolve_cancel(
                    "s-stop-3", task, pre_status="finalizing",
                )
            finally:
                evolve_api._running_tasks.pop("s-stop-3", None)

        with mock.patch.object(__import__(
            "app.evolve.api", fromlist=["get_recorder"]), "get_recorder",
            return_value=self.recorder_stub,
        ):
            asyncio.run(scenario())

        # 不覆盖为 cancelled——尊重轮次自然推进的终态
        self.assertEqual(ev_db.get_session("s-stop-3")["status"], "pending_review")




if __name__ == "__main__":
    unittest.main()
