"""artifact_client 测试：下载/digest 校验/缓存命中/LRU 清理/安全解包。

用 httpx.MockTransport 模拟 Platform（meta + download），不依赖真实服务。
"""
from __future__ import annotations

import hashlib
import io
import tarfile
import unittest
from pathlib import Path

import httpx

from app.platform.agent.artifact_client import ArtifactClient


def _make_tar_bytes(files: dict[str, str]) -> bytes:
    """构造 tar.gz 字节流（files: rel_path → content）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_transport(commit: str, tar_bytes: bytes, digest: str | None = None,
                    production_commit: str | None = None,
                    requests: list[str] | None = None):
    """构造 MockTransport：/api/production、/api/artifacts/{c}/meta、/download。"""
    real_digest = hashlib.sha256(tar_bytes).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if requests is not None:
            requests.append(path)
        if path == "/api/production":
            return httpx.Response(200, json={
                "version": 3,
                "commit": production_commit or commit,
                "artifact_digest": real_digest,
                "surface_fingerprint": {"a_text": {}, "b_param": {}, "c_code": {}},
                "promoted_at": "2026-09-19T00:00:00Z",
            })
        if path == f"/api/artifacts/{commit}/meta":
            return httpx.Response(200, json={
                "commit": commit,
                "digest": digest if digest is not None else real_digest,
                "size_bytes": len(tar_bytes),
                "file_count": 2,
                "built_at": "2026-09-19T00:00:00Z",
            })
        if path == f"/api/artifacts/{commit}/download":
            return httpx.Response(200, content=tar_bytes)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


class ArtifactClientTest(unittest.TestCase):
    def _client(self, tmp: Path, transport: httpx.MockTransport) -> ArtifactClient:
        return ArtifactClient(
            "http://platform-test",
            tmp / "cache",
            client=httpx.Client(base_url="http://platform-test", transport=transport),
        )

    def test_download_and_cache_hit(self) -> None:
        """首次下载（meta+download 各一次）→ 解包成功；再次调用缓存命中 0 次 HTTP。"""
        import tempfile

        tar_bytes = _make_tar_bytes({
            "__init__.py": "X = 1\n",
            "middleware/goal.py": "class Goal: pass\n",
        })
        requests: list[str] = []
        transport = _make_transport("c1" * 20, tar_bytes, requests=requests)

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), transport)
            dest = client.ensure_artifact("c1" * 20)
            self.assertTrue((dest / "__init__.py").is_file())
            self.assertTrue((dest / "middleware" / "goal.py").is_file())
            self.assertEqual(len(requests), 2)  # meta + download

            requests.clear()
            dest2 = client.ensure_artifact("c1" * 20)
            self.assertEqual(dest2, dest)
            self.assertEqual(requests, [])  # 缓存命中，零 HTTP

    def test_digest_mismatch_rejected_no_leftover(self) -> None:
        """digest 不符 → RuntimeError 拒绝装配，且缓存目录无半成品。"""
        import tempfile

        tar_bytes = _make_tar_bytes({"__init__.py": "X = 1\n"})
        # meta 声称的 digest 与真实内容不符
        transport = _make_transport("c2" * 20, tar_bytes, digest="0" * 64)

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), transport)
            with self.assertRaises(RuntimeError, msg=None) as ctx:
                client.ensure_artifact("c2" * 20)
            self.assertIn("摘要不符", str(ctx.exception))
            cache_dir = Path(tmp) / "cache"
            # 无 commit 目录、无 .dl- 临时文件
            self.assertEqual(list(cache_dir.iterdir()), [])

    def test_expected_digest_from_notice(self) -> None:
        """reload 通知携带的 expected_digest 与 meta 不符 → 拒绝（发版链路不一致）。"""
        import tempfile

        tar_bytes = _make_tar_bytes({"__init__.py": "X = 1\n"})
        transport = _make_transport("c3" * 20, tar_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), transport)
            with self.assertRaises(RuntimeError):
                client.ensure_artifact("c3" * 20, expected_digest="f" * 64)
            # 一致时通过
            real = hashlib.sha256(tar_bytes).hexdigest()
            dest = client.ensure_artifact("c3" * 20, expected_digest=real)
            self.assertTrue((dest / "__init__.py").is_file())

    def test_traversal_member_rejected(self) -> None:
        """tar 含 .. 穿越成员 → RuntimeError，且不落盘任何穿越文件。"""
        import tempfile

        evil = _make_tar_bytes({
            "__init__.py": "X = 1\n",
            "../evil.txt": "pwned\n",
        })
        commit4 = "c4" * 20
        transport = _make_transport(commit4, evil)

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), transport)
            with self.assertRaises(RuntimeError):
                client.ensure_artifact(commit4)
            self.assertFalse((Path(tmp) / "evil.txt").exists())
            self.assertFalse((Path(tmp) / "cache" / commit4).exists())

    def test_lru_eviction_keeps_recent_five(self) -> None:
        """第 6 个 commit 下载后，最旧 commit 目录被清理（保留最近 5 个）。"""
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            # 预置 5 个"历史" commit 目录，mtime 各差 10s，保证淘汰顺序确定
            for i in range(5):
                d = cache / f"old{i}"
                d.mkdir(parents=True)
                (d / "__init__.py").write_text("X=1", encoding="utf-8")
                stamp = time.time() - (5 - i) * 10
                import os
                os.utime(d, (stamp, stamp))

            tar_bytes = _make_tar_bytes({"__init__.py": "X = 1\n"})
            transport = _make_transport("newc" + "9" * 16, tar_bytes)
            client = self._client(Path(tmp), transport)
            client.ensure_artifact("newc" + "9" * 16)

            dirs = sorted(d.name for d in cache.iterdir())
            self.assertNotIn("old0", dirs)  # 最旧的被淘汰
            self.assertIn("old1", dirs)
            self.assertIn("newc" + "9" * 16, dirs)
            self.assertEqual(len(dirs), 5)

    def test_cache_hit_digest_mismatch_triggers_redownload(self) -> None:
        """缓存命中 + expected_digest 不符 → 删缓存重下（review #6）。"""
        import tempfile

        commit = "d1" * 20
        tar_v1 = _make_tar_bytes({"__init__.py": "X = 1\n"})
        tar_v2 = _make_tar_bytes({"__init__.py": "X = 2\n"})
        state = {"tar": tar_v1}
        requests: list[str] = []

        real_v1 = hashlib.sha256(tar_v1).hexdigest()
        real_v2 = hashlib.sha256(tar_v2).hexdigest()

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            requests.append(path)
            tar = state["tar"]
            digest = hashlib.sha256(tar).hexdigest()
            if path == f"/api/artifacts/{commit}/meta":
                return httpx.Response(200, json={
                    "commit": commit, "digest": digest,
                    "size_bytes": len(tar), "file_count": 1,
                    "built_at": "2026-09-19T00:00:00Z",
                })
            if path == f"/api/artifacts/{commit}/download":
                return httpx.Response(200, content=tar)
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), httpx.MockTransport(handler))

            # 第一次下载：落位 .digest = v1
            dest = client.ensure_artifact(commit, expected_digest=real_v1)
            self.assertEqual((dest / ".digest").read_text("utf-8"), real_v1)
            self.assertEqual((dest / "__init__.py").read_text("utf-8"), "X = 1\n")

            # 命中 + expected 一致 → 零 HTTP
            requests.clear()
            client.ensure_artifact(commit, expected_digest=real_v1)
            self.assertEqual(requests, [])

            # Platform 侧已换 v2：缓存 .digest=v1 ≠ expected=v2 → 删缓存重下
            state["tar"] = tar_v2
            requests.clear()
            dest2 = client.ensure_artifact(commit, expected_digest=real_v2)
            self.assertEqual(dest2, dest)
            self.assertEqual((dest2 / "__init__.py").read_text("utf-8"), "X = 2\n")
            self.assertEqual((dest2 / ".digest").read_text("utf-8"), real_v2)
            # 重下发生：meta + download 各一次
            self.assertEqual(requests, [
                f"/api/artifacts/{commit}/meta",
                f"/api/artifacts/{commit}/download",
            ])

    def test_cache_hit_without_sidecar_keeps_old_semantics(self) -> None:
        """外部预置的旧缓存（无 .digest）+ 带 expected → 无从比对，照旧返回。"""
        import tempfile

        commit = "d2" * 20
        requests: list[str] = []
        transport = _make_transport(commit, _make_tar_bytes({"__init__.py": "X = 1\n"}),
                                    requests=requests)
        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), transport)
            dest = client._cache_dir / commit
            dest.mkdir(parents=True)
            (dest / "__init__.py").write_text("X = 1\n", encoding="utf-8")

            result = client.ensure_artifact(commit, expected_digest="f" * 64)
            self.assertEqual(result, dest)
            self.assertEqual(requests, [])  # 未触发重下

    def test_concurrent_ensure_same_commit_downloads_once(self) -> None:
        """并发 ensure 同 commit（review #11）：per-commit 锁互斥，只下载一次。"""
        import tempfile
        import threading
        import time

        commit = "e1" * 20
        tar_bytes = _make_tar_bytes({"__init__.py": "X = 1\n"})
        downloads: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == f"/api/artifacts/{commit}/download":
                downloads.append(path)
                time.sleep(0.3)  # 拉长下载窗口，逼出并发未命中
                return httpx.Response(200, content=tar_bytes)
            if path == f"/api/artifacts/{commit}/meta":
                return httpx.Response(200, json={
                    "commit": commit, "digest": hashlib.sha256(tar_bytes).hexdigest(),
                    "size_bytes": len(tar_bytes), "file_count": 1,
                    "built_at": "2026-09-19T00:00:00Z",
                })
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), httpx.MockTransport(handler))
            results: list[Path] = []
            errors: list[BaseException] = []

            def _worker() -> None:
                try:
                    results.append(client.ensure_artifact(commit))
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=_worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(len({str(p) for p in results}), 1)  # 同一目录
            self.assertTrue((results[0] / "__init__.py").is_file())
            self.assertEqual(len(downloads), 1)  # 只下载一次

    def test_current_production(self) -> None:
        """current_production 解析 ProductionStatus；失败抛 RuntimeError。"""
        import tempfile

        tar_bytes = _make_tar_bytes({"__init__.py": "X = 1\n"})
        transport = _make_transport("c5" * 20, tar_bytes, production_commit="c5" * 20)

        with tempfile.TemporaryDirectory() as tmp:
            client = self._client(Path(tmp), transport)
            status = client.current_production()
            self.assertEqual(status.commit, "c5" * 20)
            self.assertEqual(status.version, 3)

            broken = ArtifactClient(
                "http://platform-test", Path(tmp) / "cache2",
                client=httpx.Client(base_url="http://platform-test", transport=httpx.MockTransport(
                    lambda request: httpx.Response(500),
                )),
            )
            with self.assertRaises(RuntimeError):
                broken.current_production()


if __name__ == "__main__":
    unittest.main()
