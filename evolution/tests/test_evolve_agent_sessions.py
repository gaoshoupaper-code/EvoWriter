"""Agent 会话模型 + 并发锁单测（REQ-20261001-131018 / FR-002/003/004/009，AC-002/003/004）。

隔离策略（同 test_evolve_smoke）：临时 SQLite DB + TestClient；executor 与
recorder 均桩掉——本文件只测会话生命周期控制流，不跑 LLM round。

覆盖：
  - 旧会话（agent_id NULL）发消息/拍板被拒（403 只读，AC-002）
  - 作品删除后拒绝开新会话；归档 Agent 拒绝开新会话（FR-009）
  - 开场注入三路内容（概览/进化点/发布摘要）且不含旧聊天原文（AC-004）
  - 发布摘要含全局发版流水（Platform 账本）+ 账本不可达降级
  - 概览拉取失败降级（FR-004 失败语义）
  - conversing 多会话可并存；落地通道互斥 + 占用提示（AC-003）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["EVOLUTION_DB"] = _tmp_db.name
os.environ["EXECUTOR_URL"] = "http://127.0.0.1:0"

from fastapi.testclient import TestClient

import app.core.db as db
import app.evolve.agents_api as agents_api
from app.core.settings import settings
from app.evolve import agents_repo, executor_client
from app.evolve import db as ev_db
from app.evolve.evolve_repo import EvolvePointsRepo
from app.main import app

_old_db = settings.evolution_db

WORK = {
    "workspace_id": "ws-u4",
    "title": "会话模型作品",
    "domain": "writing",
    "owner_user_id": "u1",
    "owner_username": "alice",
    "session_count": 5,
}


def setUpModule() -> None:
    settings.evolution_db = _tmp_db.name
    db._conn = None
    db.init_db()


def tearDownModule() -> None:
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    settings.evolution_db = _old_db
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass


def _ok_fetch(workspace_id: str) -> dict:
    if workspace_id == WORK["workspace_id"]:
        return dict(WORK)
    raise executor_client.WorkNotFoundError(workspace_id)


def _make_agent(name="U4Agent") -> dict:
    """直连 repo 建 Agent（绕开 HTTP，测试可控）。"""
    return agents_repo.create(name, WORK["workspace_id"])


def _force_status(session_id: str, status: str) -> None:
    ev_db.update_session(session_id, status=status)


class LegacySessionReadonlyTest(unittest.TestCase):
    """AC-002：旧无绑定会话只读。"""

    def setUp(self):
        self.client = TestClient(app)
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        ev_db.create_session("legacy-1", case_id="")  # agent_id=NULL
        _force_status("legacy-1", "conversing")

    def test_legacy_send_message_rejected_403(self):
        r = self.client.post(
            "/api/evolve/sessions/legacy-1/messages",
            json={"content": "还活着吗"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("只读", r.json()["detail"])

    def test_legacy_finalize_rejected_403(self):
        r = self.client.post("/api/evolve/sessions/legacy-1/finalize")
        self.assertEqual(r.status_code, 403)
        self.assertIn("只读", r.json()["detail"])


class AgentSessionStartTest(unittest.TestCase):
    """FR-004/009：Agent 下开会话与开场注入。"""

    def setUp(self):
        self.client = TestClient(app)
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_messages")
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")

    def _start(self, agent_id: str):
        # recorder 桩：不真跑 inspect round——会话创建后立即把后台 task 停掉
        # 的成本高；改为直接断言 503 守门前的会话已建？不行，503 时不建会话。
        # 这里直接走 503 之外的成功路径：mock get_recorder 返回 None 会 503。
        # 所以本类只测守门分支；成功路径的注入断言用 _assemble_work_context
        # + _build_evolve_ctx 直连测试（见 WorkContextInjectionTest）。
        return self.client.post(
            f"/api/evolve/agents/{agent_id}/sessions", json={})

    def test_work_deleted_rejects_new_session(self):
        agent = _make_agent()
        agents_repo.mark_work_deleted(agent["agent_id"])
        r = self._start(agent["agent_id"])
        self.assertEqual(r.status_code, 409)
        self.assertIn("已被删除", r.json()["detail"])

    def test_archived_agent_rejects_new_session(self):
        agent = _make_agent()
        agents_repo.archive(agent["agent_id"])
        r = self._start(agent["agent_id"])
        self.assertEqual(r.status_code, 409)
        self.assertIn("归档", r.json()["detail"])

    def test_probe_missing_marks_and_rejects(self):
        agent = _make_agent()
        with mock.patch.object(agents_api, "fetch_workspace",
                               side_effect=_ok_fetch):
            pass
        # 列表缓存未标记，但探测发现 404 → 补标记 + 拒绝
        def _gone(ws):
            raise executor_client.WorkNotFoundError(ws)
        with mock.patch.object(executor_client, "fetch_workspace", side_effect=_gone):
            r = self._start(agent["agent_id"])
        self.assertEqual(r.status_code, 409)
        self.assertTrue(agents_repo.get(agent["agent_id"])["work_deleted_at"])

    def test_missing_agent_404(self):
        r = self._start("ghost")
        self.assertEqual(r.status_code, 404)


class WorkContextInjectionTest(unittest.TestCase):
    """AC-004：开场注入三路内容，不含旧会话聊天原文。"""

    # Platform 账本桩数据（全局发版流水注入用）
    LEDGER = {
        "items": [
            {"version": 23, "commit": "c23" * 10, "note": "fix: 回执轮死锁修复",
             "created_at": "2026-10-02T09:44:10Z"},
            {"version": 22, "commit": "c22" * 10, "note": "进化 session abc 产出的改动",
             "created_at": "2026-10-02T07:44:29Z"},
        ],
        "production_version": 23,
    }

    def setUp(self):
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_messages")
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        self.agent = _make_agent("注入Agent")
        # 账本走桩——不真实连 Platform（测试环境不可达）
        patcher = mock.patch(
            "app.versioning.platform_ledger.fetch_ledger",
            return_value=dict(self.LEDGER),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_injection_contains_three_sections_and_no_chat_history(self):
        # 造数据：Agent 名下 3 个进化点、1 次发布、一段旧会话聊天（不应出现）
        ev_db.create_session("s1", case_id="", agent_id=self.agent["agent_id"])
        ev_db.create_session("s2", case_id="", agent_id=self.agent["agent_id"])
        _force_status("s1", "published")
        _force_status("s2", "conversing")
        point_ids = []
        for i in range(3):
            p = EvolvePointsRepo.propose(
                f"s{i % 2 + 1}", target=f"要素{i}", problem=f"问题{i}的根源",
                options=[{"description": "方案"}], agent_id=self.agent["agent_id"],
            )
            point_ids.append(p["id"])
        EvolvePointsRepo.accept(point_ids[1], chosen_option=0)
        EvolvePointsRepo.reject(point_ids[2])
        from app.evolve.evolve_repo import EvolveMessagesRepo
        EvolveMessagesRepo.append("s1", role="user", content="上次聊到的秘密暗号XYZZY")

        from app.evolve import api as evolve_api
        from app.evolve.agent.agent import _format_work_binding
        from app.evolve.ctx import EvolveContext

        with mock.patch.object(executor_client, "fetch_workspace",
                               side_effect=_ok_fetch):
            wc = evolve_api._assemble_work_context(self.agent)
        ev_db.create_session(
            "s3", case_id="", agent_id=self.agent["agent_id"], work_context=wc)
        ctx = evolve_api._build_evolve_ctx("s3")

        self.assertEqual(ctx.agent_id, self.agent["agent_id"])
        self.assertEqual(ctx.workspace_id, WORK["workspace_id"])
        binding = _format_work_binding(ctx)
        # 三路内容
        self.assertIn(WORK["title"], binding)              # 作品概览
        self.assertIn("进化点 3 个", binding)                # 进化点清单（含状态统计）
        # FR-002：清单行带全局序号 + 真实 id（跨会话引用契约），目标要素跟在 id 后
        self.assertRegex(binding, r"\[accepted\] #\d+（id=[0-9a-f]{32}）要素1")
        self.assertIn("本 Agent 名下发布 1 次", binding)       # 发布摘要（Agent 档案口径）
        # 全局发版流水（Platform 账本，harness 真实演进史）
        self.assertIn("全局发版流水", binding)
        self.assertIn("当前生产版本 v23", binding)
        self.assertIn("v23（2026-10-02）：fix: 回执轮死锁修复", binding)
        # 不含旧会话聊天原文
        self.assertNotIn("XYZZY", binding)

    def test_injection_degrades_when_overview_unreachable(self):
        from app.evolve import api as evolve_api
        from app.evolve.agent.agent import _format_work_binding
        from app.evolve.ctx import EvolveContext

        def _boom(ws):
            raise executor_client.ExecutorUnavailableError("down")
        with mock.patch.object(executor_client, "fetch_workspace", side_effect=_boom):
            wc = evolve_api._assemble_work_context(self.agent)
        self.assertEqual(wc["probe"], "unreachable")
        ev_db.create_session(
            "s4", case_id="", agent_id=self.agent["agent_id"], work_context=wc)
        ctx = evolve_api._build_evolve_ctx("s4")
        binding = _format_work_binding(ctx)
        self.assertIn("概览暂缺", binding)

    def test_injection_new_agent_not_misled_as_pristine(self):
        """新 Agent 名下无发布 ≠ harness 原始形态（线上实测曾误判「还是 v7 原始形态」）。"""
        from app.evolve import api as evolve_api
        from app.evolve.agent.agent import _format_work_binding

        # 名下零 session：直接造一个空 work_context 的当前会话
        ev_db.create_session("s5", case_id="", agent_id=self.agent["agent_id"])
        ctx = evolve_api._build_evolve_ctx("s5")
        binding = _format_work_binding(ctx)
        self.assertIn("本 Agent 名下发布：无", binding)
        self.assertIn("勿当成原始形态", binding)
        # 全局流水在场且带「勿重复提」提示
        self.assertIn("不要重复提", binding)

    def test_injection_degrades_when_ledger_unreachable(self):
        from app.evolve import api as evolve_api
        from app.evolve.agent.agent import _format_work_binding

        ev_db.create_session("s6", case_id="", agent_id=self.agent["agent_id"])
        ctx = evolve_api._build_evolve_ctx("s6")
        with mock.patch("app.versioning.platform_ledger.fetch_ledger",
                        side_effect=RuntimeError("Platform 账本不可达")):
            binding = _format_work_binding(ctx)
        self.assertIn("全局发版流水暂缺", binding)
        self.assertIn("Platform 账本不可达", binding)


class LandingChannelMutexTest(unittest.TestCase):
    """AC-003：conversing 并行 + 落地互斥。"""

    def setUp(self):
        self.client = TestClient(app)
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_messages")
            conn.execute("DELETE FROM evolve_points")
            conn.execute("DELETE FROM evolve_sessions")
            conn.execute("DELETE FROM evolve_agents")
        self.agent_a = _make_agent("AgentA")
        self.agent_b = agents_repo.create("AgentB", "ws-u4-b")

    def _mk_session(self, agent, sid, status):
        ev_db.create_session(sid, case_id="", agent_id=agent["agent_id"])
        _force_status(sid, status)

    def test_parallel_conversing_allowed_and_visible(self):
        self._mk_session(self.agent_a, "c1", "conversing")
        self._mk_session(self.agent_b, "c2", "conversing")
        # 两个 conversing 并存——落地通道不视为占用
        r = self.client.get("/api/evolve/landing-channel")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["occupied"])

    def test_finalize_blocked_while_channel_occupied(self):
        # S1 占用通道（finalizing）
        self._mk_session(self.agent_a, "f1", "finalizing")
        # S2（另一 Agent）想拍板 → 409 带占用方信息
        self._mk_session(self.agent_b, "c2", "conversing")
        from app.evolve.evolve_repo import EvolvePointsRepo
        EvolvePointsRepo.propose(
            "c2", target="x", problem="y", options=[{"description": "z"}],
            agent_id=self.agent_b["agent_id"])
        EvolvePointsRepo.accept(
            EvolvePointsRepo.list_by_agent(self.agent_b["agent_id"])[0]["id"],
            chosen_option=0)
        r = self.client.post("/api/evolve/sessions/c2/finalize")
        self.assertEqual(r.status_code, 409)
        detail = r.json()["detail"]
        self.assertIn("f1", detail["message"])
        self.assertEqual(detail["occupied_by_session_id"], "f1")
        self.assertEqual(detail["occupied_by_status"], "finalizing")

        # 通道端点可见占用方
        lc = self.client.get("/api/evolve/landing-channel").json()
        self.assertTrue(lc["occupied"])
        self.assertEqual(lc["occupier"]["session_id"], "f1")

    def test_channel_freed_after_terminate(self):
        self._mk_session(self.agent_a, "p1", "pending_review")
        _force_status("p1", "published")
        # 释放后通道空闲
        lc = self.client.get("/api/evolve/landing-channel").json()
        self.assertFalse(lc["occupied"])


if __name__ == "__main__":
    unittest.main()


class InspectOnDemandTest(unittest.TestCase):
    """FR-005 按需读取修正：开场指令不得引导预拉 trace/产物/评估。

    用户纠偏（2026-10-01）：一上来就读会话记录、还提评估报告（已废弃）——
    证据工具必须按需调用，开场白基于注入概览直接发。
    """

    def test_bound_inspect_input_no_prewarm_no_eval(self):
        from app.evolve.agent.agent import _build_inspect_user_input
        from app.evolve.ctx import EvolveContext

        ctx = EvolveContext("sess-od")
        ctx.agent_id = "agent-od"
        ctx.workspace_id = "ws-od"
        text = _build_inspect_user_input(ctx, "")
        # 按需纪律存在
        self.assertIn("按需", text)
        self.assertIn("不要在本阶段主动批量调用证据工具", text)
        # 不引导预拉
        self.assertNotIn("列出作品会话记录", text)
        self.assertNotIn("翻看绑定作品的证据", text)
        # 无废弃评估引用
        self.assertNotIn("评估报告", text)
        self.assertNotIn("评估 finding", text)

    def test_unbound_inspect_input_also_on_demand(self):
        from app.evolve.agent.agent import _build_inspect_user_input
        from app.evolve.ctx import EvolveContext

        ctx = EvolveContext("sess-od2")
        text = _build_inspect_user_input(ctx, "")
        self.assertIn("按需调用", text)
        self.assertNotIn("评估报告", text)
