"""作品缓存对账同步（REQ-20261027-012631 FR-006 / DEC-004 / DEC-011 / AC-005）。

executor 的作品删除是物理删除——不缓存则书名与归属永久丢失。workspace_cache
表 + 5 分钟对账：库里查不到的作品标 deleted=1，缓存记录与 token 累计保留；
拉取失败跳过本轮，不得误标。

跑法（evolution 目录）：python -m pytest tests/test_workspace_cache_sync.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app.core.db as db


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


class WorkspaceCacheSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ws_cache_sync_")
        self._old_dbs = [t.evolution_db for t in _settings_targets()]
        for t in _settings_targets():
            t.evolution_db = str(Path(self.tmp.name) / "evolution.db")
        db._conn = None
        db.init_db()
        from app.ingestion import workspace_sync

        self.sync = workspace_sync
        # 独立于网络层：直接喂 executor /internal/workspaces 的负载形状。
        self.apply = workspace_sync.apply_workspaces

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        for t, old in zip(_settings_targets(), self._old_dbs):
            t.evolution_db = old
        self.tmp.cleanup()

    def _rows(self) -> list[dict]:
        return [dict(r) for r in db.query_all(
            "SELECT workspace_id, owner_user_id, title, deleted FROM workspace_cache "
            "ORDER BY workspace_id"
        )]

    def test_first_sync_inserts_workspaces(self) -> None:
        self.apply([
            {"workspace_id": "ws-1", "title": "星海之上", "owner_user_id": "user-a"},
            {"workspace_id": "ws-2", "title": "深巷灯影", "owner_user_id": "user-b"},
        ])
        self.assertEqual(self._rows(), [
            {"workspace_id": "ws-1", "owner_user_id": "user-a", "title": "星海之上", "deleted": 0},
            {"workspace_id": "ws-2", "owner_user_id": "user-b", "title": "深巷灯影", "deleted": 0},
        ])

    def test_resync_updates_title_and_admits_new(self) -> None:
        self.apply([{"workspace_id": "ws-1", "title": "旧名", "owner_user_id": "user-a"}])
        self.apply([
            {"workspace_id": "ws-1", "title": "新名", "owner_user_id": "user-a"},
            {"workspace_id": "ws-3", "title": "第三本", "owner_user_id": "user-a"},
        ])
        rows = {r["workspace_id"]: r for r in self._rows()}
        self.assertEqual(rows["ws-1"]["title"], "新名")
        self.assertEqual(rows["ws-3"]["title"], "第三本")
        self.assertTrue(all(r["deleted"] == 0 for r in rows.values()))

    def test_missing_workspace_marked_deleted_with_history_kept(self) -> None:
        """AC-005 核心：executor 查不到 → 标已删除，书名/归属保留。"""
        self.apply([
            {"workspace_id": "ws-1", "title": "星海之上", "owner_user_id": "user-a"},
            {"workspace_id": "ws-2", "title": "深巷灯影", "owner_user_id": "user-b"},
        ])
        self.apply([{"workspace_id": "ws-2", "title": "深巷灯影", "owner_user_id": "user-b"}])
        rows = {r["workspace_id"]: r for r in self._rows()}
        self.assertEqual(rows["ws-1"]["deleted"], 1)
        self.assertEqual(rows["ws-1"]["title"], "星海之上")
        self.assertEqual(rows["ws-1"]["owner_user_id"], "user-a")
        self.assertEqual(rows["ws-2"]["deleted"], 0)

    def test_deleted_workspace_reappearing_is_restored(self) -> None:
        """已删除作品若再次出现（理论不发生），upsert 重置 deleted=0。"""
        self.apply([{"workspace_id": "ws-1", "title": "星海之上", "owner_user_id": "user-a"}])
        self.apply([])
        self.apply([{"workspace_id": "ws-1", "title": "星海之上", "owner_user_id": "user-a"}])
        self.assertEqual(self._rows()[0]["deleted"], 0)

    def test_empty_executor_list_marks_all_deleted(self) -> None:
        """拉取成功且列表为空是真实状态：全部标删（与失败跳过区分）。"""
        self.apply([{"workspace_id": "ws-1", "title": "星海之上", "owner_user_id": "user-a"}])
        self.apply([])
        rows = self._rows()
        self.assertEqual(rows[0]["deleted"], 1)

    def test_sync_once_failure_skips_reconcile(self) -> None:
        """FR-006 失败语义：拉取失败不动任何状态，已删除不复活、未删不误标。"""
        self.apply([{"workspace_id": "ws-1", "title": "星海之上", "owner_user_id": "user-a"}])
        self.apply([])  # ws-1 进入已删除态
        before = self._rows()

        def failing_fetch(url: str) -> dict:
            raise ConnectionError("executor 不可达")

        self.sync.sync_once(fetch=failing_fetch)
        self.assertEqual(self._rows(), before)

    def test_sync_once_applies_fetched_list(self) -> None:
        def fake_fetch(url: str) -> dict:
            assert url.endswith("/internal/workspaces")
            return {"workspaces": [
                {"workspace_id": "ws-9", "title": "远端新书", "owner_user_id": "user-z"},
            ], "total": 1}

        self.sync.sync_once(fetch=fake_fetch)
        self.assertEqual(self._rows(), [
            {"workspace_id": "ws-9", "owner_user_id": "user-z", "title": "远端新书", "deleted": 0},
        ])


if __name__ == "__main__":
    unittest.main()
