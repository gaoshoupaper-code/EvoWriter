"""双泳道 harness 包验收测试（REQ-20261011-142529 FR-013/AC-010/AC-011）。

跑法（在 executor 目录）：python -m pytest tests/test_dual_lane_package.py -v

覆盖（取代 v7 单故事专家契约）：
  - 包内容形态：storydesign/storystructure 双主泳道资产齐备；
    旧单泳道 storybuilding 资产（prompts/skills/中间件）已清理（DEC-011）
  - registry：本地镜像 sanity（production ≤ 已登记最大版本；v9 断言为
    存量失败——HEAD 的 registry 镜像只登记 6-8，账本随 promote 同步）
  - assemble：缺省装配 = 首个 main（storydesign）；按名装配 storystructure；
    checkpointer 透传到顶层 create_deep_agent（会话持久化依赖）
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"


class _StubModel(BaseChatModel):
    """最小可装配模型：bind_tools 返回自身（create_deep_agent 编译期会调用）。"""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: D102
        return AIMessage(content="stub")

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "stub"


class _StubRecorder:
    """收集装配事实（skill catalog / middleware stack），供断言装配形态。"""

    def __init__(self) -> None:
        self.catalogs: list[tuple[str, list[str]]] = []
        self.stacks: list[str] = []

    def record_skill_catalog(self, trace_id, agent_name, paths, sources):
        self.catalogs.append((agent_name, list(paths)))

    def record_middleware_assembly(self, trace_id, agent_name, middleware):
        self.stacks.append(agent_name)

    def append_event(self, *args, **kwargs):
        pass


# 旧单泳道资产（DEC-011 清理）：存在即失败
_FORBIDDEN_FILES = [
    "prompts/storybuilding_system.md",
    "prompts/storybuilding_review.md",
    "skills/storybuilding-initial/SKILL.md",
    "skills/storybuilding-specs/SKILL.md",
    "skills/storybuilding-expand-storyline/SKILL.md",
    "skills/storybuilding-expand-character/SKILL.md",
    "middleware/skill_activation_guard.py",
    "middleware/review_gate.py",
    "middleware/quota_convergence.py",
    "middleware/storyline_contract_guard.py",
    "middleware/hooks_contract_guard.py",
    "middleware/pacing_report.py",
    "middleware/storyline_single_line_limit.py",
    "middleware/storyline_integrity.py",
    "tools/increment_batch.py",
]
# 双泳道资产：缺失即失败
_REQUIRED_FILES = [
    "architecture.json",
    "prompts/storydesign_system.md",
    "prompts/storystructure_system.md",
    "prompts/demand_template.md",
    "middleware/receipt_gate.py",
    "middleware/outline_contract_guard.py",
    "middleware/object_contract_guard.py",
    "middleware/checkup_config.json",
    "tools/confirm_with_user.py",
    "tools/submit_rework_ticket.py",
    "checkup.py",
]
# DEC-003 冻结保留的记忆要素：删除即失败
_KEPT_MEMORY_FILES = [
    "tools/narrative_schema.py",
    "tools/query_builder.py",
    "tools/join_rules.py",
    "tools/packet_formatter.py",
    "middleware/memory_recall_middleware.py",
    "prompts/memory_extraction_guide.md",
]


class TestDualLanePackageContent(unittest.TestCase):
    def test_old_single_lane_assets_deleted(self):
        for rel in _FORBIDDEN_FILES:
            self.assertFalse(
                (_HARNESS_DIR / rel).exists(),
                f"旧单泳道资产应已清理（DEC-011）: {rel}",
            )

    def test_dual_lane_assets_kept(self):
        for rel in _REQUIRED_FILES:
            self.assertTrue((_HARNESS_DIR / rel).exists(), f"双泳道资产应存在: {rel}")

    def test_memory_elements_kept_frozen(self):
        for rel in _KEPT_MEMORY_FILES:
            self.assertTrue((_HARNESS_DIR / rel).exists(), f"记忆要素按 DEC-003 冻结保留: {rel}")

    def test_manifest_declares_dual_mains(self):
        from contracts.architecture_manifest import load_and_validate

        manifest, result = load_and_validate(_HARNESS_DIR)
        self.assertTrue(result.ok, result.errors)
        mains = [a.name for a in manifest.main_agents]
        self.assertEqual(mains, ["storydesign", "storystructure"])
        design = manifest.agent("storydesign")
        structure = manifest.agent("storystructure")
        # 上游只读（FR-002）：structure 白名单不含 00-03 / character/core / worldview core
        for forbidden in ("/outline/00-赛道.md", "/character/core/*.md", "/worldview/core.md"):
            self.assertNotIn(forbidden, structure.write_permissions)
            self.assertIn(forbidden, design.write_permissions)
        # 回炉工具只挂 structure（FR-004）
        self.assertIn("submit_rework_ticket", structure.tools)
        self.assertNotIn("submit_rework_ticket", design.tools)

    def test_registry_mirror_sanity(self):
        """本地 registry 镜像 sanity：production 指向已登记版本（账本随 promote 同步，
        FR-013；旧 v9 断言为存量失败，已随本测试重写移除）。"""
        registry = json.loads((_HARNESS_DIR / "registry.json").read_text(encoding="utf-8"))
        versions = [v["version"] for v in registry["versions"]]
        self.assertTrue(versions, "registry 至少登记一个版本")
        self.assertLessEqual(registry["production"], max(versions))


class TestDualLaneAssemble(unittest.TestCase):
    def _load_pkg(self):
        from app.platform.agent.loader import load_package

        return load_package(_HARNESS_DIR)

    def _make_ctx(self, tmpdir: str, recorder, checkpointer=None):
        from contracts.runtime_context import RuntimeContext

        return RuntimeContext(
            model=_StubModel(),
            backend=object(),
            checkpointer=checkpointer,
            workspace_path=Path(tmpdir),
            trace_id="trace-dual-lane-test",
            trace_recorder=recorder,
            trace_middleware_cls=None,
        )

    def test_assemble_default_and_named_lanes(self):
        import tempfile

        pkg = self._load_pkg()
        recorder = _StubRecorder()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "demand.md").write_text(
                "<!--\n元信息：\n- status: confirmed\n- mode: interactive\n-->\n# 需求\n玄幻测试",
                encoding="utf-8",
            )
            ctx = self._make_ctx(tmp, recorder)
            default = pkg.assemble(ctx)
            self.assertTrue(callable(getattr(default, "ainvoke", None)), "缺省装配应返回可执行图")
            named = pkg.assemble(ctx, agent_name="storystructure")
            self.assertTrue(callable(getattr(named, "ainvoke", None)), "按名装配 storystructure 应返回可执行图")
            with self.assertRaises(Exception):
                pkg.assemble(ctx, agent_name="storybuilding")  # 已退役泳道名 → ManifestError

        # 无技能泳道记中间件栈（trace V2 口径）
        self.assertIn("storydesign-subagent", recorder.stacks)
        self.assertIn("storystructure-subagent", recorder.stacks)
        self.assertEqual(recorder.catalogs, [], "双泳道无技能目录（方法论在主文档）")

    def test_interpreter_passes_checkpointer_to_top_level(self):
        """顶层装配必须透传 checkpointer（会话/断点持久化依赖，FR-003）。"""
        import tempfile

        from langgraph.checkpoint.memory import InMemorySaver

        pkg = self._load_pkg()
        captured: dict[str, Any] = {}

        import app.platform.agent.architecture as arch_mod

        original = arch_mod.create_deep_agent
        try:

            def _capture(**kwargs):
                captured.update(kwargs)
                return original(**kwargs)

            arch_mod.create_deep_agent = _capture
            sentinel = InMemorySaver()
            recorder = _StubRecorder()
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "demand.md").write_text(
                    "<!--\n- status: confirmed\n-->\n玄幻", encoding="utf-8"
                )
                ctx = self._make_ctx(tmp, recorder, checkpointer=sentinel)
                pkg.assemble(ctx)
        finally:
            arch_mod.create_deep_agent = original

        self.assertIs(captured.get("checkpointer"), sentinel)


if __name__ == "__main__":
    unittest.main()
