"""三步子代理写入白名单贯通测试（REQ-20261015-161200 FR-002/006）。

覆盖：
  - normalize：清单 glob 白名单（worldview/outline/chapters 三子代理、
    跨步骤只读互斥、validate=False 纯规范化、穿越恒拒、旧默认表回退）
  - build 钩子：装配出的 PathGuard 白名单 = 清单 write_permissions
  - receipt_gate：拍板前 outline 产物受保护（拦截）、拍板后放行；
    主控与子代理经共享确认位联动（TD-001）
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
    """无条件清前缀后从真实 harness 目录重载（防存量串扰）。"""
    for k in [k for k in sys.modules if k == "harness_current" or k.startswith("harness_current.")]:
        sys.modules.pop(k, None)
    load_package(_HARNESS_DIR)


_load_real_harness()
from harness_current.middleware.path_guard import (  # noqa: E402
    normalize_workspace_write_path,
)
from harness_current.middleware.receipt_gate import (  # noqa: E402
    ReceiptGateMiddleware,
    SharedConfirmState,
)

# 清单真实白名单（三步子代理，architecture.json 为准）
_WORLDVIEW_PERMS = ("/outline/世界观.md",)
_OUTLINE_PERMS = (
    "/outline/总纲.md",
    "/outline/卷纲-*.md",
    "/character/core/*.md",
)
_CHAPTERS_PERMS = (
    "/outline/章纲-*.md",
    "/outline/期待台账.md",
    "/character/support/*.md",
)


class TestNormalizeWithManifestGlobs(unittest.TestCase):
    """normalize_workspace_write_path：清单 glob 模式。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_outline_lane_allows_master_volume_core(self):
        for path in (
            "/outline/总纲.md",
            "/outline/卷纲-第1卷.md",
            "/outline/卷纲-第12卷.md",
            "/character/core/主角.md",
        ):
            self.assertEqual(
                normalize_workspace_write_path(path, self.root, allowed_globs=_OUTLINE_PERMS),
                path,
            )

    def test_outline_lane_denies_other_steps(self):
        for path in (
            "/outline/世界观.md",
            "/outline/章纲-卷1批1.md",
            "/outline/期待台账.md",
            "/character/support/小翠.md",
            "/storyline.md",
        ):
            with self.assertRaises(ValueError, msg=path):
                normalize_workspace_write_path(path, self.root, allowed_globs=_OUTLINE_PERMS)

    def test_chapters_lane_allows_batch_ledger_support(self):
        for path in (
            "/outline/章纲-卷1批1.md",
            "/outline/章纲-卷10批23.md",
            "/outline/期待台账.md",
            "/character/support/王执事.md",
        ):
            self.assertEqual(
                normalize_workspace_write_path(path, self.root, allowed_globs=_CHAPTERS_PERMS),
                path,
            )

    def test_chapters_lane_upstream_readonly(self):
        # 上游只读（FR-006）：章纲子代理写世界观/总纲/卷纲/core 一律拒
        for path in (
            "/outline/世界观.md",
            "/outline/总纲.md",
            "/outline/卷纲-第2卷.md",
            "/character/core/主角.md",
        ):
            with self.assertRaises(ValueError, msg=path):
                normalize_workspace_write_path(path, self.root, allowed_globs=_CHAPTERS_PERMS)

    def test_worldview_lane_single_file(self):
        self.assertEqual(
            normalize_workspace_write_path("/outline/世界观.md", self.root, allowed_globs=_WORLDVIEW_PERMS),
            "/outline/世界观.md",
        )
        for path in ("/outline/总纲.md", "/outline/章纲-卷1批1.md"):
            with self.assertRaises(ValueError, msg=path):
                normalize_workspace_write_path(path, self.root, allowed_globs=_WORLDVIEW_PERMS)

    def test_validate_false_normalizes_without_whitelist(self):
        # 纯规范化：白名单外路径也返回虚拟路径（保护面判定归调用方）
        self.assertEqual(
            normalize_workspace_write_path("outline/世界观.md", self.root, validate=False),
            "/outline/世界观.md",
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
            normalize_workspace_write_path("/outline/世界观.md", self.root)


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
            name="outline",
            role="sub",
            delegated_by="writer",
            description="测试",
            prompt="prompts/outline_system.md",
            write_permissions=list(write_permissions),
        )
        abc = SimpleNamespace(
            agent=agent, workspace_path=Path(tempfile.mkdtemp()),
            intervention_callback=None,
        )
        import harness_current.middleware.path_guard as pg

        return pg.build(abc)

    def test_build_passes_manifest_globs(self):
        guard = self._build(_OUTLINE_PERMS)
        self.assertEqual(guard.allowed_globs, _OUTLINE_PERMS)
        # 白名单内放行（回写规范化路径而非拦截）、白名单外拒绝
        req_ok = _FakeToolRequest("write_file", "/outline/总纲.md")
        self.assertNotIsInstance(guard._guard_request(req_ok), ToolMessage)
        self.assertEqual(req_ok.overridden["args"]["file_path"], "/outline/总纲.md")
        req_bad = _FakeToolRequest("write_file", "/outline/世界观.md")
        self.assertIsInstance(guard._guard_request(req_bad), ToolMessage)

    def test_build_falls_back_without_permissions(self):
        guard = self._build(())
        self.assertIsNone(guard.allowed_globs)
        # 旧默认表放行 /storyline.md（回写 request 而非拦截）
        req = _FakeToolRequest("write_file", "/storyline.md")
        self.assertNotIsInstance(guard._guard_request(req), ToolMessage)


class TestReceiptGateSharedConfirm(unittest.TestCase):
    """拍板闸门：共享确认位（主控记账、子代理只读，TD-001）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write_request(self, path):
        return SimpleNamespace(tool_call={
            "name": "write_file", "id": "t1",
            "args": {"file_path": path, "content": "# 世界观"},
        })

    def _confirm_request(self, answer="同意"):
        return SimpleNamespace(tool_call={
            "name": "confirm_with_user", "id": "t2",
            "args": {"question": "草稿全文", "options": []},
        })

    def test_subagent_blocked_before_owner_confirm(self):
        shared = SharedConfirmState()
        owner = ReceiptGateMiddleware(self.root, protected_globs=[], shared=shared, owner=True)
        follower = ReceiptGateMiddleware(self.root, protected_globs=list(_WORLDVIEW_PERMS), shared=shared)
        blocked = follower._maybe_block(self._write_request("/outline/世界观.md"))
        self.assertIsNotNone(blocked, "主控拍板前，子代理写世界观应被拦截")

    def test_owner_confirm_releases_follower_writes(self):
        shared = SharedConfirmState()
        owner = ReceiptGateMiddleware(self.root, protected_globs=[], shared=shared, owner=True)
        follower = ReceiptGateMiddleware(self.root, protected_globs=list(_WORLDVIEW_PERMS), shared=shared)

        def _handler(request):
            return "同意，按此落盘"

        owner.wrap_tool_call(self._confirm_request(), _handler)
        self.assertTrue(shared.confirmed, "owner 记账后共享确认位应置位")
        self.assertIsNone(
            follower._maybe_block(self._write_request("/outline/世界观.md")),
            "主控拍板后，子代理写世界观应放行",
        )

    def test_follower_does_not_record_confirm(self):
        shared = SharedConfirmState()
        follower = ReceiptGateMiddleware(self.root, protected_globs=list(_WORLDVIEW_PERMS), shared=shared)

        def _handler(request):
            return "同意"

        follower.wrap_tool_call(self._confirm_request(), _handler)
        self.assertFalse(shared.confirmed, "follower 不持有 confirm 工具，记账无效")

    def test_owner_before_agent_resets_shared_state(self):
        shared = SharedConfirmState()
        owner = ReceiptGateMiddleware(self.root, protected_globs=[], shared=shared, owner=True)
        shared.confirmed = True
        owner.before_agent(None, None)
        self.assertFalse(shared.confirmed, "新一轮运行开始时 owner 重置共享确认位")

    def test_follower_before_agent_keeps_shared_state(self):
        shared = SharedConfirmState()
        follower = ReceiptGateMiddleware(self.root, protected_globs=list(_WORLDVIEW_PERMS), shared=shared)
        shared.confirmed = True
        follower.before_agent(None, None)
        self.assertTrue(shared.confirmed, "follower 的 before_agent 不得重置共享确认位（resume 后落盘依赖）")

    def test_legacy_paths_still_protected_by_default(self):
        gate = ReceiptGateMiddleware(self.root)  # 缺省保护面 = 旧五件套
        self.assertIsNotNone(gate._maybe_block(self._write_request("/storyline.md")))


if __name__ == "__main__":
    unittest.main()
