"""loader 冷启动 fail-static + 生产周期对账测试（review finding #3，FR-003/DEC-008）。

核心断言：
- load_current_package：current_production 失败 → known_production.json 的
  commit + 本地 artifact 缓存装配成功（除失败的 /api/production 外零 HTTP）；
  known/缓存都没有 → 抛原异常。
- reconcile：Platform 生产 commit ≠ 当前加载 → reload_current(commit, digest)；
  一致或未加载 → 不动；对账异常不外抛。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.platform.agent import loader as loader_mod
from app.platform.agent import reconcile as reconcile_mod
from app.platform.agent.artifact_client import ArtifactClient


def _down_transport(requests: list[str]) -> httpx.MockTransport:
    """Platform 全线 503（模拟不可达），记录每次请求路径。"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(503)

    return httpx.MockTransport(handler)


class LoadCurrentPackageFailStaticTest(unittest.TestCase):
    def setUp(self) -> None:
        loader_mod.reset_cache()

    def tearDown(self) -> None:
        loader_mod.reset_cache()

    def test_fallback_to_known_production_with_local_cache(self) -> None:
        """Platform 不可达 + known_production 有 commit + 缓存命中 → 降级装配成功。"""
        commit = "f" * 40
        with TemporaryDirectory() as tmp:
            # 预置本地 artifact 缓存（解包完成的 commit 目录）
            dest = Path(tmp) / "cache" / commit
            dest.mkdir(parents=True)
            (dest / "__init__.py").write_text("X = 1\n", encoding="utf-8")

            requests: list[str] = []
            client = ArtifactClient(
                "http://platform-test", Path(tmp) / "cache",
                client=httpx.Client(
                    base_url="http://platform-test",
                    transport=_down_transport(requests),
                ),
            )
            known = Path(tmp) / "known_production.json"
            known.write_text(
                json.dumps({"commit": commit, "manifest_id": 3}), encoding="utf-8",
            )
            fake_binding = SimpleNamespace(
                get_known_production=lambda: json.loads(known.read_text("utf-8")),
            )

            with patch(
                "app.platform.agent.artifact_client.get_artifact_client",
                return_value=client,
            ), patch(
                "app.platform.agent.binding_client.get_binding_client",
                return_value=fake_binding,
            ):
                pkg = loader_mod.load_current_package()

            self.assertEqual(pkg.X, 1)
            self.assertEqual(loader_mod.production_commit(), commit)
            self.assertEqual(loader_mod.production_checkout(), dest)
            # 缓存命中零下载：唯一 HTTP 是失败的 /api/production
            self.assertEqual(requests, ["/api/production"])

    def test_no_known_production_raises_original(self) -> None:
        """Platform 不可达且无 known_production 缓存 → 抛原异常。"""
        with TemporaryDirectory() as tmp:
            requests: list[str] = []
            client = ArtifactClient(
                "http://platform-test", Path(tmp) / "cache",
                client=httpx.Client(
                    base_url="http://platform-test",
                    transport=_down_transport(requests),
                ),
            )
            fake_binding = SimpleNamespace(get_known_production=lambda: None)

            with patch(
                "app.platform.agent.artifact_client.get_artifact_client",
                return_value=client,
            ), patch(
                "app.platform.agent.binding_client.get_binding_client",
                return_value=fake_binding,
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    loader_mod.load_current_package()
            self.assertIn("生产版本失败", str(ctx.exception))  # 原异常（非回退异常）

    def test_known_commit_without_cache_raises_original(self) -> None:
        """known_production 有 commit 但本地缓存没有 → 抛原异常（回退失败不遮根因）。"""
        commit = "e" * 40
        with TemporaryDirectory() as tmp:
            requests: list[str] = []
            client = ArtifactClient(
                "http://platform-test", Path(tmp) / "cache",
                client=httpx.Client(
                    base_url="http://platform-test",
                    transport=_down_transport(requests),
                ),
            )
            fake_binding = SimpleNamespace(
                get_known_production=lambda: {"commit": commit},
            )

            with patch(
                "app.platform.agent.artifact_client.get_artifact_client",
                return_value=client,
            ), patch(
                "app.platform.agent.binding_client.get_binding_client",
                return_value=fake_binding,
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    loader_mod.load_current_package()
            self.assertIn("生产版本失败", str(ctx.exception))
            # 回退尝试走了 meta 拉取（Platform 依旧 503）
            self.assertIn(f"/api/artifacts/{commit}/meta", requests)


class ReconcileLoopTest(unittest.TestCase):
    def _status(self, commit: str) -> SimpleNamespace:
        return SimpleNamespace(
            version=7, commit=commit, artifact_digest="d" * 64,
            surface_fingerprint={}, promoted_at="",
        )

    def test_reconcile_triggers_reload_on_new_commit(self) -> None:
        """对账发现新生产 commit → reload_current(commit, digest)。"""
        fake_client = SimpleNamespace(
            current_production=lambda: self._status("b" * 40),
        )
        reloads: list[tuple] = []

        def fake_reload(commit=None, digest=None):
            reloads.append((commit, digest))
            return None

        with patch(
            "app.platform.agent.artifact_client.get_artifact_client",
            return_value=fake_client,
        ), patch(
            "app.platform.agent.loader.production_commit", return_value="a" * 40,
        ), patch(
            "app.platform.agent.loader.reload_current", side_effect=fake_reload,
        ):
            asyncio.run(reconcile_mod.reconcile_production_once())

        self.assertEqual(reloads, [("b" * 40, "d" * 64)])

    def test_reconcile_noop_when_same_or_unloaded(self) -> None:
        """commit 一致或尚未加载生产包 → 不触发 reload。"""
        reloads: list[tuple] = []

        def fake_reload(commit=None, digest=None):
            reloads.append((commit, digest))
            return None

        fake_client = SimpleNamespace(
            current_production=lambda: self._status("a" * 40),
        )
        with patch(
            "app.platform.agent.artifact_client.get_artifact_client",
            return_value=fake_client,
        ), patch(
            "app.platform.agent.loader.production_commit", return_value="a" * 40,
        ), patch(
            "app.platform.agent.loader.reload_current", side_effect=fake_reload,
        ):
            asyncio.run(reconcile_mod.reconcile_production_once())
        self.assertEqual(reloads, [])

        # 尚未加载（production_commit 为空）→ 连 current_production 都不查
        calls: list[str] = []

        def _prod() -> SimpleNamespace:
            calls.append("called")
            return self._status("a" * 40)

        with patch(
            "app.platform.agent.artifact_client.get_artifact_client",
            return_value=SimpleNamespace(current_production=_prod),
        ), patch(
            "app.platform.agent.loader.production_commit", return_value="",
        ), patch(
            "app.platform.agent.loader.reload_current", side_effect=fake_reload,
        ):
            asyncio.run(reconcile_mod.reconcile_production_once())
        self.assertEqual(calls, [])

    def test_reconcile_failure_is_silent(self) -> None:
        """对账轮内部异常由 _reconcile_loop 吞掉（兜底通道不拖垮协程）。"""

        def _boom() -> None:
            raise RuntimeError("platform down")

        async def run() -> None:
            task = asyncio.create_task(reconcile_mod._reconcile_loop())
            await asyncio.sleep(0.1)  # 越过 sleep，跑一轮抛异常的对账
            self.assertFalse(task.done())  # 异常轮之后协程仍存活
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with patch.object(reconcile_mod, "_RECONCILE_INTERVAL", 0.01), patch(
            "app.platform.agent.artifact_client.get_artifact_client",
            return_value=SimpleNamespace(current_production=_boom),
        ), patch(
            "app.platform.agent.loader.production_commit", return_value="a" * 40,
        ):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
