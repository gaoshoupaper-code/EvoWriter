"""三步制 harness 包验收测试（REQ-20261015-161200 FR-002/009，AC-008）。

跑法（在 executor 目录）：python -m pytest tests/test_three_step_package.py -v

覆盖（取代双泳道契约测试）：
  - 包内容形态：writer 单主控 + worldview/outline/chapters 三子代理资产齐备；
    双泳道资产（storydesign/storystructure/checkup/rework 工单/object 护栏）已清理
  - registry：本地镜像 sanity（production ≤ 已登记最大版本）
  - assemble：缺省装配 = 唯一 main（writer）；按名装配 writer；
    checkpointer 透传到顶层 create_deep_agent（会话持久化依赖）
  - 主控 Prompt 含 5 条输出风格铁律与 confirm_with_user 唯一持有声明
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


# 双泳道/七步制资产（本次清理）：存在即失败
_FORBIDDEN_FILES = [
    "prompts/storydesign_system.md",
    "prompts/storystructure_system.md",
    "middleware/object_contract_guard.py",
    "middleware/checkup_config.json",
    "tools/submit_rework_ticket.py",
    "checkup.py",
]
# 三步制资产：缺失即失败
_REQUIRED_FILES = [
    "architecture.json",
    "prompts/writer_system.md",
    "prompts/worldview_system.md",
    "prompts/outline_system.md",
    "prompts/chapters_system.md",
    "prompts/demand_template.md",
    "middleware/receipt_gate.py",
    "middleware/outline_contract_guard.py",
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

# 输出风格铁律的特征片段（全部 Prompt 必含；REQ FR-004）
_STYLE_RULE_MARKERS = (
    "禁破折号",
    "禁解释性引导语",
    "一句话",
    "表格",
    "不写设计理由",
)


class TestThreeStepPackageContent(unittest.TestCase):
    def test_dual_lane_assets_deleted(self):
        for rel in _FORBIDDEN_FILES:
            self.assertFalse(
                (_HARNESS_DIR / rel).exists(),
                f"双泳道资产应已清理: {rel}",
            )

    def test_three_step_assets_kept(self):
        for rel in _REQUIRED_FILES:
            self.assertTrue((_HARNESS_DIR / rel).exists(), f"三步制资产应存在: {rel}")

    def test_memory_elements_kept_frozen(self):
        for rel in _KEPT_MEMORY_FILES:
            self.assertTrue((_HARNESS_DIR / rel).exists(), f"记忆要素冻结保留: {rel}")

    def test_manifest_declares_single_main_three_subs(self):
        from contracts.architecture_manifest import load_and_validate

        manifest, result = load_and_validate(_HARNESS_DIR)
        self.assertTrue(result.ok, result.errors)
        mains = [a.name for a in manifest.main_agents]
        self.assertEqual(mains, ["writer"])
        subs = sorted(a.name for a in manifest.sub_agents)
        self.assertEqual(subs, ["chapters", "outline", "worldview"])
        main = manifest.agent("writer")
        # confirm 工具只挂主控（TD-001：子代理无自定义工具）
        self.assertIn("confirm_with_user", main.tools)
        self.assertEqual(main.delegates, ["worldview", "outline", "chapters"])
        # 主控不写文件；三子代理写权限按步骤划分
        self.assertEqual(main.write_permissions, [])
        self.assertEqual(
            manifest.agent("worldview").write_permissions,
            ["/outline/世界观.md"],
        )
        self.assertIn("/outline/总纲.md", manifest.agent("outline").write_permissions)
        self.assertIn("/outline/期待台账.md", manifest.agent("chapters").write_permissions)
        for name in ("worldview", "outline", "chapters"):
            self.assertEqual(manifest.agent(name).tools, [], f"{name} 不得挂自定义工具")

    def test_all_prompts_carry_style_rules(self):
        for rel in (
            "prompts/writer_system.md",
            "prompts/worldview_system.md",
            "prompts/outline_system.md",
            "prompts/chapters_system.md",
        ):
            text = (_HARNESS_DIR / rel).read_text(encoding="utf-8")
            for marker in _STYLE_RULE_MARKERS:
                self.assertIn(marker, text, f"{rel} 缺风格铁律特征「{marker}」")

    def test_registry_mirror_sanity(self):
        registry = json.loads((_HARNESS_DIR / "registry.json").read_text(encoding="utf-8"))
        versions = [v["version"] for v in registry["versions"]]
        self.assertTrue(versions, "registry 至少登记一个版本")
        self.assertLessEqual(registry["production"], max(versions))


class TestThreeStepAssemble(unittest.TestCase):
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
            trace_id="trace-three-step-test",
            trace_recorder=recorder,
            trace_middleware_cls=None,
        )

    def test_assemble_default_and_named_main(self):
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
            named = pkg.assemble(ctx, agent_name="writer")
            self.assertTrue(callable(getattr(named, "ainvoke", None)), "按名装配 writer 应返回可执行图")
            with self.assertRaises(Exception):
                pkg.assemble(ctx, agent_name="storydesign")  # 已退役主控名 → ManifestError

        # 主控与三子代理都记中间件栈（trace V2 口径）
        self.assertIn("writer-subagent", recorder.stacks)
        for sub in ("worldview-subagent", "outline-subagent", "chapters-subagent"):
            self.assertIn(sub, recorder.stacks)
        self.assertEqual(recorder.catalogs, [], "三步制无技能目录（方法论在主文档）")

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
