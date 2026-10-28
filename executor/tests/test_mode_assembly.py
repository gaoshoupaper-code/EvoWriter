"""按模式组装交互引导测试（REQ-20261028-110538 FR-001/002/003/006/007）。

跑法（在 executor 目录）：python -m pytest tests/test_mode_assembly.py -v

覆盖：
  - 新提示词资产：中性段规程不含交互编排；两份引导内容对立且齐全
  - 组装逻辑（AC-001/AC-004）：二选一注入、互不掺杂、三部分齐全顺序稳定
  - 缺失语义（AC-005）：任一组件缺失 fail-fast，四条报错路径全覆盖
  - 清单共存（AC-006 前置）：旧文件原样保留、新 executor 只读新文件
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from contracts.writing_artifacts import (
    STAGE_CHAPTER,
    STAGE_OUTLINE,
    STAGE_TEXT,
    STAGE_THEME,
    STAGE_VOLUME,
    set_stage_mode,
)

AUTO = "auto"
INTERACTIVE = "interactive"
_HARNESS = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
_ALL_STAGES = (STAGE_THEME, STAGE_OUTLINE, STAGE_VOLUME, STAGE_CHAPTER, STAGE_TEXT)


def _load_pkg():
    from app.platform.agent.loader import load_package

    return load_package(_HARNESS)


class TestNeutralAssets(unittest.TestCase):
    """新提示词资产结构（FR-002/FR-003/DEC-005）。"""

    def test_neutral_stage_prompts_exist_and_carry_no_interaction(self):
        """五段中性规程齐全，且不含逐步交互编排（confirm_with_user 编排外移）。"""
        pkg = _load_pkg()
        for stage in _ALL_STAGES:
            text = pkg.neutral_stage_prompt(stage)
            self.assertTrue(text, f"中性段规程缺失: {stage}")
            self.assertNotIn(
                "confirm_with_user", text,
                f"{stage} 中性规程仍含交互编排",
            )
            # 中性占位词在位：交互节点交给引导定义
            self.assertIn("交互引导", text, f"{stage} 中性规程缺交互节点指引")

    def test_author_base_neutral_keeps_tools_drops_interview(self):
        """中性骨架保留工具用法与表达铁律，去掉逐步停等口径。"""
        pkg = _load_pkg()
        text = pkg.author_base_neutral_prompt()
        self.assertTrue(text)
        self.assertIn("confirm_with_user", text)  # 工具调用格式保留（FR-003）
        self.assertIn("表达铁律", text)
        self.assertNotIn("每轮回复完必须停下", text)  # 逐步停等移入引导
        self.assertNotIn("访谈铁律", text)

    def test_guides_opposed_and_complete(self):
        """两份引导齐全且口径对立：逐步=每轮停等问作者；自动=严禁提问。"""
        pkg = _load_pkg()
        it = pkg.guide_prompt(INTERACTIVE)
        au = pkg.guide_prompt(AUTO)
        self.assertTrue(it and au)
        self.assertIn("每轮回复完必须停下", it)
        self.assertIn("confirm_with_user", it)
        self.assertIn("严禁调用 `confirm_with_user`", au)
        self.assertNotIn("每轮回复完必须停下", au)
        self.assertNotIn("严禁调用", it)

    def test_unknown_stage_and_mode_return_none(self):
        pkg = _load_pkg()
        self.assertIsNone(pkg.neutral_stage_prompt("nope"))
        self.assertIsNone(pkg.guide_prompt("yolo"))


class TestModeAssembly(unittest.TestCase):
    """组装逻辑（FR-001；AC-001/AC-004 用 _compose_mode_assembly + _system_prompt 全链）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.pkg = _load_pkg()

    def tearDown(self):
        self.tmp.cleanup()

    def _assemble_full_prompt(self, stage: str) -> str:
        """全链拼装 = 中性 author_base（override）+ 段规程 + 引导（styles suffix）。"""
        from app.domains.writing.agent import _compose_mode_assembly

        suffix, overrides = _compose_mode_assembly(self.pkg, self.ws, stage)
        return f"{overrides['author']}\n\n{suffix}"

    def test_interactive_assembly_has_no_auto_discipline(self):
        """AC-001：逐步段注入文本不含自动模式纪律（骨架的节点元说明除外）。"""
        for stage in _ALL_STAGES:  # 含锁死段（恒 interactive）与未设置段（默认）
            prompt = self._assemble_full_prompt(stage)
            self.assertNotIn("严禁调用", prompt, f"{stage} 逐步装配混入自动纪律")
            self.assertNotIn("作者不在场", prompt)
            self.assertNotIn("自动批次", prompt)

    def test_auto_assembly_has_no_interview_choreography(self):
        """AC-001：auto 段注入文本不含逐步停等编排。"""
        for stage in (STAGE_VOLUME, STAGE_CHAPTER, STAGE_TEXT):
            set_stage_mode(self.ws, stage, AUTO)
            prompt = self._assemble_full_prompt(stage)
            self.assertNotIn(
                "每轮回复完必须停下", prompt, f"{stage} auto 装配混入逐步停等",
            )
            self.assertIn("严禁调用 `confirm_with_user`", prompt)
            self.assertIn("request_review", prompt)

    def test_assembly_components_and_order(self):
        """AC-004：三部分齐全、顺序稳定（骨架 → 段规程 → 引导）。"""
        from app.domains.writing.agent import _compose_mode_assembly

        suffix, overrides = _compose_mode_assembly(self.pkg, self.ws, STAGE_VOLUME)
        stage_text = self.pkg.neutral_stage_prompt(STAGE_VOLUME)
        guide_text = self.pkg.guide_prompt(INTERACTIVE)
        base_text = self.pkg.author_base_neutral_prompt()
        self.assertEqual(suffix, f"{stage_text}\n\n{guide_text}")
        self.assertEqual(overrides, {"author": base_text})
        full = self._assemble_full_prompt(STAGE_VOLUME)
        self.assertLess(full.index(base_text), full.index(stage_text))
        self.assertLess(full.index(stage_text), full.index(guide_text))

    def test_mode_flip_switches_guide(self):
        """DEC-004：段模式切换改变注入的引导（实时读段模式）。"""
        from app.domains.writing.agent import _compose_mode_assembly

        set_stage_mode(self.ws, STAGE_TEXT, AUTO)
        suffix_auto, _ = _compose_mode_assembly(self.pkg, self.ws, STAGE_TEXT)
        self.assertIn("严禁调用 `confirm_with_user`", suffix_auto)
        set_stage_mode(self.ws, STAGE_TEXT, INTERACTIVE)
        suffix_it, _ = _compose_mode_assembly(self.pkg, self.ws, STAGE_TEXT)
        self.assertNotIn("严禁调用", suffix_it)


class TestFailFast(unittest.TestCase):
    """缺失语义（FR-007；AC-005 四条报错路径）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.pkg = _load_pkg()

    def tearDown(self):
        self.tmp.cleanup()

    def _compose(self, pkg):
        from app.domains.writing.agent import _compose_mode_assembly

        return _compose_mode_assembly(pkg, self.ws, STAGE_VOLUME)

    def test_missing_pkg_interface_fails_fast(self):
        with self.assertRaises(RuntimeError):
            self._compose(SimpleNamespace())  # 无组装接口

    def test_missing_stage_prompt_fails_fast(self):
        pkg = SimpleNamespace(
            neutral_stage_prompt=lambda s: None,
            guide_prompt=lambda m: "g",
            author_base_neutral_prompt=lambda: "b",
        )
        with self.assertRaises(RuntimeError):
            self._compose(pkg)

    def test_missing_guide_fails_fast(self):
        pkg = SimpleNamespace(
            neutral_stage_prompt=lambda s: "s",
            guide_prompt=lambda m: None,
            author_base_neutral_prompt=lambda: "b",
        )
        with self.assertRaises(RuntimeError):
            self._compose(pkg)

    def test_missing_base_fails_fast(self):
        pkg = SimpleNamespace(
            neutral_stage_prompt=lambda s: "s",
            guide_prompt=lambda m: "g",
            author_base_neutral_prompt=lambda: None,
        )
        with self.assertRaises(RuntimeError):
            self._compose(pkg)

    def test_real_file_missing_fails_fast(self):
        """真 harness 包文件缺失（模拟旧发版）同样显性失败。"""
        pkg = SimpleNamespace(
            neutral_stage_prompt=lambda s: self.pkg.neutral_stage_prompt(s),
            guide_prompt=lambda m: None,  # 模拟 guide 文件缺失
            author_base_neutral_prompt=lambda: self.pkg.author_base_neutral_prompt(),
        )
        with self.assertRaises(RuntimeError):
            self._compose(pkg)


class TestPromptOverride(unittest.TestCase):
    """_system_prompt 的 override 优先（FR-001 经 ctx.prompt_overrides）。"""

    def test_override_replaces_manifest_prompt_and_keeps_suffix(self):
        from app.platform.agent.architecture import _system_prompt

        abc = SimpleNamespace(
            ctx=SimpleNamespace(
                prompt_overrides={"author": "中性骨架"},
                styles={"author": "段规程+引导"},
            ),
            agent=SimpleNamespace(name="author", prompt="prompts/author_base.md"),
            pkg_dir=_HARNESS,
        )
        self.assertEqual(_system_prompt(abc), "中性骨架\n\n段规程+引导")

    def test_no_override_reads_manifest_file(self):
        from app.platform.agent.architecture import _system_prompt

        abc = SimpleNamespace(
            ctx=SimpleNamespace(prompt_overrides=None, styles=None),
            agent=SimpleNamespace(name="review", prompt="prompts/review.md"),
            pkg_dir=_HARNESS,
        )
        text = _system_prompt(abc)
        self.assertTrue(text)  # 旧清单引用照旧可读（AC-006 共存前置）


class TestCoexistence(unittest.TestCase):
    """共存发布（FR-006/DEC-008）：旧文件原样保留，旧接口照旧可用。"""

    def test_legacy_assets_untouched_and_callable(self):
        pkg = _load_pkg()
        for stage in _ALL_STAGES:
            self.assertTrue(pkg.stage_prompt(stage))  # 旧段规程在
        self.assertTrue(pkg.auto_mode_prompt())  # 旧 auto 纪律在
        manifest = (_HARNESS / "architecture.json").read_text(encoding="utf-8")
        self.assertIn("prompts/author_base.md", manifest)  # 清单引用未改

    def test_neutral_files_are_new_names(self):
        """新文件全部为新文件名，与旧文件零冲突。"""
        pkg = _load_pkg()
        old = {
            "stage_theme.md", "stage_zonggang.md", "stage_juangang.md",
            "stage_zhanggang.md", "stage_zhengwen.md", "author_base.md",
            "auto_mode.md",
        }
        new = {
            pkg.NEUTRAL_STAGE_PROMPT_FILES[s] for s in _ALL_STAGES
        } | {pkg.GUIDE_INTERACTIVE_FILE, pkg.GUIDE_AUTO_FILE, pkg.AUTHOR_BASE_NEUTRAL_FILE}
        self.assertFalse(old & new)
        for name in new:
            self.assertTrue(
                (_HARNESS / "prompts" / name).exists(), f"新文件缺失: {name}",
            )


if __name__ == "__main__":
    unittest.main()
