"""path_guard 白名单测试（lane 语义随三步制退役，REQ-20261018-115508 收敛）。

path_guard 仍在基础中间件链（BASE_CHAIN_MODULES）服务通用文件工具的
写路径规范化与白名单。五段制下 author 的内置文件工具被全拒（路径收权），
path_guard 是纵深防御层；回执闸（receipt_gate）已随单 agent 拍板流退役，
接替者为 stage_gate（见 test_writing_harness_tools.py）。

锁定：
  - normalize_workspace_write_path：glob 白名单语义、穿越恒拒、无 glob 回退。
  - build 钩子：装配出的白名单 = 清单 write_permissions。
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

# 通用测试白名单（glob 语义与清单无关；path_guard 是领域无关防御层）
_GENERIC_PERMS = (
    "/总纲.md",
    "/卷纲/*.md",
    "/人物/*.md",
)


class TestNormalizeWithGlobs(unittest.TestCase):
    """normalize_workspace_write_path：清单 glob 模式。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_allows_whitelisted_paths(self):
        for path in (
            "/总纲.md",
            "/卷纲/第01卷.md",
            "/卷纲/第12卷.md",
            "/人物/主角.md",
        ):
            self.assertEqual(
                normalize_workspace_write_path(path, self.root, allowed_globs=_GENERIC_PERMS),
                path,
            )

    def test_denies_non_whitelisted_paths(self):
        for path in (
            "/主题.md",
            "/章纲/第001章.md",
            "/storyline.md",
            "/etc/passwd-ish",
        ):
            with self.assertRaises(ValueError, msg=path):
                normalize_workspace_write_path(path, self.root, allowed_globs=_GENERIC_PERMS)

    def test_validate_false_normalizes_without_whitelist(self):
        # 纯规范化：白名单外路径也返回虚拟路径（保护面判定归调用方）
        self.assertEqual(
            normalize_workspace_write_path("主题.md", self.root, validate=False),
            "/主题.md",
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
            normalize_workspace_write_path("/总纲.md", self.root)


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
            name="guard-test",
            role="sub",
            delegated_by="author",
            description="测试",
            prompt="prompts/author_base.md",
            write_permissions=list(write_permissions),
        )
        abc = SimpleNamespace(
            agent=agent, workspace_path=Path(tempfile.mkdtemp()),
            intervention_callback=None,
        )
        import harness_current.middleware.path_guard as pg

        return pg.build(abc)

    def test_build_passes_manifest_globs(self):
        guard = self._build(_GENERIC_PERMS)
        self.assertEqual(guard.allowed_globs, _GENERIC_PERMS)
        # 白名单内放行（回写规范化路径而非拦截）、白名单外拒绝
        req_ok = _FakeToolRequest("write_file", "/总纲.md")
        self.assertNotIsInstance(guard._guard_request(req_ok), ToolMessage)
        self.assertEqual(req_ok.overridden["args"]["file_path"], "/总纲.md")
        req_bad = _FakeToolRequest("write_file", "/主题.md")
        self.assertIsInstance(guard._guard_request(req_bad), ToolMessage)

    def test_build_falls_back_without_permissions(self):
        guard = self._build(())
        self.assertIsNone(guard.allowed_globs)
        # 旧默认表放行 /storyline.md（回写 request 而非拦截）
        req = _FakeToolRequest("write_file", "/storyline.md")
        self.assertNotIsInstance(guard._guard_request(req), ToolMessage)


if __name__ == "__main__":
    unittest.main()
