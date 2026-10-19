"""会话级连续与中断恢复集成测试（REQ-20261019-222403，AC-001~007 服务端部分）。

真实链路：MetaAgentService.generate_stream + 真 langgraph 图（echo 节点）+
真 AsyncSqliteSaver checkpoint——只有 run_agent_stream 与外部 IO 接缝被替换。
锁定：
  - AC-001 粘性：同一客户端 thread 多轮复用同一 langgraph 线程，历史跨轮累积；
  - AC-002/003 断连可续：断连 → trace interrupted + 僵尸清理 + 半截留档；
    重试原话重发后历史无重复用户消息、无悬空 tool_calls、半截不进上下文；
  - AC-004 半截内容：日志 status=interrupted 且携带已生成部分；
  - AC-005 台账完整回放（含中断轮）+ GET 会话端点；
  - AC-006 排队串行：第二轮排队、第一轮结束后才开始；
  - AC-007 超预算截断：最老轮次裁出 LLM 上下文，日志不删；
  - DEC-010 僵尸 active_traces 惰性清理；FR-006 停止清空队列。

跑法（在 executor 目录）：
    python -m pytest tests/test_conversation_continuity.py -v
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app.domains.writing.agent import MetaAgentService, _sse
from app.domains.writing.conversation_log import ConversationLog
from app.platform.streaming import StreamResult
from app.platform.trace.recorder import TraceRecorder
from app.schemas.screenplay import (
    ScreenplayGenerateRequest,
    ThreadSummary,
)


def _make_thread(workspace_path: str, thread_id: str = "conv-thread") -> ThreadSummary:
    now = "2026-10-19T00:00:00+00:00"
    return ThreadSummary(
        thread_id=thread_id,
        workspace_id="conv-ws",
        session_name="conv-test",
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
    )


def _payload(thread_id: str, prompt: str, **extra) -> ScreenplayGenerateRequest:
    return ScreenplayGenerateRequest(thread_id=thread_id, prompt=prompt, **extra)


def _build_echo_agent(checkpointer):
    """真 langgraph 图：追加一条 echo AIMessage（真 checkpoint 持久化）。"""
    graph = StateGraph(MessagesState)

    def reply(state: dict) -> dict:
        last = state["messages"][-1]
        text = last.content if isinstance(last.content, str) else str(last.content)
        return {"messages": [AIMessage(content=f"echo:{text[-30:]}", id=f"a-{id(state):x}"[:16])]}

    graph.add_node("reply", reply)
    graph.add_edge(START, "reply")
    graph.add_edge("reply", END)
    return graph.compile(checkpointer=checkpointer)


async def _collect(gen) -> list[str]:
    frames = []
    async for frame in gen:
        frames.append(frame)
    return frames


async def _collect_into(gen, out: list[str]) -> list[str]:
    async for frame in gen:
        out.append(frame)
    return out


async def _wait_started(frames: list[str], timeout: float = 5.0) -> None:
    """确定性等待该 run 已开跑（status started 帧 ⇒ 会话门已持有）。

    修测试竞态：固定 sleep 不保证首个 run 已完成 acquire，后到请求可能
    反而先拿到锁（全套件负载下偶发），排队断言随之失真。
    """
    import time as _time

    deadline = _time.perf_counter() + timeout
    while _time.perf_counter() < deadline:
        if any(f.startswith("event: status") and '"started"' in f for f in frames):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("run 未在超时内开跑（会话门未持有，测试前置不成立）")


def _frame_status(frame: str) -> str | None:
    if not frame.startswith("event: status"):
        return None
    for line in frame.split("\n"):
        if line.startswith("data: "):
            import json
            return json.loads(line[6:]).get("status")
    return None


class _Harness:
    """每个用例一套：tmp 工作区 + 真 saver + 真 service + 可编程 fake 流。"""

    def __init__(self, *, budget: int = 96_000):
        self.tmp = tempfile.TemporaryDirectory(prefix="conv_ws_")
        self.ws = Path(self.tmp.name)
        self.thread = _make_thread(str(self.tmp.name))
        self.recorder = TraceRecorder()
        from types import SimpleNamespace as NS

        self.service = MetaAgentService(
            settings=NS(writer_agent_mode="live", conversation_history_char_budget=budget),
            workspace_root=self.ws,
            trace_recorder=self.recorder,
            style_store=None,
            checkpointer=None,
        )
        self.saver = None
        self.agent = None
        self.stream_impls: list = []  # run_agent_stream 逐次消费

    async def __aenter__(self):
        cm = AsyncSqliteSaver.from_conn_string(str(self.ws / "ckpt.db"))
        self._saver_cm = cm
        self.saver = await cm.__aenter__()
        await self.saver.setup()
        self.agent = _build_echo_agent(self.saver)
        return self

    async def __aexit__(self, *exc):
        try:
            await self._saver_cm.__aexit__(None, None, None)
        except Exception:
            pass
        return False

    def patch_all(self, es):
        """把全部接缝 patch 注册进 ExitStack。"""
        async def resolve_cp(owner_id):
            return self.saver

        async def fake_run_agent_stream(agent, agent_input, config, **kwargs):
            impl = self.stream_impls.pop(0)
            return await impl(agent, agent_input, config, **kwargs)

        es.enter_context(patch.object(self.service, "_resolve_checkpointer", side_effect=resolve_cp))
        es.enter_context(patch.object(self.service, "_issue_run_binding", return_value=(None, None)))
        es.enter_context(patch.object(self.service, "_resolve_model", return_value=object()))
        es.enter_context(patch.object(self.service, "_agent_for_workspace", return_value=self.agent))
        es.enter_context(patch(
            "app.domains.writing.agent.run_agent_stream", side_effect=fake_run_agent_stream,
        ))

    # ── 可编程 fake 流 ──────────────────────────────────────────────────

    def use_completing_stream(self, sse_text: str = "正文完成"):
        """真实 ainvoke 持久化 + 一条增量帧 + 正常 result。"""
        async def impl(agent, agent_input, config, **kw):
            async def gen():
                yield _sse("model_stream", {"content": sse_text})
            full = await agent.ainvoke(agent_input, config)
            return gen(), StreamResult(full_result=full)
        self.stream_impls.append(impl)

    def use_hanging_stream(self, partial_text: str, release: asyncio.Event,
                           dangling: bool = True, invoke: bool = False):
        """挂起模拟长生成。

        dangling=True 先写悬空尾段（user + 未闭合 tool_calls assistant）；
        invoke=True 同时真跑图持久化（释放后能正常收尾，排队用例用）。
        """
        async def impl(agent, agent_input, config, **kw):
            if dangling:
                prompt = agent_input["messages"][0]["content"]
                await agent.aupdate_state(config, {"messages": [
                    HumanMessage(content=prompt, id="u-dangle"),
                    AIMessage(content="", id="ai-dangle",
                              tool_calls=[{"name": "write_file", "args": {}, "id": "tc-1",
                                           "type": "tool_call"}]),
                ]})
            full = await agent.ainvoke(agent_input, config) if invoke else None
            async def gen():
                yield _sse("model_stream", {"content": partial_text})
                await release.wait()
            return gen(), StreamResult(full_result=full)
        self.stream_impls.append(impl)

    async def run_stream(self, payload) -> list[str]:
        return await _collect(
            self.service.generate_stream(payload, self.thread, owner_id="tester")
        )

    async def state_messages(self, thread_key: str):
        snap = await self.agent.aget_state({"configurable": {"thread_id": thread_key}})
        return list((snap.values or {}).get("messages") or [])

    def cleanup(self):
        self.tmp.cleanup()


@patch("app.platform.trace.recorder._notify_evolution")
class ConversationContinuityTest(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "h", None) and getattr(self.h, "tmp", None):
            self.h.cleanup()

    def _harness(self, **kw):
        h = _Harness(**kw)
        self.h = h
        return h

    # ── AC-001：会话粘性 + 历史跨轮累积 ────────────────────────────────

    def test_sticky_thread_accumulates_history(self, _n):
        from contextlib import ExitStack

        async def scenario():
            async with self._harness() as h:
                h.use_completing_stream("第一轮完成")
                h.use_completing_stream("第二轮完成")
                with ExitStack() as es:
                    h.patch_all(es)
                    frames1 = await h.run_stream(_payload(h.thread.thread_id, "第一轮：聊聊题材"))
                    frames2 = await h.run_stream(_payload(h.thread.thread_id, "第二轮：换个爽点"))
                # aiosqlite 连接绑定本事件循环：状态断言收在本协程内完成。
                key = f"{h.thread.thread_id}:author:1"
                msgs = await h.state_messages(key)
                snap2 = await h.agent.aget_state(
                    {"configurable": {"thread_id": f"{h.thread.thread_id}:author:2"}}
                )
                forked_empty = not (snap2.values or {}).get("messages")
                return h, frames1, frames2, msgs, forked_empty

        h, frames1, frames2, msgs, forked_empty = asyncio.run(scenario())
        assert any(f.startswith("event: final") for f in frames1)
        assert any(f.startswith("event: final") for f in frames2)
        users = [m for m in msgs if isinstance(m, HumanMessage)]
        assert len(users) == 2, f"两轮用户消息应都在同一粘性线程: {len(users)}"
        assert any("第一轮" in m.content for m in users)
        assert any("第二轮" in m.content for m in users)
        assert forked_empty, "不应派生 :2 新线程"
        h.cleanup()

    # ── AC-002/003/004：断连 → interrupted；重试原话重发无重复 ──────────

    def test_disconnect_marks_interrupted_and_retry_dedupes(self, _n):
        from contextlib import ExitStack

        async def scenario():
            async with self._harness() as h:
                release = asyncio.Event()
                h.use_hanging_stream("写了一半的正文", release)
                h.use_completing_stream("重试完成")
                with ExitStack() as es:
                    h.patch_all(es)
                    # 第一轮：消费到挂起点后取消（模拟 SSE 断连）
                    gen = h.service.generate_stream(
                        _payload(h.thread.thread_id, "写第三章"), h.thread, owner_id="tester",
                    )
                    consumer = asyncio.create_task(_collect(gen))
                    await asyncio.sleep(0.2)
                    assert not consumer.done()
                    consumer.cancel()
                    try:
                        await consumer
                    except asyncio.CancelledError:
                        pass
                    key = f"{h.thread.thread_id}:author:1"
                    msgs_after_break = await h.state_messages(key)
                    dangling_kept = any(getattr(m, "id", "") == "ai-dangle" for m in msgs_after_break)
                    # 重试：同话术重发（FR-003 原话接续重发）
                    frames2 = await h.run_stream(_payload(h.thread.thread_id, "写第三章"))
                    msgs = await h.state_messages(key)
                dangling_left = [getattr(m, "id", "") for m in msgs]
                user_texts = [m.content for m in msgs if isinstance(m, HumanMessage)]
                all_text = [str(getattr(m, "content", "")) for m in msgs]
                return h, frames2, dangling_kept, dangling_left, user_texts, all_text

        h, frames2, dangling_kept, dangling_left, user_texts, all_text = asyncio.run(scenario())
        assert dangling_kept, "断连当下悬空尾段仍在（清理发生在下一轮开跑前）"
        assert any(f.startswith("event: final") for f in frames2)
        # 断尾清理生效：悬空 assistant 没了、重试用户消息恰好一条、半截不进上下文
        assert "ai-dangle" not in dangling_left, "重试后悬空 tool_calls 链应被清除"
        assert sum(1 for t in user_texts if "写第三章" in t) == 1, \
            f"重试后用户消息应恰好一条: {len(user_texts)}"
        assert not any("写了一半" in t for t in all_text), \
            "半截内容不得进入 LLM 上下文（DEC-004）"
        # 台账：user + assistant(interrupted 携半截) + user + assistant(completed)
        log = ConversationLog(h.ws)
        entries = log.read(h.thread.thread_id)
        roles = [(m.role, m.status) for m in entries]
        assert ("assistant", "interrupted") in roles
        interrupted_entry = next(m for m in entries if m.status == "interrupted")
        assert interrupted_entry.content == "写了一半的正文"  # AC-004 半截留档
        assert ("assistant", "completed") in roles
        h.cleanup()

    def test_disconnect_trace_status_and_zombie_cleanup(self, _n):
        from contextlib import ExitStack
        from contracts.writing_artifacts import load_writing_state

        async def scenario():
            async with self._harness() as h:
                release = asyncio.Event()
                h.use_hanging_stream("半截", release)
                with ExitStack() as es:
                    h.patch_all(es)
                    gen = h.service.generate_stream(
                        _payload(h.thread.thread_id, "x"), h.thread, owner_id="tester",
                    )
                    consumer = asyncio.create_task(_collect(gen))
                    await asyncio.sleep(0.2)
                    consumer.cancel()
                    try:
                        await consumer
                    except asyncio.CancelledError:
                        pass
                return h

        h = asyncio.run(scenario())
        runs = h.recorder.list_runs(h.thread)
        assert runs, "至少一条 trace"
        assert runs[0].status == "interrupted", f"断连 trace 应为 interrupted: {runs[0].status}"
        state = load_writing_state(h.ws)
        assert "author" not in state.active_traces, "断连后 active_traces 僵尸应被清理"
        h.cleanup()

    # ── AC-006：排队串行 ────────────────────────────────────────────────

    def test_queue_serializes_same_conversation(self, _n):
        from contextlib import ExitStack

        async def scenario():
            async with self._harness() as h:
                release = asyncio.Event()
                h.use_hanging_stream("第一轮正文", release, dangling=False, invoke=True)
                h.use_completing_stream("第二轮正文")
                with ExitStack() as es:
                    h.patch_all(es)
                    frames1: list[str] = []
                    gen1 = h.service.generate_stream(
                        _payload(h.thread.thread_id, "第一轮"), h.thread, owner_id="tester",
                    )
                    t1 = asyncio.create_task(_collect_into(gen1, frames1))
                    await _wait_started(frames1)  # 确定性：run1 已持门再提交第二条
                    gen2 = h.service.generate_stream(
                        _payload(h.thread.thread_id, "第二轮"), h.thread, owner_id="tester",
                    )
                    t2 = asyncio.create_task(_collect(gen2))
                    await asyncio.sleep(0.3)
                    assert not t2.done(), "第二轮应排队等待"
                    release.set()
                    await t1
                    frames2 = await t2
                return h, frames1, frames2

        h, frames1, frames2 = asyncio.run(scenario())
        statuses2 = [s for s in (_frame_status(f) for f in frames2) if s]
        assert "queued" in statuses2, f"第二轮应收到排队状态: {statuses2}"
        assert statuses2.index("queued") < statuses2.index("started")
        assert any(f.startswith("event: final") for f in frames1)
        assert any(f.startswith("event: final") for f in frames2)
        # 台账顺序：第二轮的助手记录在第一轮之后开
        entries = ConversationLog(h.ws).read(h.thread.thread_id)
        assistants = [m for m in entries if m.role == "assistant"]
        assert [m.status for m in assistants] == ["completed", "completed"]
        h.cleanup()

    # ── AC-007：超预算截断 ──────────────────────────────────────────────

    def test_history_budget_trims_oldest_turns(self, _n):
        from contextlib import ExitStack

        async def scenario():
            async with self._harness(budget=700) as h:
                # 直接经真图播种 5 轮长历史（绕过 service，不建 trace）
                key = f"{h.thread.thread_id}:author:1"
                for i in range(5):
                    await h.agent.ainvoke(
                        {"messages": [{"role": "user", "content": f"第{i}轮：" + "长" * 200}]},
                        {"configurable": {"thread_id": key}},
                    )
                seeded = await h.state_messages(key)
                h.use_completing_stream("新轮完成")
                with ExitStack() as es:
                    h.patch_all(es)
                    frames = await h.run_stream(_payload(h.thread.thread_id, "新的一轮"))
                msgs = await h.state_messages(key)
                seeded_count = len(seeded)
                final_texts = [str(getattr(m, "content", "")) for m in msgs]
                return h, frames, seeded_count, final_texts

        h, frames, seeded_count, final_texts = asyncio.run(scenario())
        assert any(
            f.startswith("event: status") and "history_truncated" in f for f in frames
        ), "应广播截断提示"
        assert len(final_texts) < seeded_count, "超预算后最老轮次应被裁出上下文"
        assert not any("第0轮" in t for t in final_texts), "最老轮先裁"
        assert any("新的一轮" in t for t in final_texts), "最新轮保留"
        # 台账不受截断影响（只有本轮一条记录，播种未经 service）
        entries = ConversationLog(h.ws).read(h.thread.thread_id)
        assert len(entries) == 2  # user + assistant(completed)
        h.cleanup()

    # ── DEC-010：僵尸 active_traces 惰性清理 ────────────────────────────

    def test_stale_active_traces_swept_on_new_run(self, _n):
        from contextlib import ExitStack
        from contracts.writing_artifacts import load_writing_state, save_writing_state

        async def scenario():
            async with self._harness() as h:
                state = load_writing_state(h.ws)
                state.active_traces["author"] = "trace-gone"
                save_writing_state(h.ws, state)
                h.use_completing_stream("ok")
                with ExitStack() as es:
                    h.patch_all(es)
                    await h.run_stream(_payload(h.thread.thread_id, "清僵尸"))
                return h

        h = asyncio.run(scenario())
        state = load_writing_state(h.ws)
        assert "author" not in state.active_traces
        h.cleanup()

    # ── AC-005：GET 会话端点回放 ────────────────────────────────────────

    def test_conversation_endpoint_replays_log(self, _n):
        from contextlib import ExitStack
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.auth import current_user
        from app.routers import threads as threads_router

        async def scenario():
            async with self._harness() as h:
                h.use_completing_stream("完成内容")
                with ExitStack() as es:
                    h.patch_all(es)
                    await h.run_stream(_payload(h.thread.thread_id, "端点回放测试"))
                return h

        h = asyncio.run(scenario())

        class _FakeStore:
            def get_thread(self, user_id, thread_id):
                return h.thread if thread_id == h.thread.thread_id else None

        app = FastAPI()
        app.include_router(threads_router.router)
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="tester")
        client = TestClient(app)
        with patch.object(threads_router, "get_thread_store", return_value=_FakeStore()):
            resp = client.get(f"/threads/{h.thread.thread_id}/conversation")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        roles = [(m["role"], m["status"]) for m in data]
        assert roles == [("user", "running"), ("assistant", "completed")]
        assert data[1]["content"] == "echo:" + "端点回放测试"[-30:] or data[1]["content"].startswith("echo:")
        assert data[1]["trace_id"]
        h.cleanup()

    # ── review R-002：停止清队后同 id 重发不得复活 ─────────────────────

    def test_stopped_message_not_revived_by_resend(self, _n):
        from contextlib import ExitStack

        async def scenario():
            async with self._harness() as h:
                # 先经台账制造一条被停止清队作废的消息
                conv_log = ConversationLog(h.ws)
                stopped_id = conv_log.admit_user(h.thread.thread_id, "被停掉的消息", message_id="u-stop")
                conv_log.mark_user(h.thread.thread_id, stopped_id, "cancelled")
                with ExitStack() as es:
                    h.patch_all(es)
                    frames = await h.run_stream(
                        _payload(h.thread.thread_id, "被停掉的消息", message_id="u-stop")
                    )
                runs = h.recorder.list_runs(h.thread)
                return h, frames, runs

        h, frames, runs = asyncio.run(scenario())
        statuses = [s for s in (_frame_status(f) for f in frames) if s]
        assert statuses == ["cancelled"], f"应只收到 cancelled 收口帧: {statuses}"
        assert not any(f.startswith("event: final") for f in frames)
        assert runs == [], "不得创建任何 trace（消息不复活）"
        # 台账状态仍 cancelled
        msgs = ConversationLog(h.ws).read(h.thread.thread_id)
        assert [m.status for m in msgs] == ["cancelled"]
        h.cleanup()

    # ── review R-003：排队跨多个心跳周期仍保 FIFO ──────────────────────

    def test_queue_fifo_preserved_across_heartbeats(self, _n):
        from contextlib import ExitStack
        from unittest.mock import patch as mock_patch

        from app.domains.writing import agent as writing_agent

        async def scenario():
            async with self._harness() as h:
                release1 = asyncio.Event()
                # 心跳周期压到 50ms：第一轮放行前跨多个心跳，验证 acquire 不被
                # 超时取消重排（wait_for 版本会在此乱序）
                with mock_patch.object(writing_agent, "_QUEUE_HEARTBEAT_S", 0.05):
                    with ExitStack() as es:
                        h.patch_all(es)
                        h.use_hanging_stream("第一轮", release1, dangling=False, invoke=True)
                        h.use_completing_stream("第二轮")
                        h.use_completing_stream("第三轮")
                        live1: list[str] = []
                        t1 = asyncio.create_task(_collect_into(h.service.generate_stream(
                            _payload(h.thread.thread_id, "第1轮请求"), h.thread, owner_id="tester",
                        ), live1))
                        await _wait_started(live1)  # 确定性：run1 先持门
                        t2 = asyncio.create_task(_collect(h.service.generate_stream(
                            _payload(h.thread.thread_id, "第2轮请求"), h.thread, owner_id="tester",
                        )))
                        t3 = asyncio.create_task(_collect(h.service.generate_stream(
                            _payload(h.thread.thread_id, "第3轮请求"), h.thread, owner_id="tester",
                        )))
                        # 完成顺序记录：FIFO 下必须与提交顺序一致
                        completion_order: list[int] = []

                        async def _record(index: int, task):
                            await task
                            completion_order.append(index)

                        watchers = [
                            asyncio.ensure_future(_record(1, t1)),
                            asyncio.ensure_future(_record(2, t2)),
                            asyncio.ensure_future(_record(3, t3)),
                        ]
                        await asyncio.sleep(0.4)  # > 多个心跳周期
                        assert not any(t.done() for t in [t1, t2, t3])
                        release1.set()
                        frames = list(await asyncio.gather(*watchers))
                        frames = [await t for t in [t1, t2, t3]]
                entries = ConversationLog(h.ws).read(h.thread.thread_id)
                assistants = [m for m in entries if m.role == "assistant"]
                return h, frames, completion_order, assistants

        h, frames, completion_order, assistants = asyncio.run(scenario())
        for stream_frames in frames:
            assert any(f.startswith("event: final") for f in stream_frames)
        # 完成顺序 = 提交顺序（FIFO 未被心跳重排；串行门保证一轮一跑）
        assert len(assistants) == 3
        assert completion_order == [1, 2, 3], f"排队乱序: {completion_order}"
        # 台账开壳顺序同样 = 提交顺序
        order = [m.trace_id for m in assistants]
        assert len(set(order)) == 3
        h.cleanup()

    # ── review R2-P1：授予先于取消观察 → 锁必须被归还 ──────────────────

    def test_queue_cancel_after_grant_returns_lock(self, _n):
        from contextlib import ExitStack
        from unittest.mock import patch as mock_patch

        from app.domains.writing import agent as writing_agent
        from app.domains.writing.run_gate import get_conversation_gate

        async def scenario():
            async with self._harness() as h:
                release1 = asyncio.Event()
                with mock_patch.object(writing_agent, "_QUEUE_HEARTBEAT_S", 0.05):
                    with ExitStack() as es:
                        h.patch_all(es)
                        h.use_hanging_stream("第一轮", release1, dangling=False, invoke=True)
                        h.use_completing_stream("第二轮")
                        live1: list[str] = []
                        t1 = asyncio.create_task(_collect_into(h.service.generate_stream(
                            _payload(h.thread.thread_id, "第1轮"), h.thread, owner_id="tester",
                        ), live1))
                        await _wait_started(live1)
                        # 慢消费者：只拉两帧（queued 状态 + ping）就停手——
                        # 生成器悬在 ping yield 上，看不见 acquire 的授予
                        gen2 = h.service.generate_stream(
                            _payload(h.thread.thread_id, "第2轮"), h.thread, owner_id="tester",
                        )
                        it2 = gen2.__aiter__()
                        f1 = await it2.__anext__()  # status queued
                        assert "queued" in f1
                        f2 = await it2.__anext__()  # ping
                        assert f2.startswith(":")
                        gate = get_conversation_gate(h.thread.thread_id)
                        # 放行第一轮 → 锁转移到第2轮的 acquire_task（生成器仍悬着，
                        # 看不见授予）
                        release1.set()
                        await t1
                        assert gate._lock.locked(), "锁已被第2轮 acquire 持有（未观察）"
                        # 取消慢消费者：finally 的 done-callback 兜底归还授予的锁
                        t2 = asyncio.ensure_future(it2.__anext__())
                        t2.cancel()
                        try:
                            await t2
                        except asyncio.CancelledError:
                            pass
                        deadline = asyncio.get_event_loop().time() + 3.0
                        while gate._lock.locked() and asyncio.get_event_loop().time() < deadline:
                            await asyncio.sleep(0.02)
                        lock_state = gate._lock.locked()
                        # 第三轮：必须能立刻拿到锁并完成（不死锁）
                        h.use_completing_stream("第三轮")
                        t3 = asyncio.create_task(_collect(h.service.generate_stream(
                            _payload(h.thread.thread_id, "第3轮"), h.thread, owner_id="tester",
                        )))
                        frames3 = await asyncio.wait_for(t3, timeout=3.0)
                entries = ConversationLog(h.ws).read(h.thread.thread_id)
                return h, lock_state, frames3, entries

        h, lock_state, frames3, entries = asyncio.run(scenario())
        assert lock_state is False, "授予归还前锁应已释放（done-callback）"
        assert any(f.startswith("event: final") for f in frames3), "第三轮不死锁、正常完成"
        roles = [(m.role, m.status) for m in entries]
        assert ("user", "paused") in roles, "被取消的排队消息应落 paused"
        h.cleanup()
