"""internal 路由的 Phase A 改造测试：

- POST /internal/platform/reload：ensure_artifact（digest 校验）→ reload_current
  → 重算 runtime_identity → 返回 {status, commit, runtime_identity}
- 旧 POST /internal/reload 保留：内部调 reload_current()（向 Platform 对账）
- /internal/harness/probe 已删除（FR-002：门禁迁 Platform，executor 无门禁语义）
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.internal import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class PlatformReloadTest(unittest.TestCase):
    def test_platform_reload_ensure_then_reload(self) -> None:
        """带 commit+digest 的 reload 通知：ensure_artifact 收到 digest，reload_current 用通知 commit。"""
        calls = SimpleNamespace(ensured=[], reloaded=[])

        def fake_ensure(commit, expected_digest=None):
            calls.ensured.append((commit, expected_digest))
            return Path("cached")

        def fake_reload(commit=None, digest=None):
            calls.reloaded.append((commit, digest))

        fake_client = SimpleNamespace(ensure_artifact=fake_ensure)
        with patch(
            "app.platform.agent.artifact_client.get_artifact_client",
            return_value=fake_client,
        ), patch(
            "app.platform.agent.loader.reload_current", side_effect=fake_reload,
        ), patch(
            "app.platform.agent.loader.production_commit", return_value="c" * 40,
        ), patch(
            "app.platform.agent.loader.production_checkout",
            return_value=Path(__file__).parent,  # 任意目录均可算身份
        ):
            resp = _client().post("/internal/platform/reload", json={
                "version": 3, "commit": "c" * 40, "artifact_digest": "d" * 64,
            })

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "reloaded")
        self.assertEqual(body["commit"], "c" * 40)
        self.assertIn("identity_digest", body["runtime_identity"])
        # 顺序语义：先 ensure（digest 校验）再 reload；reload 用通知 commit 不再对账
        self.assertEqual(calls.ensured, [("c" * 40, "d" * 64)])
        self.assertEqual(calls.reloaded, [("c" * 40, None)])

    def test_platform_reload_bad_body_rejected(self) -> None:
        """缺 commit（min_length=1）→ 422 契约校验。"""
        resp = _client().post("/internal/platform/reload", json={"version": 1})
        self.assertEqual(resp.status_code, 422)

    def test_legacy_reload_reconciles_with_platform(self) -> None:
        """旧 /internal/reload：无 git 操作，等价于向 Platform 对账拉最新。"""
        calls = SimpleNamespace(reloaded=[])

        def fake_reload(commit=None, digest=None):
            calls.reloaded.append(commit)

        with patch(
            "app.platform.agent.loader.reload_current", side_effect=fake_reload,
        ), patch(
            "app.platform.agent.loader.production_commit", return_value="e" * 40,
        ), patch(
            "app.platform.agent.loader.production_checkout",
            return_value=Path(__file__).parent,
        ):
            resp = _client().post("/internal/reload")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "reloaded")
        self.assertEqual(resp.json()["commit"], "e" * 40)
        self.assertEqual(calls.reloaded, [None])  # 无 commit → 内部走 current_production 对账


class ProbeRemovalTest(unittest.TestCase):
    def test_harness_probe_route_removed(self) -> None:
        """FR-002：门禁迁 Platform，executor 路由表不再有 /internal/harness/probe。"""
        app = FastAPI()
        app.include_router(router)
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertNotIn("/internal/harness/probe", paths)
        self.assertIn("/internal/platform/reload", paths)
        self.assertIn("/internal/reload", paths)  # Phase A 过渡保留


if __name__ == "__main__":
    unittest.main()
