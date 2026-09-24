"""系统资产 API 数据源切换测试（Platform 账本，REQ-20260923-145931 FR-001/002/003）。

最小 app 只挂 snapshot/versions 两路由（不拉起完整 main——lifespan 依赖 DB/调度器）。
覆盖：
- GET /api/snapshots：账本列表（倒序 + 富化）、账本不可达 502
- GET /api/snapshots/{version}：404 / 200
- GET /api/snapshots/{version}/upgrade-diff：200 形状 / 404 / 502
- GET /api/versions：envelope 形状（items/total/production_version/limit/offset）
- GET /api/versions/{version}：detail + is_bootstrap 判定
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.versioning import platform_ledger, snapshot_api, upgrade_diff
from app.view import versions_api


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(snapshot_api.router, prefix="/api")
    app.include_router(versions_api.router, prefix="/api")
    return TestClient(app)


def _versions_fixture():
    """账本富化条目样例（对齐线上 v6–v14 关键形态）。"""
    return [
        {"version": 14, "status": "production", "change_summary": "", "created_at": "2026-09-22",
         "commit": "c14", "same_code_as": None, "based_on": 10, "based_on_status": "resolved"},
        {"version": 13, "status": "retired", "change_summary": "", "created_at": "2026-09-22",
         "commit": "c13", "same_code_as": None, "based_on": 14, "based_on_status": "resolved"},
        {"version": 9, "status": "retired", "change_summary": "Phase A 验证",
         "created_at": "2026-09-19",
         "commit": "c8", "same_code_as": 8, "based_on": None, "based_on_status": "root"},
        {"version": 8, "status": "retired", "change_summary": "修复拦截",
         "created_at": "2026-08-02",
         "commit": "c8", "same_code_as": None, "based_on": 7, "based_on_status": "resolved"},
        {"version": 6, "status": "retired", "change_summary": "根版本",
         "created_at": "2026-07-20",
         "commit": "c6", "same_code_as": None, "based_on": None, "based_on_status": "root"},
    ]


class SnapshotsApiTest(unittest.TestCase):
    def test_list_from_ledger(self):
        with patch.object(platform_ledger, "list_versions", return_value=_versions_fixture()):
            resp = _make_client().get("/api/snapshots")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertEqual([v["version"] for v in items], [14, 13, 9, 8, 6])
        by_ver = {v["version"]: v for v in items}
        self.assertEqual(by_ver[14]["status"], "production")
        self.assertEqual(by_ver[9]["same_code_as"], 8)

    def test_list_ledger_unreachable_502(self):
        with patch.object(platform_ledger, "list_versions",
                          side_effect=RuntimeError("Platform 账本不可达：boom")):
            resp = _make_client().get("/api/snapshots")
        self.assertEqual(resp.status_code, 502)

    def test_get_version_404_and_200(self):
        client = _make_client()
        with patch.object(platform_ledger, "get_version", return_value=None):
            self.assertEqual(client.get("/api/snapshots/99").status_code, 404)
        with patch.object(platform_ledger, "get_version", return_value=_versions_fixture()[0]):
            resp = client.get("/api/snapshots/14")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["based_on"], 10)


class UpgradeDiffApiTest(unittest.TestCase):
    def _payload(self):
        return {
            "version": 14, "target_commit": "c14", "base_kind": "ancestor",
            "base_version": 10, "base_commit": "c10", "same_code_as": None,
            "changes": {"agents": [{"agent": "storybuilding", "diff": {
                "prompt": {"hunks": [], "summary": {"added": 2, "removed": 1}},
                "skills": None, "processors": [],
            }}], "intent": None},
        }

    def test_ok(self):
        with patch.object(upgrade_diff, "build_upgrade_diff", return_value=self._payload()):
            resp = _make_client().get("/api/snapshots/14/upgrade-diff")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["base_kind"], "ancestor")
        self.assertEqual(body["changes"]["agents"][0]["agent"], "storybuilding")

    def test_404_unknown_version(self):
        with patch.object(upgrade_diff, "build_upgrade_diff",
                          side_effect=LookupError("版本 v99 不存在")):
            resp = _make_client().get("/api/snapshots/99/upgrade-diff")
        self.assertEqual(resp.status_code, 404)

    def test_502_ledger_unreachable(self):
        with patch.object(upgrade_diff, "build_upgrade_diff",
                          side_effect=RuntimeError("Platform 账本不可达：boom")):
            resp = _make_client().get("/api/snapshots/14/upgrade-diff")
        self.assertEqual(resp.status_code, 502)


class VersionsApiTest(unittest.TestCase):
    def test_list_envelope(self):
        fixture = _versions_fixture()
        with patch.object(platform_ledger, "list_versions", return_value=fixture), \
             patch.object(platform_ledger, "production_version_number", return_value=14):
            resp = _make_client().get("/api/versions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["production_version"], 14)
        self.assertEqual(body["items"][0]["version"], 14)
        self.assertIn("same_code_as", body["items"][0])
        self.assertIn("based_on", body["items"][0])

    def test_list_502(self):
        with patch.object(platform_ledger, "list_versions",
                          side_effect=RuntimeError("Platform 账本不可达：boom")):
            resp = _make_client().get("/api/versions")
        self.assertEqual(resp.status_code, 502)

    def test_detail_is_bootstrap(self):
        client = _make_client()
        fixture = {v["version"]: v for v in _versions_fixture()}
        with patch.object(platform_ledger, "get_version",
                          side_effect=lambda v: fixture.get(v)):
            resp_root = client.get("/api/versions/6")
            resp_child = client.get("/api/versions/14")
        self.assertTrue(resp_root.json()["is_bootstrap"])
        self.assertFalse(resp_child.json()["is_bootstrap"])


if __name__ == "__main__":
    unittest.main()
