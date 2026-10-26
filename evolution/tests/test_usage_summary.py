"""三层用量观测 API（REQ-20261027-012631 FR-002/003/004/005 → AC-001~006）。

用户层（/usage/users）与作品层（/usage/workspaces）聚合已入库 trace 的 token
（run_purpose=user_generation 口径）+ 活跃缓存实时叠加（DEC-010）；trace 层在
/traces 列表项加 token_total（无 usage 数据时 None，前端显示「—」）。

跑法（evolution 目录）：python -m pytest tests/test_usage_summary.py -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("EXECUTOR_URL", "http://127.0.0.1:0")

import app.core.db as db  # noqa: E402
import app.view.active as active_mod  # noqa: E402
from app.ingestion.workspace_sync import apply_workspaces  # noqa: E402
from app.view.usage import router as usage_router  # noqa: E402
from app.view.traces import router as traces_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _settings_targets() -> list:
    """返回需要设置 evolution_db 的全部 settings 单例。

    test_golden_add_case 会 importlib.reload(app.core.settings) 重建单例，
    而 db.py 的 from-import 仍持有旧对象（hook_gate.py 注释记录过此陷阱）。
    只设新对象时 db 实际连的是旧对象指向的库——两个对象都设，双保险。
    """
    import app.core.settings as settings_mod

    targets = [settings_mod.settings]
    db_settings = getattr(db, "settings", None)
    if db_settings is not None and db_settings is not settings_mod.settings:
        targets.append(db_settings)
    return targets


def _client() -> TestClient:
    """轻量 app 挂被测路由（仓库模式：不起完整 main，不触发 lifespan）。"""
    app = FastAPI()
    app.include_router(usage_router)
    app.include_router(traces_router, prefix="/api")
    return TestClient(app)


def _run(
    trace_id: str, workspace_id: str, owner: str, status: str,
    started_at: str, purpose: str | None = "user_generation",
) -> None:
    db.execute(
        """INSERT INTO runs (trace_id, workspace_id, thread_id, session_name, endpoint,
               status, started_at, ended_at, duration_ms, event_count, owner_user_id,
               run_purpose, ingested_at)
           VALUES (?, ?, NULL, NULL, NULL, ?, ?, NULL, 100, 1, ?, ?, ?)""",
        (trace_id, workspace_id, status, started_at, owner, purpose, started_at),
    )


def _node(trace_id: str, usage_total: int | None) -> None:
    db.execute(
        "INSERT INTO nodes (node_id, trace_id, kind, label, status, usage_total) "
        "VALUES (?, ?, 'llm', 'n', 'completed', ?)",
        (f"{trace_id}-n{usage_total}", trace_id, usage_total),
    )


class UserUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="usage_summary_")
        self._old_dbs = [t.evolution_db for t in _settings_targets()]
        for t in _settings_targets():
            t.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        db._conn = None
        db.init_db()
        self.client = _client()
        self._old_active = active_mod.get_active_runs

    def tearDown(self) -> None:
        active_mod.get_active_runs = self._old_active
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        for t, old in zip(_settings_targets(), self._old_dbs):
            t.evolution_db = old
        self.tmp.cleanup()

    def _seed_users(self) -> None:
        db.executemany(
            "INSERT INTO user_cache (user_id, username, disabled, synced_at) VALUES (?, ?, 0, '2026-10-27')",
            [("user-a", "作家甲"), ("user-b", "作家乙")],
        )

    def _set_active(self, runs: list[dict]) -> None:
        active_mod.get_active_runs = lambda: runs

    def test_ac001_user_summary_with_running_trace(self) -> None:
        """AC-001：已入库 token + 运行中 token 实时叠加，running 标记正确。"""
        self._seed_users()
        _run("t-1", "ws-1", "user-a", "completed", "2026-10-26T10:00:00+00:00")
        _node("t-1", 1500)
        self._set_active([{
            "trace_id": "t-2", "workspace_id": "ws-1", "owner_user_id": "user-a",
            "run_purpose": "user_generation", "status": "running",
            "started_at": "2026-10-27T09:00:00+00:00", "usage_total": 12345678,
        }])

        rows = {r["user_id"]: r for r in self.client.get("/usage/users").json()}
        a = rows["user-a"]
        self.assertTrue(a["running"])
        # DEC-006：运行中 trace 是最新一次创作，started_at 融入上次创作时间。
        self.assertEqual(a["last_created_at"], "2026-10-27T09:00:00+00:00")
        self.assertEqual(a["total_tokens"], 12345678 + 1500)

    def test_ac002_user_without_traces_visible_and_sorted_last(self) -> None:
        """AC-002：未创作用户可见，token 0、时间空、排最后。"""
        self._seed_users()
        _run("t-1", "ws-1", "user-a", "completed", "2026-10-26T10:00:00+00:00")
        self._set_active([])

        rows = self.client.get("/usage/users").json()
        self.assertEqual([r["user_id"] for r in rows], ["user-a", "user-b"])
        b = rows[1]
        self.assertFalse(b["running"])
        self.assertIsNone(b["last_created_at"])
        self.assertEqual(b["total_tokens"], 0)

    def test_ac003_evolution_purpose_excluded(self) -> None:
        """AC-003：evolution_eval/evolve 不计入用户 token；未标注 purpose 的历史
        数据按 user_generation 计（runs.run_purpose NOT NULL DEFAULT 'user_generation'，
        迁移已回填存量——历史口径即默认值路径）。"""
        self._seed_users()
        _run("t-eval", "ws-1", "user-a", "completed", "2026-10-26T08:00:00+00:00",
             purpose="evolution_eval")
        _node("t-eval", 5000000)
        _run("t-hist", "ws-1", "user-a", "completed", "2026-10-26T09:00:00+00:00",
             purpose="user_generation")
        _node("t-hist", 800)
        self._set_active([{
            "trace_id": "t-evo-live", "workspace_id": "", "owner_user_id": "user-a",
            "run_purpose": "evolution_evolve", "status": "running", "usage_total": 999999,
        }])

        rows = {r["user_id"]: r for r in self.client.get("/usage/users").json()}
        self.assertEqual(rows["user-a"]["total_tokens"], 800)


class WorkspaceUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="usage_ws_")
        self._old_dbs = [t.evolution_db for t in _settings_targets()]
        for t in _settings_targets():
            t.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        db._conn = None
        db.init_db()
        self.client = _client()
        self._old_active = active_mod.get_active_runs

    def tearDown(self) -> None:
        active_mod.get_active_runs = self._old_active
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        for t, old in zip(_settings_targets(), self._old_dbs):
            t.evolution_db = old
        self.tmp.cleanup()

    def _set_active(self, runs: list[dict]) -> None:
        active_mod.get_active_runs = lambda: runs

    def test_ac004_status_derivation(self) -> None:
        """AC-004 四场景：running 优先 / failed→aborted / completed→normal / 无 trace→normal。"""
        apply_workspaces([
            {"workspace_id": "ws-run", "title": "在写", "owner_user_id": "u"},
            {"workspace_id": "ws-fail", "title": "断了", "owner_user_id": "u"},
            {"workspace_id": "ws-ok", "title": "好的", "owner_user_id": "u"},
            {"workspace_id": "ws-new", "title": "新书", "owner_user_id": "u"},
        ])
        _run("t-f1", "ws-fail", "u", "completed", "2026-10-25T10:00:00+00:00")
        _run("t-f2", "ws-fail", "u", "failed", "2026-10-26T10:00:00+00:00")
        _run("t-ok", "ws-ok", "u", "completed", "2026-10-26T09:00:00+00:00")
        self._set_active([{
            "trace_id": "t-live", "workspace_id": "ws-run", "owner_user_id": "u",
            "run_purpose": "user_generation", "status": "running", "usage_total": 100,
        }])

        rows = {r["workspace_id"]: r for r in self.client.get("/usage/workspaces", params={"owner_user_id": "u"}).json()}
        self.assertEqual(rows["ws-run"]["status"], "running")
        self.assertEqual(rows["ws-fail"]["status"], "aborted")
        self.assertEqual(rows["ws-ok"]["status"], "normal")
        self.assertEqual(rows["ws-new"]["status"], "normal")
        # 运行中 token 也实时叠加（ws-run 无已入库 trace，叠加活跃 100）。
        self.assertEqual(rows["ws-run"]["total_tokens"], 100)

    def test_ac005_deleted_workspace_kept_with_history(self) -> None:
        """AC-005：已删除作品保留书名/归属/累计，标 deleted 状态。"""
        apply_workspaces([
            {"workspace_id": "ws-1", "title": "星海之上", "owner_user_id": "u"},
            {"workspace_id": "ws-2", "title": "别的", "owner_user_id": "u"},
        ])
        _run("t-1", "ws-1", "u", "completed", "2026-10-26T10:00:00+00:00")
        _node("t-1", 4321)
        apply_workspaces([{"workspace_id": "ws-2", "title": "别的", "owner_user_id": "u"}])
        self._set_active([])

        rows = {r["workspace_id"]: r for r in self.client.get("/usage/workspaces", params={"owner_user_id": "u"}).json()}
        gone = rows["ws-1"]
        self.assertEqual(gone["status"], "deleted")
        self.assertEqual(gone["title"], "星海之上")
        self.assertEqual(gone["total_tokens"], 4321)

    def test_workspace_missing_from_cache_still_listed(self) -> None:
        """对账窗口内的新作品（runs 有、缓存无）不得消失，书名降级为空。"""
        _run("t-fresh", "ws-brand-new", "u", "completed", "2026-10-26T10:00:00+00:00")
        self._set_active([])

        rows = {r["workspace_id"]: r for r in self.client.get("/usage/workspaces", params={"owner_user_id": "u"}).json()}
        fresh = rows["ws-brand-new"]
        self.assertEqual(fresh["title"], "")
        self.assertEqual(fresh["status"], "normal")
        self.assertEqual(fresh["total_tokens"], 0)


class TraceTokenTest(unittest.TestCase):
    """AC-006：trace 列表项 token_total——有数据给数，无数据给 None。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="usage_trace_")
        self._old_dbs = [t.evolution_db for t in _settings_targets()]
        for t in _settings_targets():
            t.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        db._conn = None
        db.init_db()
        self.client = _client()

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        for t, old in zip(_settings_targets(), self._old_dbs):
            t.evolution_db = old
        self.tmp.cleanup()

    def test_ac006_token_total_present_and_absent(self) -> None:
        _run("t-with", "ws-1", "u", "completed", "2026-10-26T10:00:00+00:00")
        _node("t-with", 9876543)
        _run("t-zero", "ws-1", "u", "completed", "2026-10-26T11:00:00+00:00")
        _node("t-zero", 0)
        _run("t-none", "ws-1", "u", "completed", "2026-10-26T12:00:00+00:00")

        resp = self.client.get("/api/traces", params={"limit": 10}).json()
        items = {i["trace_id"]: i for i in resp["items"]}
        self.assertEqual(items["t-with"]["token_total"], 9876543)
        self.assertEqual(items["t-zero"]["token_total"], 0)
        self.assertIsNone(items["t-none"]["token_total"])


if __name__ == "__main__":
    unittest.main()
