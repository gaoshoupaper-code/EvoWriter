"""作品证据工具集单测（REQ-20261001-131018 / FR-005/006/007 / AC-005/006/007）。

隔离策略：临时 SQLite DB + ctx 直连（set_tool_context）调工具本体。
executor 部分桩掉（当前产物/刷新）；trace/版本史/历史会话用本地造数。

覆盖（AC-005/006/007）：
  - list_work_traces：20 条全量（分页）、harness 版本展示与过滤、数据截止时间、
    排除进化自观测 trace
  - read_work_trace：summary/skeleton/events 三层；跨作品拒绝
  - read_current_artifacts：executor 内容直通；作品删除 → 明确提示
  - list_artifact_revisions / read_artifact_revision：分组、过期标注、不可读正文
  - list_agent_sessions / read_agent_session：只看自己 Agent 的；别家拒绝
  - 刷新参数：触发 _scan_once（桩验证调用）
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

import app.core.db as db
from app.core.settings import settings
from app.evolve import agents_repo, executor_client
from app.evolve import db as ev_db
from app.evolve.ctx import EvolveContext, set_tool_context
from app.evolve.evolve_repo import EvolveMessagesRepo
from app.evolve.agent.tools.evidence import make_evidence_tools
from app.main import app  # noqa: F401 — 确保 app 上下文加载（TestClient 不必需）

_old_db = settings.evolution_db

WS = "ws-evidence"
OTHER_WS = "ws-other"


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


def _mk_runs():
    """造 20 条 trace：2 个 thread、2 个 harness 版本 + 1 条进化自观测。"""
    for i in range(20):
        ws = WS
        purpose = "user_generation"
        hv = "aaaa1111" if i < 10 else "bbbb2222"
        if i == 19:  # 进化自观测——不得出现
            ws, purpose, hv = "evolution", "evolution_evolve", "self"
        db.execute(
            """INSERT INTO runs (trace_id, workspace_id, thread_id, session_name, endpoint,
                 status, started_at, ended_at, duration_ms, event_count, ingested_at,
                 run_purpose, run_snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, json_object('harness_version', ?))""",
            (f"trace-{i:02d}", ws, f"thread-{i % 2}", f"会话{i}", "ep",
             "failed" if i % 5 == 0 else "completed",
             f"2026-10-01T00:{i:02d}:00", f"2026-10-01T00:{i:02d}:30",
             30000 + i * 100, 10 + i, f"2026-10-01T01:{i:02d}:00",
             purpose, hv),
        )


def _store_payload(value):
    """经 facts._store_payload 真实落盘（含内容寻址文件）。"""
    from app.trace.facts import _store_payload as _sp
    return _sp(value)


def _mk_artifacts():
    """造 2 个产物共 3 个修订（1 个已过期）。"""
    now = "2026-10-01T00:00:00"
    db.execute(
        "INSERT INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at)"
        " VALUES ('art-1', 'storyline', ?, 'storyline.md', ?)", (WS, now))
    db.execute(
        "INSERT INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at)"
        " VALUES ('art-2', 'character', ?, 'character/林零.md', ?)", (WS, now))
    # payload：两个可读（真实落盘）+ 一个过期（DB 行直接标过期，正文不可读）
    pid_fresh = _store_payload("故事线 v2 内容")
    pid_fresh2 = _store_payload("林零档案 v1")
    pid_old = "payload-expired"
    db.execute(
        """INSERT INTO payload_objects
           (payload_id, content_hash, kind, size_bytes, sensitivity, expires_at,
            storage_path, created_at)
           VALUES (?, 'h0', 'text', 10, 'normal', '2020-01-01T00:00:00+00:00',
                   '/nonexistent', '2020-01-01T00:00:00+00:00')""",
        (pid_old,))
    db.execute(
        "INSERT INTO artifact_revisions (artifact_revision_id, artifact_id, payload_id, content_hash,"
        " producer_trace_id, harness_version, created_at) VALUES"
        " ('rev-old', 'art-1', ?, 'h1', 'trace-00', 'aaaa1111', '2026-09-30T00:00:00'),"
        " ('rev-new', 'art-1', ?, 'h2', 'trace-05', 'bbbb2222', '2026-10-01T00:00:00'),"
        " ('rev-char', 'art-2', ?, 'h3', 'trace-07', 'bbbb2222', '2026-10-01T00:00:00')",
        (pid_old, pid_fresh, pid_fresh2))


class EvidenceToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = {t.name: t for t in make_evidence_tools()}
        cls.agent = agents_repo.create("证据Agent", WS)
        cls.other_agent = agents_repo.create("别家Agent", OTHER_WS)

    def setUp(self):
        with db.transaction() as conn:
            for t in ("runs", "nodes", "event_payloads", "artifacts",
                      "artifact_revisions", "payload_objects",
                      "evolve_messages", "evolve_sessions", "evolve_agents"):
                conn.execute(f"DELETE FROM {t}")
        # payload_objects 有 append-only 触发器？——按表清理验证
        self._recreate_ctx()

    def _recreate_ctx(self, session_id="sess-ev"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM evolve_agents")
        self.agent = agents_repo.create("证据Agent", WS)
        self.other_agent = agents_repo.create("别家Agent", OTHER_WS)
        ev_db.create_session(session_id, case_id="", agent_id=self.agent["agent_id"])
        ctx = EvolveContext(session_id)
        ctx.agent_id = self.agent["agent_id"]
        ctx.workspace_id = WS
        set_tool_context(ctx)
        self.ctx = ctx
        return ctx

    def _invoke(self, name, *args, **kwargs):
        if not args and not kwargs:
            args = ({},)
        return self.tools[name].invoke(*args, **kwargs)

    # ── AC-005：list_work_traces ──────────────────────────────

    def test_list_all_traces_paginated_with_cutoff_and_version(self):
        _mk_runs()
        out = self._invoke("list_work_traces")
        # 19 条作品 trace 全量（第 20 条是进化自观测，排除）
        self.assertIn("命中 19 条", out)
        self.assertIn("数据截止", out)
        self.assertIn("harness@aaaa1111", out)
        self.assertIn("harness@bbbb2222", out)
        self.assertNotIn("evolution_evolve", out)

    def test_filter_by_harness_version(self):
        _mk_runs()
        out = self._invoke("list_work_traces", {"harness_version": "aaaa1111"})
        self.assertIn("命中 10 条", out)
        self.assertNotIn("harness@bbbb2222", out)

    def test_refresh_param_triggers_scan(self):
        _mk_runs()
        with mock.patch("app.ingestion.scan._scan_once", return_value=3) as scan:
            out = self._invoke("list_work_traces", {"refresh": True})
        scan.assert_called_once()
        self.assertIn("补摄入 3 条", out)

    def test_read_trace_layers_and_cross_work_rejected(self):
        _mk_runs()
        run_row = db.query_one(
            "SELECT trace_id FROM runs WHERE workspace_id=? LIMIT 1", (WS,))
        tid = run_row["trace_id"]
        # 造节点 + 事件
        db.execute(
            "INSERT INTO nodes (node_id, trace_id, kind, label, status, duration_ms, tool_name)"
            " VALUES ('n1', ?, 'tool', 'write_storyline', 'ok', 1200, 'write_storyline')",
            (tid,))
        db.execute(
            "INSERT INTO event_payloads (trace_id, sequence, type, timestamp, payload_json)"
            " VALUES (?, 5, 'tool_call', '2026-10-01T00:00:05',"
            " json_object('type','tool_call','name','write_storyline','status','ok'))",
            (tid,))
        summary = self._invoke("read_work_trace", {"trace_id": tid, "detail": "summary"})
        self.assertIn("节点统计", summary)
        self.assertIn("tool 1", summary)
        skel = self._invoke("read_work_trace", {"trace_id": tid, "detail": "skeleton"})
        self.assertIn("write_storyline", skel)
        events = self._invoke(
            "read_work_trace", {"trace_id": tid, "detail": "events", "from_seq": 0, "to_seq": 10})
        self.assertIn("#5 tool_call", events)
        # 别家作品 trace 拒绝
        db.execute(
            """INSERT INTO runs (trace_id, workspace_id, thread_id, status, started_at,
                 ingested_at, run_purpose, run_snapshot_json)
               VALUES ('trace-other', ?, 't', 'completed', '2026-10-01T00:00:00',
                 '2026-10-01T01:00:00', 'user_generation', json_object('harness_version','x'))""",
            (OTHER_WS,))
        rejected = self._invoke("read_work_trace", {"trace_id": "trace-other", "detail": "summary"})
        self.assertIn("不属于绑定作品", rejected)

    # ── AC-006：产物双路 ─────────────────────────────────────

    def test_current_artifacts_passthrough_and_deleted(self):
        fake = {
            "workspace_id": WS, "title": "证据作品",
            "storyline": {"markdown": "# 故事线\n主线内容", "entries": [{"title": "主线"}]},
            "worldview": {"markdown": "# 世界观\n魔法"},
            "characters": {"characters": [{"filename": "林零.md", "name": "林零",
                                           "markdown": "# 林零\n主角"}]},
        }
        with mock.patch.object(executor_client, "fetch_workspace_artifacts",
                               return_value=fake):
            out = self._invoke("read_current_artifacts")
        self.assertIn("主线内容", out)
        self.assertIn("魔法", out)
        self.assertIn("林零", out)
        # 作品删除
        with mock.patch.object(executor_client, "fetch_workspace_artifacts",
                               side_effect=executor_client.WorkNotFoundError(WS)):
            out2 = self._invoke("read_current_artifacts")
        self.assertIn("作品已删除", out2)

    def test_revision_history_grouped_with_expired_flag(self):
        _mk_artifacts()
        out = self._invoke("list_artifact_revisions")
        self.assertIn("storyline.md", out)
        self.assertIn("2 版", out)
        self.assertIn("character/林零.md", out)
        self.assertIn("已过期", out)
        # 过期修订不可读正文
        out_old = self._invoke("read_artifact_revision", {"revision_id": "rev-old"})
        self.assertIn("已过期", out_old)
        self.assertNotIn("旧内容", out_old)
        # 可读修订返回正文
        out_new = self._invoke("read_artifact_revision", {"revision_id": "rev-new"})
        self.assertIn("故事线 v2 内容", out_new)

    # ── AC-007：自身历史会话 ─────────────────────────────────

    def test_agent_history_isolated_to_own_agent(self):
        ev_db.create_session("own-1", case_id="", agent_id=self.agent["agent_id"])
        ev_db.create_session("own-2", case_id="", agent_id=self.agent["agent_id"])
        ev_db.create_session("foreign", case_id="", agent_id=self.other_agent["agent_id"])
        EvolveMessagesRepo.append("own-1", role="assistant", content="上次结论：要改重试")
        EvolveMessagesRepo.append("foreign", role="assistant", content="别家的秘密")

        listing = self._invoke("list_agent_sessions")
        self.assertIn("own-1", listing)
        self.assertIn("own-2", listing)
        self.assertNotIn("foreign", listing)

        reading = self._invoke("read_agent_session", {"session_id": "own-1"})
        self.assertIn("上次结论", reading)
        rejected = self._invoke("read_agent_session", {"session_id": "foreign"})
        self.assertIn("不属于本 Agent", rejected)

    def test_unbound_session_rejected(self):
        ctx = EvolveContext("legacy-x")  # 无 agent 绑定
        set_tool_context(ctx)
        out = self._invoke("list_work_traces")
        self.assertIn("未绑定", out)


if __name__ == "__main__":
    unittest.main()
