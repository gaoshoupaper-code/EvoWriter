"""elements_api 布局探测测试（v7 静态映射 / v14 目录推导，v14 形态适配）。

覆盖：
- _agent_specs_for_commit：v14 判定（orchestrator prompt 存在）+ 领域 agent
  从 domain_*.md 推导（顺序稳定）+ reviewer 收尾；旧 commit 回退 v7 两泳道
- read_prompt_body：领域 agent 多文件拼接（公共规则 + 分隔线 + 领域文件）
- _agent_skills：v7 前缀归属 / v14 全部挂 orchestrator、reviewer 恒空
- build_elements_view：v14 五 agent 视图 + orchestrator 四条委托关系
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.versioning import elements_api


def _mount(cls, params=None):
    return {"class_name": cls, "params": params or {}, "group": "agent",
            "hooks": ["before_model"], "optional": False}


class SpecsDetectionTest(unittest.TestCase):
    def test_v14_layout_derives_domains(self):
        v14_files = [
            "prompts/v14/ALIGNMENT.md",
            "prompts/v14/common_rules.md",
            "prompts/v14/domain_character.md",
            "prompts/v14/domain_storyline.md",
            "prompts/v14/domain_worldview.md",
            "prompts/v14/orchestrator_system.md",
        ]
        with patch.object(elements_api, "_file_exists_at_commit", return_value=True), \
             patch.object(elements_api, "_list_files_at_commit", return_value=v14_files):
            specs, layout = elements_api._agent_specs_for_commit("c14")
        self.assertEqual(layout, "v14")
        self.assertEqual(
            [(name, kind) for name, kind, _ in specs],
            [
                ("orchestrator", "orchestrator"),
                ("character", "domain"),   # domain_*.md 文件名排序
                ("storyline", "domain"),
                ("worldview", "domain"),
                ("storybuilding_review", "reviewer"),
            ],
        )
        # 领域 spec = 公共规则 + 领域文件（镜像运行时拼接）
        by_name = dict(specs and [(s[0], s[2]) for s in specs])
        self.assertEqual(
            by_name["worldview"],
            ("prompts/v14/common_rules.md", "prompts/v14/domain_worldview.md"),
        )

    def test_v7_fallback_when_no_v14_prompt(self):
        with patch.object(elements_api, "_file_exists_at_commit", return_value=False):
            specs, layout = elements_api._agent_specs_for_commit("c8")
        self.assertEqual(layout, "v7")
        self.assertEqual(
            [(name, kind) for name, kind, _ in specs],
            [("storybuilding", "story_expert"), ("storybuilding_review", "reviewer")],
        )


class PromptBodyJoinTest(unittest.TestCase):
    def test_domain_prompt_joins_with_separator(self):
        content = {
            ("c", "prompts/v14/common_rules.md"): "COMMON",
            ("c", "prompts/v14/domain_worldview.md"): "DOMAIN",
            ("c", "prompts/storybuilding_system.md"): "SINGLE",
        }
        with patch.object(elements_api, "show_file", side_effect=lambda c, p: content[(c, p)]):
            joined = elements_api.read_prompt_body(
                "c", ("prompts/v14/common_rules.md", "prompts/v14/domain_worldview.md"),
            )
            single = elements_api.read_prompt_body("c", ("prompts/storybuilding_system.md",))
        self.assertEqual(joined, "COMMON\n\n---\n\nDOMAIN")
        self.assertEqual(single, "SINGLE")


class SkillsAttributionTest(unittest.TestCase):
    def _skills(self):
        return [
            {"path": "skills/storybuilding/draft"},
            {"path": "skills/v14/orchestrator-cycling"},
            {"path": "skills/v14/worldview-build"},
        ]

    def test_v7_prefix_attribution(self):
        owned = elements_api._agent_skills(self._skills(), "storybuilding", "story_expert", "v7")
        self.assertEqual([s["path"] for s in owned], ["skills/storybuilding/draft"])
        self.assertEqual(elements_api._agent_skills(self._skills(), "storybuilding_review", "reviewer", "v7"), [])

    def test_v14_all_skills_on_orchestrator(self):
        orch = elements_api._agent_skills(self._skills(), "orchestrator", "orchestrator", "v14")
        self.assertEqual(
            [s["path"] for s in orch],
            ["skills/v14/orchestrator-cycling", "skills/v14/worldview-build"],
        )
        self.assertEqual(
            elements_api._agent_skills(self._skills(), "worldview", "domain", "v14"), [],
        )


class ElementsViewV14Test(unittest.TestCase):
    def test_v14_view_builds_five_agents_and_relations(self):
        v14_files = [
            "prompts/v14/common_rules.md",
            "prompts/v14/domain_character.md",
            "prompts/v14/domain_storyline.md",
            "prompts/v14/domain_worldview.md",
            "prompts/v14/orchestrator_system.md",
        ]
        content = {
            ("c14", "prompts/v14/orchestrator_system.md"): "ORCH",
            ("c14", "prompts/v14/common_rules.md"): "COMMON",
            ("c14", "prompts/v14/domain_worldview.md"): "WORLD",
            ("c14", "prompts/storybuilding_review.md"): "REVIEW",
        }
        skills = [{"path": "skills/v14/worldview-build"}, {"path": "skills/v14/character-build"}]
        stacks = {"orchestrator": [_mount("QuotaConvergenceMiddleware")], "worldview": []}

        with patch.object(elements_api, "_version_to_commit", return_value="c14"), \
             patch.object(elements_api, "_file_exists_at_commit", return_value=True), \
             patch.object(elements_api, "_list_files_at_commit", return_value=v14_files), \
             patch.object(elements_api, "show_file", side_effect=lambda c, p: content[(c, p)]), \
             patch.object(elements_api, "_build_skill_infos", return_value=skills), \
             patch.object(elements_api, "_build_middleware_stacks", return_value=stacks), \
             patch.object(elements_api, "_build_tool_infos", return_value=[]):
            view = elements_api.build_elements_view(14)

        self.assertEqual(
            [(a["name"], a["kind"]) for a in view["agents"]],
            [
                ("orchestrator", "orchestrator"),
                ("character", "domain"),
                ("storyline", "domain"),
                ("worldview", "domain"),
                ("storybuilding_review", "reviewer"),
            ],
        )
        by_name = {a["name"]: a for a in view["agents"]}
        # 领域 prompt = 公共规则 + 分隔线 + 领域文件
        self.assertEqual(
            by_name["worldview"]["prompt"]["body"], "COMMON\n\n---\n\nWORLD",
        )
        self.assertEqual(by_name["orchestrator"]["prompt"]["body"], "ORCH")
        # v14 技能全部挂 orchestrator；orchestrator 有专属 middleware 栈
        self.assertEqual(len(by_name["orchestrator"]["skills"]), 2)
        self.assertEqual(by_name["character"]["skills"], [])
        self.assertEqual(
            by_name["orchestrator"]["middlewares"][0]["class_name"], "QuotaConvergenceMiddleware",
        )
        # 委托关系：orchestrator → 3 领域 + 审查
        self.assertEqual(
            [(r["from"], r["to"]) for r in view["subagent_relations"]],
            [
                ("orchestrator", "character"),
                ("orchestrator", "storyline"),
                ("orchestrator", "worldview"),
                ("orchestrator", "storybuilding_review"),
            ],
        )

    def test_v7_view_falls_back(self):
        with patch.object(elements_api, "_version_to_commit", return_value="c8"), \
             patch.object(elements_api, "_file_exists_at_commit", return_value=False), \
             patch.object(elements_api, "_build_skill_infos", return_value=[]), \
             patch.object(elements_api, "_build_middleware_stacks", return_value={}), \
             patch.object(elements_api, "_build_tool_infos", return_value=[]):
            view = elements_api.build_elements_view(8)
        self.assertEqual(
            [a["name"] for a in view["agents"]], ["storybuilding", "storybuilding_review"],
        )
        self.assertEqual(
            [(r["from"], r["to"]) for r in view["subagent_relations"]],
            [("storybuilding", "storybuilding_review")],
        )


if __name__ == "__main__":
    unittest.main()
