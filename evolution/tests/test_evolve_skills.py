"""Skills 池加载器 + system prompt 注入单测（2026-10-04 require 技能接入）。

覆盖：
  - load_skill_sections：池内 SKILL.md 正常渲染（frontmatter 剥离、
    name/description 进头、正文保留）；池空 → 空串
  - _render_section：frontmatter 非法 → 目录名占位、正文不丢；
    无 frontmatter → 目录名占位；无正文 → 跳过
  - evolve_system_prompt：技能段追加在 prompt 最末尾；CURRENT_SESSION
    占位符替换不受影响；池空时行为与旧版一致
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evolve.skills import _render_section, load_skill_sections
from app.evolve.agent.prompt import evolve_system_prompt


class _FakePoolPath:
    """把 Path(__file__).resolve().parent 指向指定目录的替身。"""

    def __init__(self, target: Path) -> None:
        self._target = target

    def resolve(self) -> "_FakePoolPath":
        return self

    @property
    def parent(self) -> Path:
        return self._target


class LoadSkillSectionsTest(unittest.TestCase):
    def test_real_pool_loads_require_skill(self):
        """池内 require 技能被渲染：头带 name + description，frontmatter 已剥。"""
        section = load_skill_sections()
        self.assertIn("# 技能 · require", section)
        self.assertIn("需求追问纪律", section)
        self.assertNotIn("name: require", section)  # frontmatter 已剥离
        # 正文核心章节在
        self.assertIn("# 追问循环", section)
        self.assertIn("# 收尾", section)

    def test_empty_pool_returns_empty(self):
        """池空 → 空串（调用方按空串不追加）。"""
        with tempfile.TemporaryDirectory() as td:
            fake = _FakePoolPath(Path(td))
            with mock.patch("app.evolve.skills.Path", return_value=fake):
                self.assertEqual(load_skill_sections(), "")

    def test_invalid_frontmatter_degrades_to_body(self):
        """frontmatter YAML 非法 → 目录名占位 + 正文原样注入（内容不丢）。"""
        text = "---\nname: [broken\n---\n\n正文指令 A。"
        section = _render_section(text, fallback_name="my-skill")
        self.assertIn("# 技能 · my-skill", section)
        self.assertIn("正文指令 A。", section)

    def test_missing_frontmatter_uses_fallback_name(self):
        """无 frontmatter → 目录名占位，正文注入。"""
        section = _render_section("纯正文。", fallback_name="bare")
        self.assertIn("# 技能 · bare", section)
        self.assertIn("纯正文。", section)

    def test_empty_body_skipped(self):
        """只有 frontmatter 没正文 → 空串（跳过该技能）。"""
        self.assertEqual(_render_section("---\nname: empty\n---\n\n", "empty"), "")


class EvolveSystemPromptSkillsTest(unittest.TestCase):
    def _build(self, skills_block: str) -> str:
        with mock.patch(
            "app.evolve.agent.prompt.load_skill_sections", return_value=skills_block
        ):
            return evolve_system_prompt(
                session_id="sess-1",
                trace_id="trace-1",
                input_summary="（无附带输入）",
                work_binding="- 作品绑定示例",
            )

    def test_skills_appended_at_tail(self):
        """技能段追加在 prompt 最末尾，且占位符替换正常。"""
        prompt = self._build("# 技能 · require\n\n追问纪律正文。")
        self.assertTrue(prompt.endswith("追问纪律正文。"))
        self.assertIn("## 当前 session", prompt)
        self.assertIn("sess-1", prompt)
        self.assertNotIn("<!-- CURRENT_SESSION -->", prompt)

    def test_empty_skills_keeps_prompt_intact(self):
        """池空 → 不追加任何内容，与注入前行为一致。"""
        prompt = self._build("")
        self.assertNotIn("# 技能 ·", prompt)
        self.assertIn("## 当前 session", prompt)

    def test_real_pool_end_to_end(self):
        """不 mock 时：真实池内容进入最终 prompt（require 技能在尾部）。"""
        prompt = evolve_system_prompt("s", "t", "x")
        self.assertIn("# 技能 · require", prompt)
        self.assertIn("宁可多问十轮", prompt)


if __name__ == "__main__":
    unittest.main()
