"""双架构 Prompt 同源对齐校验（REQ-20260922-162823 FR-003 / AC-003）。

以 v13 发版 commit（单 Agent 连续增量版）的 storybuilding_system.md 与
initial/expand SKILL.md 为基准，自动核对 v14 working 包（多 Agent 版）的
prompt 拆分文件逐字包含对应的领域规范段落——「规则共享 + 领域增补」
（DEC-001）与 skills 同源拆分（DEC-005）的机械化证据。

映射总表（人工部分见包内 prompts/v14/ALIGNMENT.md）：
  v13 §一/§二/§三/§四/§五      → v14 domain_worldview/character/storyline（逐字）
  v13 §7.1/7.2/7.3/7.5/7.6/7.7/7.9、§8.1/8.3 → v14 common_rules（逐字）
  v13 §7.4/7.8、§8.2/8.4      → v14 orchestrator_system（编排适配版，语义同源）
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

_HARNESS_REPO = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
_V13_COMMIT = "0692887"  # v13 单故事专家连续增量版发版 commit
_V14_PROMPTS = _HARNESS_REPO / "prompts" / "v14"

# v13 基准经嵌套仓 git 历史读取（git show）——fresh clone / 未携带嵌套 .git 的
# 环境（CI）无该对象，守卫跳过而非 RuntimeError（review correctness-P2）。
_HAS_NESTED_GIT = (_HARNESS_REPO / ".git").exists()


def _git_show(path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{_V13_COMMIT}:{path}"],
        cwd=_HARNESS_REPO, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show {path} 失败: {result.stderr.strip()}")
    return result.stdout


def _v13_sections() -> dict[str, str]:
    """v13 system prompt 按 ## 标题切节（标题+正文，逐字）。"""
    text = _git_show("prompts/storybuilding_system.md")
    parts = re.split(r"(?m)^(## .+)$", text)
    sections: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        sections[parts[i].strip()] = parts[i] + "\n" + parts[i + 1]
    return sections


def _v13_subsections(sec_title: str) -> dict[str, str]:
    text = _v13_sections()[sec_title]
    pieces = re.split(r"(?m)^(### .+)$", text)
    m: dict[str, str] = {}
    for i in range(1, len(pieces), 2):
        m[pieces[i].strip()] = pieces[i] + "\n" + pieces[i + 1]
    return m


def _read_v14(name: str) -> str:
    return (_V14_PROMPTS / name).read_text(encoding="utf-8")


@unittest.skipUnless(_HAS_NESTED_GIT, "嵌套 harness 仓 git 历史不可用（fresh clone），v13 基准对齐校验跳过")
class PromptAlignmentTest(unittest.TestCase):
    """v14 领域/公共文件逐字包含 v13 对应段落（DEC-001/005）。"""

    def test_domain_worldview_contains_section(self) -> None:
        v14 = _read_v14("domain_worldview.md")
        self.assertIn(_v13_sections()["## 一、世界观"].strip(), v14)

    def test_domain_character_contains_section(self) -> None:
        v14 = _read_v14("domain_character.md")
        self.assertIn(_v13_sections()["## 二、人物"].strip(), v14)

    def test_domain_storyline_contains_sections(self) -> None:
        v14 = _read_v14("domain_storyline.md")
        for key in ("## 三、故事线文件归属", "## 四、故事线", "## 五、事件组"):
            self.assertIn(_v13_sections()[key].strip(), v14)

    def test_common_rules_contains_public_subsections(self) -> None:
        v14 = _read_v14("common_rules.md")
        sub7 = _v13_subsections("## 七、规则")
        for key in (
            "### 7.1 核心关系", "### 7.2 ID 编号规范", "### 7.3 工作区文件地图",
            "### 7.5 关键规则", "### 7.6 增量构建原则", "### 7.7 创作原则",
            "### 7.9 回复格式",
        ):
            self.assertIn(sub7[key].strip(), v14, msg=f"公共规则缺 {key}")
        sub8 = _v13_subsections("## 八、连续增量循环与配比导航")
        for key in ("### 8.1 配比与计数", "### 8.3 与增量分流的协调（分流优先、配比让步）"):
            self.assertIn(sub8[key].strip(), v14, msg=f"公共规则缺 {key}")

    def test_orchestrator_contains_navigation_and_review_subsections(self) -> None:
        """编排专属段（7.4/7.8/8.2/8.4）在 orchestrator 中语义同源（逐字或适配）。"""
        v14 = _read_v14("orchestrator_system.md")
        sub8 = _v13_subsections("## 八、连续增量循环与配比导航")
        # 8.1/8.2/8.3/8.4 整节进 orchestrator（8.2 导航机械判定逐字）
        for key in (
            "### 8.1 配比与计数", "### 8.2 配比导航（系统指令，机械判定）",
            "### 8.3 与增量分流的协调（分流优先、配比让步）",
            "### 8.4 收束与 review 时机",
        ):
            self.assertIn(sub8[key].strip(), v14, msg=f"orchestrator 缺 {key}")
        sub7 = _v13_subsections("## 七、规则")
        # 7.2/7.3/7.5/7.7 也进 orchestrator（编排需要文件地图与 ID 规范）
        for key in (
            "### 7.2 ID 编号规范", "### 7.3 工作区文件地图",
            "### 7.5 关键规则", "### 7.7 创作原则",
        ):
            self.assertIn(sub7[key].strip(), v14, msg=f"orchestrator 缺 {key}")

    def test_full_coverage_no_orphan_section(self) -> None:
        """v13 每个节/子节必须被 v14 四文件之一覆盖（7.4/7.8 为适配版豁免逐字）。"""
        v14_all = "\n".join(
            _read_v14(n) for n in (
                "common_rules.md", "domain_worldview.md",
                "domain_character.md", "domain_storyline.md",
                "orchestrator_system.md",
            )
        )
        for title, body in _v13_sections().items():
            if title in ("## 七、规则", "## 八、连续增量循环与配比导航"):
                continue  # 子节级核对
            self.assertIn(body.strip(), v14_all, msg=f"v13 节未覆盖: {title}")
        for sec in ("## 七、规则", "## 八、连续增量循环与配比导航"):
            for title, body in _v13_subsections(sec).items():
                if title in ("### 7.4 委托参数", "### 7.8 review 审查（单次）"):
                    continue  # 编排适配版（orchestrator 领域委托参数 / review 分派）
                self.assertIn(
                    body.strip(), v14_all, msg=f"v13 子节未覆盖: {title}")


@unittest.skipUnless(_HAS_NESTED_GIT, "嵌套 harness 仓 git 历史不可用（fresh clone），v13 基准对齐校验跳过")
class SkillAlignmentTest(unittest.TestCase):
    """v14 skills 与 v13 initial/expand 的关键流程同源（DEC-005 抽查）。"""

    def test_expand_mode_b_body_migrated_to_character_build(self) -> None:
        """模式B 核心段落（融入方式表 + 功能性要求）逐字迁入 character-build。"""
        v13_expand = _git_show("skills/storybuilding-expand/SKILL.md")
        v14 = (_V14_PROMPTS.parent.parent / "skills" / "v14" / "character-build" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("| **前瞻登场** |", v14)
        self.assertIn("| **回溯嵌入** |", v14)
        self.assertIn("承担明确功能", v14)
        self.assertIn(v13_expand[v13_expand.index("### B.1"):v13_expand.index("### B.5")].strip()[:200], v14)

    def test_expand_mode_a_body_migrated_to_storyline_build(self) -> None:
        """模式A 核心段落（类型表 + 事件组数）逐字迁入 storyline-build。"""
        v14 = (_V14_PROMPTS.parent.parent / "skills" / "v14" / "storyline-build" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("| 主线（续延） |", v14)
        self.assertIn("- 主线：发展(8) + 终局(4)", v14)
        self.assertIn("- 暗线：悬念→冲突→危机→反转→揭露(5)", v14)

    def test_initial_constraints_migrated(self) -> None:
        """initial 硬约束（只生成主线/结局不可改/角色类型表）迁入对应 skill。"""
        storyline = (_V14_PROMPTS.parent.parent / "skills" / "v14" / "storyline-build" / "SKILL.md").read_text(encoding="utf-8")
        character = (_V14_PROMPTS.parent.parent / "skills" / "v14" / "character-build" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("**只生成主线（1 条）**", storyline)
        self.assertIn("不可更改", storyline)
        self.assertIn("| 主角 | 视点人物，核心叙事驱动者 |", character)


if __name__ == "__main__":
    unittest.main()
