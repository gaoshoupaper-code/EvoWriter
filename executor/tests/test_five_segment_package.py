"""五段制 harness 包验收测试（REQ-20261018-115508 FR-001/003/006，AC-005/008）。

跑法（在 executor 目录）：python -m pytest tests/test_five_segment_package.py -v

覆盖（取代三步制契约测试）：
  - 包内容形态：author 单 agent 资产齐备（常驻骨架 + 五份段规程 + 阶段闸 +
    语义工具七件套）；三步制资产（writer/worldview/outline/chapters 提示词、
    demand 模板、回执闸、outline 契约闸）已清理
  - registry：本地镜像 sanity（production ≤ 已登记最大版本）
  - assemble：缺省装配 = 唯一 main（author）；按名装配 author；退役名 writer 报错
  - checkpointer 透传到顶层 create_deep_agent（会话持久化依赖）
"""
from __future__ import annotations

import json
import tempfile
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


# 三步制资产（本次清理）：存在即失败
_FORBIDDEN_FILES = [
    "prompts/writer_system.md",
    "prompts/worldview_system.md",
    "prompts/outline_system.md",
    "prompts/chapters_system.md",
    "prompts/demand_template.md",
    "middleware/receipt_gate.py",
    "middleware/outline_contract_guard.py",
]
# 五段制资产：缺失即失败
_REQUIRED_FILES = [
    "architecture.json",
    "prompts/author_base.md",
    "prompts/stage_theme.md",
    "prompts/stage_zonggang.md",
    "prompts/stage_juangang.md",
    "prompts/stage_zhanggang.md",
    "prompts/stage_zhengwen.md",
    "middleware/stage_gate.py",
    "tools/read_artifact.py",
    "tools/save_artifact.py",
    "tools/update_foreshadow_table.py",
    "tools/rewrite_rhythm_table.py",
    "tools/append_chapter_deviation.py",
    "tools/confirm_with_user.py",
]
# 记忆要素冻结保留：删除即失败
_KEPT_MEMORY_FILES = [
    "tools/narrative_schema.py",
    "tools/query_builder.py",
    "tools/join_rules.py",
    "tools/packet_formatter.py",
    "middleware/memory_recall_middleware.py",
    "prompts/memory_extraction_guide.md",
]


class TestFiveSegmentPackageContent(unittest.TestCase):
    def test_three_step_assets_deleted(self):
        for rel in _FORBIDDEN_FILES:
            self.assertFalse(
                (_HARNESS_DIR / rel).exists(),
                f"三步制资产应已清理: {rel}",
            )

    def test_five_segment_assets_kept(self):
        for rel in _REQUIRED_FILES:
            self.assertTrue((_HARNESS_DIR / rel).exists(), f"五段制资产应存在: {rel}")

    def test_memory_elements_kept_frozen(self):
        for rel in _KEPT_MEMORY_FILES:
            self.assertTrue((_HARNESS_DIR / rel).exists(), f"记忆要素冻结保留: {rel}")

    def test_manifest_declares_single_agent(self):
        from contracts.architecture_manifest import load_and_validate

        manifest, result = load_and_validate(_HARNESS_DIR)
        self.assertTrue(result.ok, result.errors)
        mains = [a.name for a in manifest.main_agents]
        self.assertEqual(mains, ["author"])
        self.assertEqual(manifest.sub_agents, [])
        main = manifest.agent("author")
        # 语义工具七件套 + confirm；无委托；路径收权
        self.assertIn("save_artifact", main.tools)
        self.assertIn("confirm_with_user", main.tools)
        self.assertEqual(main.delegates, [])
        self.assertEqual(main.write_permissions, [])
        self.assertTrue(main.disable_file_tools)

    def test_base_prompt_carries_interview_discipline(self):
        """常驻骨架含访谈纪律与工具纪律（FR-001：一轮 1-2 问、必停、确认分级）。"""
        text = (_HARNESS_DIR / "prompts" / "author_base.md").read_text(encoding="utf-8")
        for marker in ("一轮只聊一两个问题", "停下", "confirm_with_user", "问询协议", "约束级"):
            self.assertIn(marker, text, f"author_base 缺纪律特征「{marker}」")

    def test_registry_mirror_sanity(self):
        registry = json.loads((_HARNESS_DIR / "registry.json").read_text(encoding="utf-8"))
        versions = [v["version"] for v in registry["versions"]]
        self.assertTrue(versions, "registry 至少登记一个版本")
        self.assertLessEqual(registry["production"], max(versions))


class TestFiveSegmentAssemble(unittest.TestCase):
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
            trace_id="trace-five-segment-test",
            trace_recorder=recorder,
            trace_middleware_cls=None,
        )

    def test_assemble_default_and_named_main(self):
        pkg = self._load_pkg()
        recorder = _StubRecorder()
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._make_ctx(tmp, recorder)
            default = pkg.assemble(ctx)
            self.assertTrue(callable(getattr(default, "ainvoke", None)), "缺省装配应返回可执行图")
            named = pkg.assemble(ctx, agent_name="author")
            self.assertTrue(callable(getattr(named, "ainvoke", None)), "按名装配 author 应返回可执行图")
            with self.assertRaises(Exception):
                pkg.assemble(ctx, agent_name="writer")  # 退役主控名 → ManifestError

        # 单 agent：只有 author 的中间件栈（trace V2 口径）
        self.assertIn("author-subagent", recorder.stacks)
        self.assertNotIn("writer-subagent", recorder.stacks)
        self.assertEqual(recorder.catalogs, [], "五段制无技能目录（方法论在提示词）")

    def test_stage_prompt_helper(self):
        """包级段规程装配口（executor 按段注入的读取入口，DEC-002）。"""
        pkg = self._load_pkg()
        self.assertIn("## 一句话主题", pkg.stage_prompt("theme") or "")
        self.assertIn("核心纲", pkg.stage_prompt("outline") or "")
        self.assertIn("卷末大爽点", pkg.stage_prompt("volume") or "")
        self.assertIn("指令卡", pkg.stage_prompt("chapter") or "")
        self.assertIn("章纲是宪法", pkg.stage_prompt("text") or "")
        self.assertIsNone(pkg.stage_prompt("不存在"))

    def test_interpreter_passes_checkpointer_to_top_level(self):
        """顶层装配必须透传 checkpointer（会话/断点持久化依赖，FR-003）。"""
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
                ctx = self._make_ctx(tmp, recorder, checkpointer=sentinel)
                pkg.assemble(ctx)
        finally:
            arch_mod.create_deep_agent = original

        self.assertIs(captured.get("checkpointer"), sentinel)
        # 路径收权（FR-003）：deny-all 文件工具权限传给了 create_deep_agent
        perms = captured.get("permissions")
        self.assertTrue(perms, "disable_file_tools 应产生 permissions")
        self.assertEqual(perms[0].mode, "deny")


if __name__ == "__main__":
    unittest.main()
