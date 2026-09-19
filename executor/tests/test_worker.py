"""worker 服务测试（Phase 7 包化后的 worker/server）。

动态加载（load_package_at）是 A/B 变体执行的关键路径——任意路径的 harness
包要能被 worker 正确加载（含 assemble 函数）。

覆盖：
- 正常加载（合法包目录 → 模块含 assemble）
- 包入口不存在 → FileNotFoundError
- 包无 assemble → RuntimeError
- worker app 骨架：health 返回状态与包路径；未注入 generate_fn 时 /generate/stream 501
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.worker.server import (
    create_worker_app,
    load_package_at,
)


_VALID_PACKAGE_INIT = '''
ASSEMBLE_CALLED = False


def assemble(ctx):
    raise NotImplementedError("仅验证可装配性，测试不真正调用")
'''


def _make_package(root: Path, init_code: str = _VALID_PACKAGE_INIT) -> Path:
    """构造一个最小合法 harness 包目录（含 __init__.py）。"""
    pkg = root / "fake_harness"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(init_code, encoding="utf-8")
    return pkg


# ── 动态加载测试 ────────────────────────────────────────────


class TestLoadPackageAt:
    def test_load_valid_package(self, tmp_path) -> None:
        """合法包目录 → 加载成功，模块含 assemble。"""
        pkg_dir = _make_package(tmp_path)
        mod = load_package_at(pkg_dir)
        assert callable(mod.assemble)

    def test_load_missing_package_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError, match="不存在"):
            load_package_at(tmp_path / "nonexistent")

    def test_load_syntax_error_raises(self, tmp_path) -> None:
        pkg_dir = _make_package(tmp_path, init_code="def broken(:\n")
        with pytest.raises(SyntaxError):
            load_package_at(pkg_dir)

    def test_load_package_without_assemble(self, tmp_path) -> None:
        """包能 import 但无 assemble：加载本身成功（run_worker 才校验 assemble）。"""
        pkg_dir = _make_package(tmp_path, init_code="X = 1\n")
        mod = load_package_at(pkg_dir)
        assert not hasattr(mod, "assemble")


# ── worker app 测试 ─────────────────────────────────────────


class TestWorkerApp:
    def test_health_endpoint(self, tmp_path) -> None:
        """health 端点返回状态/包路径/注入标记。"""
        pkg_dir = _make_package(tmp_path)
        app = create_worker_app(generate_fn=None, package_path=str(pkg_dir))
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["package_path"] == str(pkg_dir)
        assert data["generate_ready"] is False

    def test_generate_stream_returns_501_without_generate_fn(self, tmp_path) -> None:
        """未注入 generate_fn（测试/骨架环境）→ /generate/stream 返回 501。"""
        pkg_dir = _make_package(tmp_path)
        app = create_worker_app(generate_fn=None, package_path=str(pkg_dir))
        client = TestClient(app)
        resp = client.post("/generate/stream", json={
            "workspace_path": str(tmp_path / "ws"),
            "payload": {"premise": "test"},
        })
        assert resp.status_code == 501
