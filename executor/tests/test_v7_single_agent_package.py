"""v7 单故事专家 harness 包验收测试（REQ-20260920-150149 FR-101/AC-101）。

跑法（在 executor 目录）：
    .venv/Scripts/python.exe -m pytest tests/test_v7_single_agent_package.py -v

覆盖：
  - 包内容形态：多 Agent 要素文件已删除（meta/interview/detail_outline/writing）
  - registry：v7 已登记（parent=6），production 仍指 6（DEC-005：P1 不切生产）
  - assemble：产物为单故事专家 + reviewer；技能目录只含 storybuilding 两技能；
    checkpointer 透传到顶层 create_deep_agent（P2 修订对话依赖线程持久化）
"""
from __future__ import annotations

import json
import sys
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


# 已删除的多 Agent 要素文件（FR-101）：存在即失败
_FORBIDDEN_FILES = [
    "prompts/meta_system.md",
    "prompts/interview_system.md",
    "prompts/detail_outline_system.md",
    "prompts/detail_outline_review.md",
    "prompts/writing_system.md",
    "prompts/writing_review.md",
    "subagents/interview.py",
    "subagents/detail_outline.py",
    "subagents/writing.py",
    "subagents/reviewers/detail_outline.py",
    "subagents/reviewers/writing.py",
    "middleware/meta_readonly.py",
    "middleware/storybuilding_iteration_limit.py",
    "middleware/demand_preload.py",
]
# 已删除的多 Agent 技能目录（FR-101）
_FORBIDDEN_DIRS = [
    "skills/detail_outline",
    "skills/meta",
    "skills/writing",
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


class TestV7PackageContent(unittest.TestCase):
    def test_multi_agent_files_deleted(self):
        for rel in _FORBIDDEN_FILES:
            self.assertFalse(
                (_HARNESS_DIR / rel).exists(),
                f"多 Agent 要素文件应已删除: {rel}",
            )

    def test_multi_agent_skill_dirs_deleted(self):
        for rel in _FORBIDDEN_DIRS:
            self.assertFalse(
                (_HARNESS_DIR / rel).exists(),
                f"多 Agent 技能目录应已删除: {rel}",
            )

    def test_memory_elements_kept_frozen(self):
        for rel in _KEPT_MEMORY_FILES:
            self.assertTrue(
                (_HARNESS_DIR / rel).exists(),
                f"记忆要素按 DEC-003 冻结保留: {rel}",
            )

    def test_story_expert_assets_kept(self):
        for rel in [
            "prompts/storybuilding_system.md",
            "prompts/storybuilding_review.md",
            "prompts/demand_template.md",
            "subagents/storybuilding.py",
            "subagents/factory.py",
            "subagents/types.py",
            "subagents/reviewers/storybuilding.py",
            "skills/storybuilding-initial/SKILL.md",
            "skills/storybuilding-expand/SKILL.md",
        ]:
            self.assertTrue((_HARNESS_DIR / rel).exists(), f"故事专家资产应保留: {rel}")

    def test_registry_v7_registered_production_stays_v6(self):
        registry = json.loads((_HARNESS_DIR / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(registry["production"], 6, "DEC-005：P1 不切生产指针")
        v7 = next((v for v in registry["versions"] if v["version"] == 7), None)
        self.assertIsNotNone(v7, "v7 应已登记")
        self.assertEqual(v7["parent_version"], 6)


class TestV7Assemble(unittest.TestCase):
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
            trace_id="trace-v7-test",
            trace_recorder=recorder,
            trace_middleware_cls=None,
        )

    def test_assemble_single_story_expert_shape(self):
        import tempfile

        pkg = self._load_pkg()
        recorder = _StubRecorder()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "demand.md").write_text(
                "<!--\n元信息：\n- status: confirmed\n- mode: auto\n-->\n# 需求\n玄幻测试",
                encoding="utf-8",
            )
            ctx = self._make_ctx(tmp, recorder)
            agent = pkg.assemble(ctx)
            self.assertTrue(callable(getattr(agent, "ainvoke", None)), "assemble 应返回可执行图")

        # 技能目录：只含 storybuilding 两技能（无 meta/detail_outline/writing）
        all_paths = [p for _, paths in recorder.catalogs for p in paths]
        self.assertTrue(all_paths, "应记录技能目录")
        for p in all_paths:
            self.assertIn(
                "storybuilding", str(p).replace("\\", "/"),
                f"只允许故事专家技能，出现: {p}",
            )

    def test_factory_passes_checkpointer_to_top_level(self):
        """顶层装配必须透传 checkpointer（P2 修订对话的线程持久化依赖）。"""
        import tempfile

        from langgraph.checkpoint.memory import InMemorySaver

        pkg = self._load_pkg()
        captured: dict[str, Any] = {}

        # 直接 patch 工厂模块内的函数绑定（build_deep_subagent 从这里取
        # create_deep_agent），避免依赖 runtime 共享模块的 patch 时机
        import harness_current.subagents.factory as factory_mod

        original = factory_mod.create_deep_agent
        try:

            def _capture(**kwargs):
                captured.update(kwargs)
                return original(**kwargs)

            factory_mod.create_deep_agent = _capture
            sentinel = InMemorySaver()
            recorder = _StubRecorder()
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "demand.md").write_text(
                    "<!--\n- status: confirmed\n-->\n玄幻", encoding="utf-8"
                )
                ctx = self._make_ctx(tmp, recorder, checkpointer=sentinel)
                pkg.assemble(ctx)
        finally:
            factory_mod.create_deep_agent = original

        self.assertIs(captured.get("checkpointer"), sentinel)


if __name__ == "__main__":
    unittest.main()
