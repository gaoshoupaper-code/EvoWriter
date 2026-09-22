"""v7 进化端同步验收测试（REQ-20260920-150149 FR-103/FR-104 → AC-103/AC-104）。

跑法（evolution 目录）：python -m pytest tests/test_v7_evolution_sync.py -v

覆盖：
  - STATIC_BLUEPRINT 为单 Agent 认知地图：无多 Agent 表述、含两泳道与记忆退役标注
  - 要素视图常量：2 泳道（故事专家 + 审查器）、唯一委托关系
  - dossier 阶段词表与交付映射：storybuilding 单阶段、三件套交付
  - 错题库下线：进化链路三文件零 problem_kb 依赖、无轨迹注入
"""
from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from app.evolve.agent import prompt as evolve_prompt

# 蓝图中不允许出现的多 Agent 表述（用户可见面术语清理，DEC-007）
_FORBIDDEN_BLUEPRINT_TERMS = [
    "interview",
    "detail_outline",
    "detail-outline",
    "五岗位",
    "九个要素",
    "meta 层 5 个",
    "subagents 列表（GP",
]
# 蓝图中必须出现的 v7 表述
_REQUIRED_BLUEPRINT_TERMS = [
    "单故事专家",
    "两泳道",
    "故事专家（storybuilding",
    "审查器",
    "已冻结休眠",        # 记忆退役标注（DEC-003）
    "demand.md",          # 表单直入（DEC-009）
    "RevisionLimit",     # 修订上限护栏叙事
]


class TestBlueprintV7(unittest.TestCase):
    def test_blueprint_no_multi_agent_vocabulary(self):
        for term in _FORBIDDEN_BLUEPRINT_TERMS:
            self.assertNotIn(
                term, evolve_prompt.STATIC_BLUEPRINT,
                f"蓝图不应再含多 Agent 表述: {term}",
            )

    def test_blueprint_has_v7_vocabulary(self):
        for term in _REQUIRED_BLUEPRINT_TERMS:
            self.assertIn(term, evolve_prompt.STATIC_BLUEPRINT, f"蓝图应含: {term}")

    def test_blueprint_no_multi_agent_pipeline_words(self):
        """进化和 subagent 委托叙事改为单专家 + reviewer。"""
        bp = evolve_prompt.STATIC_BLUEPRINT
        self.assertNotIn("interview 子代理", bp)
        self.assertNotIn("正文写作", bp)
        self.assertNotIn("细纲生成", bp)

    def test_prompt_builder_drops_trajectories(self):
        """v7：问题知识库下线，无 trajectories 占位符/参数。"""
        self.assertNotIn("TRAJECTORIES", dir(evolve_prompt))
        import inspect

        sig = inspect.signature(evolve_prompt.evolve_system_prompt)
        self.assertNotIn("trajectories_summary", sig.parameters)
        prompt = evolve_prompt.evolve_system_prompt("s1", "t1", "摘要")
        self.assertIn("当前 session", prompt)
        self.assertIn("s1", prompt)


class TestElementsViewConstants(unittest.TestCase):
    def test_two_lanes(self):
        from app.versioning import elements_api

        names = [spec[0] for spec in elements_api._AGENT_SPECS]
        self.assertEqual(names, ["storybuilding", "storybuilding_review"])
        kinds = [spec[1] for spec in elements_api._AGENT_SPECS]
        self.assertEqual(kinds, ["story_expert", "reviewer"])
        self.assertEqual(
            elements_api._SUBAGENT_ROLE_MAP["storybuilding"], "故事专家（剧情大纲设计）"
        )
        self.assertEqual(len(elements_api._ASSEMBLY_SOURCE_PATHS), 4)

    def test_projection_two_lanes_from_v7_package(self):
        from app.versioning.middleware_projection import build_middleware_projection

        pkg = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
        # v14 实验包守卫（REQ-20260922-162823）：双架构实验期 working 包为多 Agent
        # 形态（无 factory.py / 单专家装配），两泳道投影断言不适用；rollback 后恢复。
        if not (pkg / "subagents" / "factory.py").is_file():
            self.skipTest(
                "working 包为 v14 多 Agent 实验形态，v7/v13 两泳道投影断言不适用"
                "（REQ-20260922-162823 实验期；rollback 后自动恢复）"
            )
        paths = ["__init__.py", "subagents/storybuilding.py", "subagents/factory.py",
                 "subagents/reviewers/storybuilding.py"]
        paths += [p.relative_to(pkg).as_posix() for p in (pkg / "middleware").glob("*.py")]
        stacks = build_middleware_projection(
            {p: (pkg / p).read_text(encoding="utf-8") for p in paths}
        )
        self.assertEqual(set(stacks), {"storybuilding", "storybuilding_review"})


class TestProblemKbOffline(unittest.TestCase):
    """AC-104：进化链路对错题库零依赖（模块级 + 注入面）。"""

    def test_evolve_repo_no_problem_kb_wiring(self):
        src = Path("app/evolve/evolve_repo.py").read_text(encoding="utf-8")
        self.assertNotIn("problem_kb", src)
        self.assertNotIn("_try_assign_point_ownership", src)

    def test_evolve_agent_no_problem_kb_injection(self):
        agent_mod = importlib.import_module("app.evolve.agent.agent")
        self.assertFalse(hasattr(agent_mod, "_format_similar_trajectories"))
        src = Path("app/evolve/agent/agent.py").read_text(encoding="utf-8")
        self.assertNotIn("problem_kb", src)


if __name__ == "__main__":
    unittest.main()
