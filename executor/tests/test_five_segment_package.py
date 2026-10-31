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
    "prompts/review.md",
    "prompts/volume_qc.md",
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

    def test_manifest_declares_author_and_review(self):
        """五段制单 main（author）+ review 审稿 + volume_qc 质检子代理（REQ-20261031-142504）。"""
        from contracts.architecture_manifest import load_and_validate

        manifest, result = load_and_validate(_HARNESS_DIR)
        self.assertTrue(result.ok, result.errors)
        mains = [a.name for a in manifest.main_agents]
        self.assertEqual(mains, ["author"])
        self.assertEqual([a.name for a in manifest.sub_agents], ["review", "volume_qc"])
        main = manifest.agent("author")
        # 语义工具六件套（含 confirm）；委托审稿＋卷纲质检；路径收权；修订上限 1（一轮修订无复审）
        self.assertIn("save_artifact", main.tools)
        self.assertIn("confirm_with_user", main.tools)
        self.assertEqual(main.delegates, ["review", "volume_qc"])
        self.assertEqual(main.write_permissions, [])
        self.assertTrue(main.disable_file_tools)
        self.assertEqual(main.max_revisions, 1)
        # 审稿子代理：只读（无写权限、无语义写工具）、路径收权、被 author 委托
        review = manifest.agent("review")
        self.assertEqual(review.role, "sub")
        self.assertEqual(review.delegated_by, "author")
        self.assertEqual(review.delegates, [])
        self.assertIn("read_artifact", review.tools)
        self.assertNotIn("save_artifact", review.tools)
        self.assertEqual(review.write_permissions, [])
        self.assertTrue(review.disable_file_tools)
        # 质检子代理（REQ-20261031-142504）：同款只读收权；max_revisions=2 即质检循环硬上限
        qc = manifest.agent("volume_qc")
        self.assertEqual(qc.role, "sub")
        self.assertEqual(qc.delegated_by, "author")
        self.assertEqual(qc.delegates, [])
        self.assertEqual(qc.tools, ["read_artifact"])
        self.assertEqual(qc.max_revisions, 2)
        self.assertTrue(qc.disable_file_tools)

    def test_base_prompt_carries_interview_discipline(self):
        """常驻骨架含访谈纪律与工具纪律（FR-001：批次推进、必停、确认出口、表达铁律）。"""
        text = (_HARNESS_DIR / "prompts" / "author_base.md").read_text(encoding="utf-8")
        for marker in ("一轮推进一个批次", "停下", "confirm_with_user", "表达铁律", "约束级"):
            self.assertIn(marker, text, f"author_base 缺纪律特征「{marker}」")

    def test_review_prompt_carries_review_dimensions(self):
        """审稿提示词含三维判据与 12 类病灶全集（REQ-20261025-230811 FR-002）。"""
        text = (_HARNESS_DIR / "prompts" / "review.md").read_text(encoding="utf-8")
        for marker in (
            "逻辑四账", "清晰度", "AI 味",
            # 12 类病灶全集（eval_agent 判据基线，一项不落）
            "完整闭环", "副词堆砌", "千人一面", "辩论式对话", "情绪贴标签",
            "信息均匀分布", "安全着陆", "展示后解释", "说明书式对话",
            "句式同构", "四字套语堆叠", "情绪三连",
            "不得判为问题",                 # 用户技能审查约束承接（FR-005）
            "只审不改",                     # 职责边界
        ):
            self.assertIn(marker, text, f"review.md 缺审查特征「{marker}」")

    def test_zhengwen_stage_carries_review_loop(self):
        """正文段规程含审稿委托与一轮修订流程（REQ-20261025-230811 FR-001/003/006）。"""
        text = (_HARNESS_DIR / "prompts" / "stage_zhengwen.md").read_text(encoding="utf-8")
        for marker in (
            "审稿", "review",                # 委托环节
            "最小改动",                      # 修订纪律（无复审风险的处置约束）
            "不重写",
            "自检报告",                      # 修订后对终稿自检（现有流程保留）
            "审稿环节失败已跳过",            # FR-006 失败降级声明
            "不重试",                        # FR-006 失败不阻塞交付
        ):
            self.assertIn(marker, text, f"stage_zhengwen.md 缺审稿闭环特征「{marker}」")

    def test_other_stages_carry_no_review_delegation(self):
        """题材/总纲/卷纲/章纲四段规程零审稿委托（FR-001：仅正文段委托 review）。"""
        for filename in (
            "stage_theme.md", "stage_zonggang.md",
            "stage_juangang.md", "stage_zhanggang.md",
        ):
            text = (_HARNESS_DIR / "prompts" / filename).read_text(encoding="utf-8")
            self.assertNotIn("审稿", text, f"{filename} 不应含审稿委托")
            self.assertNotIn("review", text, f"{filename} 不应含 review 委托")

    def test_qc_delegation_pins(self):
        """REQ-20261031-142504：卷纲段（含 neutral 变体）委托 volume_qc 质检；正文段仍委托 review；其余段零子代理委托。"""
        for filename in ("stage_juangang.md", "stage_juangang_neutral.md"):
            text = (_HARNESS_DIR / "prompts" / filename).read_text(encoding="utf-8")
            self.assertIn('subagent_type="volume_qc"', text, f"{filename} 应委托 volume_qc 质检")
            self.assertIn("上限 2 轮", text, f"{filename} 应声明质检循环上限 2 轮")
        zhengwen = (_HARNESS_DIR / "prompts" / "stage_zhengwen.md").read_text(encoding="utf-8")
        self.assertIn('subagent_type="review"', zhengwen, "正文段应保持 review 审稿委托")
        for filename in (
            "stage_theme.md", "stage_zonggang.md", "stage_zhanggang.md",
            "stage_theme_neutral.md", "stage_zonggang_neutral.md", "stage_zhanggang_neutral.md",
        ):
            text = (_HARNESS_DIR / "prompts" / filename).read_text(encoding="utf-8")
            self.assertNotIn("volume_qc", text, f"{filename} 不应委托 volume_qc")
            self.assertNotIn("subagent_type=", text, f"{filename} 不应含任何子代理委托")

    def test_old_four_step_terms_purged(self):
        """REQ-20261031-142504 AC-001/003：卷纲/章纲（含 neutral 变体）旧结构术语零残留。"""
        juangang_forbidden = [
            "卷末超大爽点", "发展阶段", "压抑标注", "【压·小", "【压·大",
            "爽点质检表", "与此同时", "定拍", "双出口",
        ]
        zhanggang_forbidden = [
            "所属阶段", "五列", "卷末超大爽点", "阶段表", "【压·小", "【压·大",
            "蓄水对象", "偿付出口",
        ]
        zonggang_forbidden = [
            "关键节点", "小目标", "节点卡", "卷末超大爽点", "五步",
        ]
        for filename in ("stage_juangang.md", "stage_juangang_neutral.md"):
            text = (_HARNESS_DIR / "prompts" / filename).read_text(encoding="utf-8")
            for term in juangang_forbidden:
                self.assertNotIn(term, text, f"{filename} 残留旧术语「{term}」")
        for filename in ("stage_zhanggang.md", "stage_zhanggang_neutral.md"):
            text = (_HARNESS_DIR / "prompts" / filename).read_text(encoding="utf-8")
            for term in zhanggang_forbidden:
                self.assertNotIn(term, text, f"{filename} 残留旧术语「{term}」")
        for filename in ("stage_zonggang.md", "stage_zonggang_neutral.md"):
            text = (_HARNESS_DIR / "prompts" / filename).read_text(encoding="utf-8")
            for term in zonggang_forbidden:
                self.assertNotIn(term, text, f"{filename} 残留旧术语「{term}」")

    def test_volume_qc_prompt_carries_three_layers(self):
        """REQ-20261031-142504 AC-002：质检员提示词含三层判据、只审不改与通过暗号。"""
        text = (_HARNESS_DIR / "prompts" / "volume_qc.md").read_text(encoding="utf-8")
        for marker in ("因果链", "驱动贴章", "释放对位", "只审不改", "通过，无修改意见", "人际", "环境"):
            self.assertIn(marker, text, f"volume_qc.md 缺判据特征「{marker}」")

    def test_volume_qc_architecture_registration(self):
        """REQ-20261031-142504：architecture.json 注册 volume_qc 子代理并挂进 author 委托。"""
        manifest = json.loads((_HARNESS_DIR / "architecture.json").read_text(encoding="utf-8"))
        agents = {a["name"]: a for a in manifest["agents"]}
        self.assertIn("volume_qc", agents, "volume_qc 子代理未注册")
        qc = agents["volume_qc"]
        self.assertEqual(qc["role"], "sub")
        self.assertEqual(qc["prompt"], "prompts/volume_qc.md")
        self.assertEqual(qc["max_revisions"], 2, "质检循环硬上限应为 2 轮")
        self.assertEqual(qc["tools"], ["read_artifact"], "质检员只读不写")
        self.assertIn("volume_qc", agents["author"]["delegates"], "author 应委托 volume_qc")

    def test_registry_mirror_sanity(self):
        registry = json.loads((_HARNESS_DIR / "registry.json").read_text(encoding="utf-8"))
        versions = [v["version"] for v in registry["versions"]]
        self.assertTrue(versions, "registry 至少登记一个版本")
        self.assertLessEqual(registry["production"], max(versions))


class TestFiveSegmentAssemble(unittest.TestCase):
    def _load_pkg(self):
        from app.platform.agent.loader import load_package

        return load_package(_HARNESS_DIR)

    def _make_ctx(self, tmpdir: str, recorder, checkpointer=None, styles=None):
        from contracts.runtime_context import RuntimeContext

        return RuntimeContext(
            model=_StubModel(),
            backend=object(),
            checkpointer=checkpointer,
            workspace_path=Path(tmpdir),
            trace_id="trace-five-segment-test",
            trace_recorder=recorder,
            trace_middleware_cls=None,
            styles=styles,
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
        outline_prompt = pkg.stage_prompt("outline") or ""
        self.assertIn("核心纲", outline_prompt)
        self.assertIn("主角想要", outline_prompt, "核心纲应含卷级主角想要列")
        self.assertIn("本卷矛盾", outline_prompt, "核心纲应含卷级本卷矛盾列")
        volume_prompt = pkg.stage_prompt("volume") or ""
        self.assertIn("衔接词", volume_prompt, "卷纲段规程应含因果衔接词规则")
        self.assertIn("主角想要", volume_prompt, "卷纲段规程应含驱动两要素")
        self.assertIn("20-25", volume_prompt, "卷纲段章数默认估算应为 20-25 章")
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

    def test_review_subagent_mounted_with_styles_suffix(self):
        """审稿子代理挂进 author 的 task 委托；styles["review"] 进其 system prompt
        （REQ-20261025-230811 FR-001/FR-005 装配链：技能约束 → 解释器 → 子代理）。"""
        from app.platform.agent.architecture import SubAgent  # noqa: F401 — 类型参考

        pkg = self._load_pkg()
        captured: dict[str, Any] = {}

        import app.platform.agent.architecture as arch_mod

        original = arch_mod.create_deep_agent
        try:

            def _capture(**kwargs):
                captured.update(kwargs)
                return original(**kwargs)

            arch_mod.create_deep_agent = _capture
            recorder = _StubRecorder()
            with tempfile.TemporaryDirectory() as tmp:
                # 模拟 executor 装配层（U2）注入的审稿约束 suffix
                ctx = self._make_ctx(
                    tmp, recorder, styles={"review": "审查约束：不得将其判为问题"}
                )
                pkg.assemble(ctx)
        finally:
            arch_mod.create_deep_agent = original

        subs = captured.get("subagents") or []

        def _field(spec: Any, key: str) -> Any:
            return spec[key] if isinstance(spec, dict) else getattr(spec, key)

        names = [_field(s, "name") for s in subs]
        self.assertIn("review", names, f"review 应挂进 task 委托（实际: {names}）")
        review = next(s for s in subs if _field(s, "name") == "review")
        self.assertIn("逻辑四账", _field(review, "system_prompt"))          # review.md 常驻判据
        self.assertIn("不得将其判为问题", _field(review, "system_prompt"))  # styles suffix 已拼接
        # 只读审稿：写权限全拒 + 工具面恰好一个读档工具（不继承父级写工具，
        # REQ-20261025-230811 review P1：解释器须显式传 tools，缺省即继承）
        perm_modes = [(p.operations, p.mode) for p in _field(review, "permissions")]
        self.assertIn((["write"], "deny"), perm_modes, f"review 应无写权限（实际: {perm_modes}）")
        tools = _field(review, "tools") or []
        tool_names = sorted(
            t.name if hasattr(t, "name") else str(t.get("name", t)) if isinstance(t, dict) else str(t)
            for t in tools
        )
        self.assertEqual(tool_names, ["read_artifact"], f"review 工具面应恰为 read_artifact（实际: {tool_names}）")


if __name__ == "__main__":
    unittest.main()
