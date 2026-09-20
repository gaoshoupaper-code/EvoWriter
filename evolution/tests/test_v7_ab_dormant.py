"""A/B 休眠验收测试（REQ-20260920-150149 FR-105/AC-105）。

跑法（evolution 目录）：python -m pytest tests/test_v7_ab_dormant.py -v

休眠语义（休眠不拆，版本对比移交 benchmark）：
  1. A/B 对比链入口停用：improvement 实验路由（/api/experiments*）不在活路由表
  2. 版本对比能力由 benchmark 承接：/api/benchmark 路由在位
  3. 执行载体保留：/api/tests（POST，内部调 executor /internal/ab/run）在位
     ——benchmark 跑分与手动测试都以它为执行通道，休眠不得误伤
  4. run_experiment 无活调用方（不被任何已挂载模块引用）
"""
from __future__ import annotations

import unittest
from pathlib import Path


def _live_routes() -> set[str]:
    from app.main import app

    return {getattr(r, "path", "") for r in app.routes}


class TestAbDormant(unittest.TestCase):
    def test_ab_experiment_routes_not_mounted(self):
        routes = _live_routes()
        ab_routes = [r for r in routes if "experiment" in r]
        self.assertEqual(
            ab_routes, [],
            f"A/B 实验入口应停用（未挂载），实际存在: {ab_routes}",
        )

    def test_benchmark_comparison_mounted(self):
        routes = _live_routes()
        self.assertTrue(
            any(r.startswith("/api/benchmark") for r in routes),
            "benchmark 版本对比能力应在位（承接 A/B 的对比语义）",
        )

    def test_execution_vehicle_alive(self):
        """/api/tests POST 是执行载体（调 executor /internal/ab/run），必须保留。"""
        from app.main import app

        test_routes = [
            (getattr(r, "path", ""), set(getattr(r, "methods", set()) or set()))
            for r in app.routes
            if getattr(r, "path", "").startswith("/api/tests")
        ]
        self.assertTrue(
            any("POST" in methods for _, methods in test_routes),
            f"执行载体 /api/tests POST 应在位，实际: {test_routes}",
        )

    def test_run_experiment_has_no_live_caller(self):
        """run_experiment 的宿主模块均未挂载（improvement 链 + 旧 evaluation 视图）。"""
        from app.main import app

        endpoint_modules = {
            getattr(getattr(r, "endpoint", None), "__module__", "")
            for r in app.routes
        }
        live_ab_modules = [
            m for m in endpoint_modules
            if m.startswith("app.improvement") or m == "app.view.evaluation_api"
        ]
        self.assertEqual(
            live_ab_modules, [],
            f"A/B 链宿主模块不应挂载，实际出现在活路由: {live_ab_modules}",
        )

        # 源码层复核：run_experiment 的引用只允许出现在休眠模块内部
        allowed_files = {"app/improvement/experiment.py", "app/view/evaluation_api.py"}
        offenders = []
        for py in Path("app").rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            rel = py.as_posix()
            if rel in allowed_files:
                continue
            if "run_experiment" in py.read_text(encoding="utf-8"):
                offenders.append(rel)
        self.assertEqual(offenders, [], f"run_experiment 出现在非休眠模块: {offenders}")


if __name__ == "__main__":
    unittest.main()
