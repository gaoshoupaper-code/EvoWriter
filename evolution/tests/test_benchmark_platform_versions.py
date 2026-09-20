"""评测版本数据源切换测试（Platform 账本替代冻结 registry.json）。

覆盖：
- fetch_platform_versions：正常解析 / 不可达 / 非 200 / 响应畸形（宁拒勿错）
- runner 版本解析：production 版本号、版本→commit、trigger 传账本 commit
- API：GET /benchmark/versions 形状与状态标记；Platform 不可达 502；
  触发时默认版本解析失败快败 502（不给半配置批次）
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.benchmark import manifest as bench_manifest
from app.benchmark import runner

# 模拟账本流水：production=12（v9 单故事专家架构），历史 v8
_LEDGER = {
    "items": [
        {"version": 12, "commit": "c9" * 20, "note": "v9 单故事专家架构", "created_at": "2026-09-20"},
        {"version": 8, "commit": "08" * 20, "note": "旧版本", "created_at": "2026-08-02"},
    ],
    "production_version": 12,
}


class _Resp:
    """httpx.Response 替身（只提供 fetch 用到的两个字段）。"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else _LEDGER

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


class FetchPlatformVersionsTest(unittest.TestCase):
    def test_ok(self):
        with patch.object(bench_manifest.httpx, "get", return_value=_Resp()):
            data = bench_manifest.fetch_platform_versions()
        self.assertEqual(data["production_version"], 12)
        self.assertEqual([v["version"] for v in data["items"]], [12, 8])

    def test_unreachable_raises(self):
        with patch.object(bench_manifest.httpx, "get", side_effect=httpx.ConnectError("boom")):
            with self.assertRaises(RuntimeError):
                bench_manifest.fetch_platform_versions()

    def test_non_200_raises(self):
        with patch.object(bench_manifest.httpx, "get", return_value=_Resp(status_code=500)):
            with self.assertRaises(RuntimeError):
                bench_manifest.fetch_platform_versions()

    def test_malformed_items_raises(self):
        bad = _Resp(payload={"items": [{"version": "x", "commit": 1}]})
        with patch.object(bench_manifest.httpx, "get", return_value=bad):
            with self.assertRaises(RuntimeError):
                bench_manifest.fetch_platform_versions()

    def test_malformed_production_raises(self):
        bad = _Resp(payload={"items": [], "production_version": "12"})
        with patch.object(bench_manifest.httpx, "get", return_value=bad):
            with self.assertRaises(RuntimeError):
                bench_manifest.fetch_platform_versions()


class RunnerResolutionTest(unittest.TestCase):
    def test_production_version_from_ledger(self):
        with patch.object(bench_manifest.httpx, "get", return_value=_Resp()):
            self.assertEqual(runner._get_production_version(), 12)

    def test_snapshot_and_miss_from_ledger(self):
        with patch.object(bench_manifest.httpx, "get", return_value=_Resp()):
            snap = runner._get_snapshot(8)
            self.assertEqual(snap["commit"], "08" * 20)
            self.assertIsNone(runner._get_snapshot(999))

    def test_trigger_executor_sends_ledger_commit(self):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            return _Resp(payload={"task_id": "t1"})

        snap = {"version": 12, "commit": "c9" * 20}
        with patch.object(runner.httpx, "post", side_effect=fake_post):
            task_id = runner._trigger_executor("需求", snap)
        self.assertEqual(task_id, "t1")
        self.assertIn("/internal/ab/run", captured["url"])
        self.assertEqual(captured["payload"]["source_commit"], "c9" * 20)


class ApiVersionsTest(unittest.TestCase):
    def _api(self):
        from app.benchmark import api as bench_api

        return bench_api

    def test_endpoint_shape(self):
        bench_api = self._api()
        with patch.object(bench_manifest.httpx, "get", return_value=_Resp()):
            data = bench_api.list_versions()
        self.assertEqual(data["production_version"], 12)
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["items"][0]["status"], "production")
        self.assertEqual(data["items"][0]["change_summary"], "v9 单故事专家架构")
        self.assertEqual(data["items"][1]["status"], "retired")
        self.assertEqual(data["items"][1]["commit"], "08" * 20)

    def test_endpoint_502_when_platform_down(self):
        bench_api = self._api()
        from fastapi import HTTPException

        with patch.object(bench_manifest.httpx, "get", side_effect=httpx.ConnectError("boom")):
            with self.assertRaises(HTTPException) as ctx:
                bench_api.list_versions()
        self.assertEqual(ctx.exception.status_code, 502)

    def test_trigger_default_resolution_failfast_502(self):
        """versions=None 且账本不可达 → 502 快败，不建半配置批次。"""
        bench_api = self._api()
        from fastapi import HTTPException

        with patch.object(
            runner, "_get_production_version", side_effect=RuntimeError("Platform 账本不可达")
        ):
            with self.assertRaises(HTTPException) as ctx:
                bench_api.trigger_run(bench_api.RunRequest())
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
