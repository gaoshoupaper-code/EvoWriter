"""泳道写入白名单贯通测试（REQ-20261011-142529 修复：path_guard 接入清单权限）。

根因：path_guard build 钩子未消费清单 write_permissions，恒回退旧五件套
正则（/character/*.md、/storyline.md、/worldview.md、/object/*.md、/review/*.md），
双泳道设计端写 outline/00-赛道.md 被硬拦；receipt_gate / file_state_tracker
的路径规范化同样被旧默认表卡死，拍板闸门对 outline 产物失效。

覆盖：
  - normalize：清单 glob 白名单（设计端 00-03/core、结构端 04-06/**、
    上游只读互斥、validate=False 纯规范化、穿越恒拒、旧默认表回退）
  - build 钩子：装配出的 PathGuard 白名单 = 清单 write_permissions
  - receipt_gate：拍板前 outline 产物受保护（拦截）、拍板后放行
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"


def _load_real_harness() -> None:
    """无条件清前缀后从真实 harness 目录重载（防存量串扰，同 object_contract 口径）。"""
    for k in [k for k in sys.modules if k == "harness_current" or k.startswith("harness_current.")]:
        sys.modules.pop(k, None)
    load_package(_HARNESS_DIR)


_load_real_harness()
from harness_current.middleware.path_guard import (  # noqa: E402
    FilesystemPathGuardMiddleware,
    normalize_workspace_write_path,
)
from harness_current.middleware.receipt_gate import ReceiptGateMiddleware  # noqa: E402

# 清单真实白名单（双泳道，architecture.json 为准）
_DESIGN_PERMS = (
    "/outline/00-赛道.md",
    "/outline/01-设计原则.md",
    "/outline/02-冲突引擎.md",
    "/outline/03-力量体系.md",
    "/character/core/*.md",
    "/worldview/core.md",
)
_STRUCTURE_PERMS = (
    "/outline/04-总纲.md",
    "/outline/05-卷纲/*.md",
    "/outline/06-章纲/**/*.md",
    "/outline/期待台账.md",
    "/character/support/*.md",
    "/worldview/setting.md",
    "/object/*.md",
)


class TestNormalizeWithManifestGlobs(unittest.TestCase):
    """normalize_workspace_write_path：清单 glob 模式。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_design_lane_allows_outline_and_core(self):
        for path in (
            "/outline/00-赛道.md",
            "/outline/03-力量体系.md",
            "/character/core/主角.md",
            "/worldview/core.md",
        ):
            self.assertEqual(
                normalize_workspace_write_path(path, self.root, allowed_globs=_DESIGN_PERMS),
                path,
            )

    def test_design_lane_denies_legacy_and_structure_paths(self):
        for path in ("/storyline.md", "/review/x.md", "/outline/04-总纲.md", "/object/剑.md"):
            with self.assertRaises(ValueError, msg=path):
                normalize_workspace_write_path(path, self.root, allowed_globs=_DESIGN_PERMS)

    def test_structure_lane_globstar_crosses_segments(self):
        # ** 跨段：06-章纲/卷一/第001章.md；* 不跨段：05-卷纲 直接子文件
        for path in (
            "/outline/06-章纲/卷一/第001章.md",
            "/outline/06-章纲/卷一/子目录/第002章.md",
            "/outline/05-卷纲/卷一.md",
            "/outline/期待台账.md",
            "/character/support/师父.md",
            "/object/青云剑.md",
        ):
            self.assertEqual(
                normalize_workspace_write_path(path, self.root, allowed_globs=_STRUCTURE_PERMS),
                path,
            )

    def test_structure_lane_upstream_readonly(self):
        # 上游只读（FR-002）：结构端写 00-03 / character/core / worldview core 一律拒
        for path in (
            "/outline/00-赛道.md",
            "/outline/01-设计原则.md",
            "/character/core/主角.md",
            "/worldview/core.md",
            "/storyline.md",
        ):
            with self.assertRaises(ValueError, msg=path):
                normalize_workspace_write_path(path, self.root, allowed_globs=_STRUCTURE_PERMS)

    def test_validate_false_normalizes_without_whitelist(self):
        # 纯规范化：白名单外路径也返回虚拟路径（保护面判定归调用方）
        self.assertEqual(
            normalize_workspace_write_path("outline/00-赛道.md", self.root, validate=False),
            "/outline/00-赛道.md",
        )
        self.assertEqual(
            normalize_workspace_write_path("/任意/未列入.md", self.root, validate=False),
            "/任意/未列入.md",
        )
        # 穿越恒拒（规范化模式也不放行）
        with self.assertRaises(ValueError):
            normalize_workspace_write_path("/../etc/passwd", self.root, validate=False)

    def test_fallback_to_legacy_patterns_without_globs(self):
        # 无 glob 时回退旧五件套（兼容旧清单/直构场景）
        for path in ("/character/主角.md", "/storyline.md", "/object/剑.md", "/review/x.md"):
            self.assertEqual(
                normalize_workspace_write_path(path, self.root),
                path,
            )
        with self.assertRaises(ValueError):
            normalize_workspace_write_path("/outline/00-赛道.md", self.root)


class _FakeToolRequest:
    """最小工具调用请求（path_guard 放行路径会调 request.override 回写路径）。"""

    def __init__(self, name, file_path, content="x"):
        self.tool_call = {"name": name, "id": "t", "args": {"file_path": file_path, "content": content}}
        self.overridden = None

    def override(self, **kwargs):
        self.overridden = kwargs.get("tool_call")
        return self


class TestBuildHookConsumesManifest(unittest.TestCase):
    """build 钩子：装配出的 PathGuard 白名单 = 清单 write_permissions。"""

    def _build(self, write_permissions):
        from contracts.architecture_manifest import AgentSpec

        agent = AgentSpec(
            name="storydesign",
            role="main",
            description="测试",
            prompt="prompts/storydesign_system.md",
            write_permissions=list(write_permissions),
        )
        abc = SimpleNamespace(
            agent=agent, workspace_path=Path(tempfile.mkdtemp()),
            intervention_callback=None,
        )
        import harness_current.middleware.path_guard as pg

        return pg.build(abc)

    def test_build_passes_manifest_globs(self):
        guard = self._build(_DESIGN_PERMS)
        self.assertEqual(guard.allowed_globs, _DESIGN_PERMS)
        # 白名单内放行（回写规范化路径而非拦截）、白名单外拒绝
        # （含旧表路径——清单是唯一真相源）
        req_ok = _FakeToolRequest("write_file", "/outline/00-赛道.md")
        self.assertNotIsInstance(guard._guard_request(req_ok), ToolMessage)
        self.assertEqual(req_ok.overridden["args"]["file_path"], "/outline/00-赛道.md")
        req_bad = _FakeToolRequest("write_file", "/storyline.md")
        self.assertIsInstance(guard._guard_request(req_bad), ToolMessage)

    def test_build_falls_back_without_permissions(self):
        guard = self._build(())
        self.assertIsNone(guard.allowed_globs)
        # 旧默认表放行 /storyline.md（回写 request 而非拦截）
        req = _FakeToolRequest("write_file", "/storyline.md")
        self.assertNotIsInstance(guard._guard_request(req), ToolMessage)


class TestReceiptGateProtectsOutline(unittest.TestCase):
    """拍板闸门：outline 产物在拍板前被拦（规范化不再被旧默认表卡死）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write_request(self, path):
        return SimpleNamespace(tool_call={
            "name": "write_file", "id": "t1",
            "args": {"file_path": path, "content": "# 赛道"},
        })

    def test_outline_blocked_before_confirmation(self):
        gate = ReceiptGateMiddleware(self.root, protected_globs=list(_DESIGN_PERMS))
        blocked = gate._maybe_block(self._write_request("/outline/00-赛道.md"))
        self.assertIsNotNone(blocked, "拍板前写 outline/00-赛道.md 应被闸门拦截")

    def test_outline_allowed_after_confirmation(self):
        gate = ReceiptGateMiddleware(self.root, protected_globs=list(_DESIGN_PERMS))
        gate._confirmed = True
        self.assertIsNone(
            gate._maybe_block(self._write_request("/outline/00-赛道.md")),
            "拍板后写 outline/00-赛道.md 应放行",
        )

    def test_legacy_paths_still_protected(self):
        gate = ReceiptGateMiddleware(self.root)  # 缺省保护面 = 旧五件套
        self.assertIsNotNone(gate._maybe_block(self._write_request("/storyline.md")))


if __name__ == "__main__":
    unittest.main()
