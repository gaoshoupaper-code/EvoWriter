"""M3 风格注入闭环测试（D3=③：新行为补针对性测试）。

验证三环：
1. apply_style_suffix 纯函数：有 suffix 追加、无 suffix 原样。
2. RuntimeContext.styles 字段：可设置、可读取。
3. assemble 消费 styles：带 styles 的 ctx → assemble 不报错（验证 import 链 + styles 被读取）。

注：完整 assemble 需要真实 model/backend/checkpointer，本测试不跑完整装配，
只验证"风格数据能正确流到注入点"。subagent prompt 含 suffix 的端到端验证
依赖真实 LLM 调用，留手动验证。
"""
from __future__ import annotations

import unittest

from contracts.runtime_context import RuntimeContext


class TestApplyStyleSuffix(unittest.TestCase):
    """风格 SUFFIX 注入逻辑（M2 清单架构下随 subagents/types.py 退役，
    落进清单解释器 _system_prompt——按 manifest agent 名取 styles）。"""

    def setUp(self):
        from pathlib import Path

        from app.platform.agent.loader import load_package
        from contracts.architecture_manifest import load_architecture_manifest

        harness_dir = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
        load_package(harness_dir)
        self.pkg_dir = harness_dir
        self.manifest = load_architecture_manifest(harness_dir)

    def _prompt(self, styles):
        from pathlib import Path

        from app.platform.agent.architecture import AgentBuildCtx, _system_prompt
        from contracts.runtime_context import RuntimeContext

        ctx = RuntimeContext(
            model=object(), backend=object(), checkpointer=None,
            workspace_path=Path("/tmp"), styles=styles,
        )
        agent = self.manifest.agent("storybuilding")
        abc = AgentBuildCtx(ctx, self.manifest, agent, "harness_current", self.pkg_dir)
        return _system_prompt(abc)

    def test_no_suffix_returns_original(self):
        """无 suffix（styles=None）应返回 prompt 原文。"""
        body = (self.pkg_dir / "prompts" / "storybuilding_system.md").read_text(
            encoding="utf-8"
        ).strip()
        self.assertEqual(self._prompt(None), body)

    def test_empty_suffix_returns_original(self):
        """空字符串 suffix 应原样返回（falsy 值短路不追加）。"""
        body = (self.pkg_dir / "prompts" / "storybuilding_system.md").read_text(
            encoding="utf-8"
        ).strip()
        self.assertEqual(self._prompt({"storybuilding": ""}), body)

    def test_suffix_appended(self):
        """有 suffix 应按 manifest agent 名取值并追加到 prompt 末尾（两换行分隔）。"""
        result = self._prompt({"storybuilding": "风格：简洁有力。"})
        self.assertTrue(result.endswith("\n\n风格：简洁有力。"))


class TestRuntimeContextStyles(unittest.TestCase):
    """验证 RuntimeContext.styles 字段（contracts 层）。"""

    def test_styles_defaults_none(self):
        """styles 默认 None（无风格注入）。"""
        from pathlib import Path
        ctx = RuntimeContext(
            model=object(), backend=object(), checkpointer=object(),
            workspace_path=Path("/tmp"),
        )
        self.assertIsNone(ctx.styles)

    def test_styles_can_be_set(self):
        """styles 可设置 scope→suffix 映射。"""
        from pathlib import Path
        ctx = RuntimeContext(
            model=object(), backend=object(), checkpointer=object(),
            workspace_path=Path("/tmp"),
            styles={"meta": "全局风格", "writing": "写作风格"},
        )
        self.assertEqual(ctx.styles["meta"], "全局风格")
        self.assertEqual(ctx.styles["writing"], "写作风格")

    def test_styles_scope_key_names(self):
        """D2 决策：key 用包内 scope 名（含 detail-outline 连字符）。"""
        from pathlib import Path
        ctx = RuntimeContext(
            model=object(), backend=object(), checkpointer=object(),
            workspace_path=Path("/tmp"),
            styles={"detail-outline": "细纲风格"},
        )
        self.assertIn("detail-outline", ctx.styles)


if __name__ == "__main__":
    unittest.main()
