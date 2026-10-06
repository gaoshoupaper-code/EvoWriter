from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.versioning import registry_repo
from app.versioning.middleware_projection import build_middleware_projection


class MiddlewareProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package_root = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
        # 三形态守卫：working 包现为 M2 清单形态（architecture.json，REQ-
        # 20261006-130414），历史提交可能是 v7 单专家形态（factory.py）或 v14
        # 实验形态（orchestrator.py）。清单形态走 project_manifest_stacks
        # （结构 = 清单声明，真相源）；旧形态走源码解析投影——按实际形态断言。
        manifest_file = cls.package_root / "architecture.json"
        if manifest_file.is_file():
            from contracts.architecture_manifest import ArchitectureManifest
            from app.versioning.middleware_projection import project_manifest_stacks

            manifest = ArchitectureManifest.model_validate(
                json.loads(manifest_file.read_text(encoding="utf-8"))
            )
            sources = {
                f"middleware/{p.name}": p.read_text(encoding="utf-8")
                for p in (cls.package_root / "middleware").glob("*.py")
                if p.name != "__init__.py"
            }
            cls.stacks = project_manifest_stacks(sources, manifest)
            cls.layout = "manifest"
            return
        paths = [
            "__init__.py",
            "subagents/storybuilding.py",
            "subagents/factory.py",
            "subagents/orchestrator.py",
            "subagents/reviewers/storybuilding.py",
        ]
        paths.extend(
            path.relative_to(cls.package_root).as_posix()
            for path in (cls.package_root / "middleware").glob("*.py")
        )
        cls.stacks = build_middleware_projection(
            {
                path: (cls.package_root / path).read_text(encoding="utf-8")
                for path in paths
                if (cls.package_root / path).is_file()
            }
        )
        cls.layout = (
            "v14" if (cls.package_root / "subagents" / "orchestrator.py").is_file() else "v7"
        )

    def test_projects_layout_lanes(self) -> None:
        """按 working 包形态产出对应泳道集合。"""
        if self.layout == "v14":
            self.assertEqual(
                set(self.stacks),
                {"orchestrator", "worldview", "character", "storyline", "storybuilding_review"},
            )
        else:
            # manifest 与 v7 同构：故事专家（main）+ 审查器（sub）
            self.assertEqual(set(self.stacks), {"storybuilding", "storybuilding_review"})

    def test_projects_real_class_names_and_hooks(self) -> None:
        """故事专家泳道：类名 + hooks 投影（v14 形态下无该泳道，跳过）。"""
        if "storybuilding" not in self.stacks:
            self.skipTest("working 包为 v14 多 Agent 形态，v7 泳道断言不适用")
        story = {item["class_name"]: item for item in self.stacks["storybuilding"]}
        self.assertEqual(
            story["StorylineSingleLineLimitMiddleware"]["hooks"],
            ["before_agent", "wrap_tool_call"],
        )
        # 清单 context_files=["demand.md"]（v7 assemble 同参）→ ContextAssembler 实挂
        self.assertIn("ContextAssemblerMiddleware", story)
        self.assertFalse(story["ArtifactValidationMiddleware"]["optional"])
        self.assertTrue(
            all(item["hooks"] for stack in self.stacks.values() for item in stack)
        )

    def test_story_expert_stack_has_guards_and_limits(self) -> None:
        """故事专家栈含护栏 + 修订上限 + 产物校验。"""
        if "storybuilding" not in self.stacks:
            self.skipTest("working 包为 v14 多 Agent 形态，v7 泳道断言不适用")
        story = {item["class_name"] for item in self.stacks["storybuilding"]}
        for required in (
            "ErrorRecoveryMiddleware",
            "FilesystemPathGuardMiddleware",
            "WriteResultInspectorMiddleware",
            "RevisionLimitMiddleware",
            "ArtifactValidationMiddleware",
            "StorylineSingleLineLimitMiddleware",
        ):
            self.assertIn(required, story)
        # meta 层概念随 v7 退役
        self.assertNotIn("MetaReadOnlyMiddleware", story)
        self.assertNotIn("GoalMiddleware", story)


class V14ProjectionSyntheticTest(unittest.TestCase):
    """v14 多 Agent 布局投影（合成装配源，不依赖 working 包形态）。"""

    STACKS = build_middleware_projection({
        "__init__.py": '''
def middleware_factory(agent_name):
    mw = [
        ErrorRecoveryMiddleware(),
        ReadCacheMiddleware(),
    ]
    mw.append(FilesystemPathGuardMiddleware())
    return mw

def assemble(ctx):
    return build_orchestrator_agent(ws, model, backend, middleware_factory,
                                    context_file_paths=["demand.md"])
''',
        "subagents/orchestrator.py": '''
def build_orchestrator_agent(workspace_root, model, backend, middleware_factory, *,
                             style_suffix=None, context_file_paths=None,
                             checkpointer=None):
    orchestrator_mw: list = list(middleware_factory("orchestrator"))
    orchestrator_mw.append(QuotaConvergenceMiddleware(workspace_root, quota_target,
                                                      max_model_calls=48))
    orchestrator_mw.append(RevisionLimitMiddleware(max_revisions=2,
                                                   review_name="review-storybuilding"))
    if context_file_paths:
        orchestrator_mw.append(ContextAssemblerMiddleware(workspace_root,
                                                          file_paths=context_file_paths,
                                                          context_label="创作需求"))
    storyline_mw: list = list(middleware_factory("storyline-subagent"))
    storyline_mw.append(StorylineSingleLineLimitMiddleware(workspace_root,
                                                           max_new_lines=16,
                                                           reset_per_invocation=False))
    return None
''',
        "subagents/reviewers/storybuilding.py": '''
def build_storybuilding_reviewer(workspace_root, middleware):
    review_middleware: list = list(middleware)
    review_middleware.append(ArtifactValidationMiddleware(
        artifact_paths=["/review/storybuilding.md"]))
    return {"middleware": review_middleware}
''',
    })

    def test_five_v14_lanes(self) -> None:
        self.assertEqual(
            set(self.STACKS),
            {"orchestrator", "worldview", "character", "storyline", "storybuilding_review"},
        )

    def test_orchestrator_appends_loop_guards_and_context(self) -> None:
        orch = {m["class_name"]: m for m in self.STACKS["orchestrator"]}
        self.assertIn("QuotaConvergenceMiddleware", orch)
        self.assertEqual(orch["RevisionLimitMiddleware"]["params"]["max_revisions"], 2)
        # __init__.assemble 传 context_file_paths → ContextAssembler 实挂（非可选）
        self.assertFalse(orch["ContextAssemblerMiddleware"]["optional"])

    def test_storyline_appends_line_guard_worldview_character_plain(self) -> None:
        storyline = {m["class_name"] for m in self.STACKS["storyline"]}
        self.assertIn("StorylineSingleLineLimitMiddleware", storyline)
        self.assertEqual(
            {m["class_name"] for m in self.STACKS["worldview"]},
            {m["class_name"] for m in self.STACKS["character"]},
        )
        self.assertNotIn(
            "StorylineSingleLineLimitMiddleware",
            {m["class_name"] for m in self.STACKS["worldview"]},
        )

    def test_reviewer_extends_common_with_validation(self) -> None:
        review = {m["class_name"] for m in self.STACKS["storybuilding_review"]}
        self.assertIn("ArtifactValidationMiddleware", review)
        self.assertIn("ErrorRecoveryMiddleware", review)


class RegistryCleanupTest(unittest.TestCase):
    def test_prunes_unbound_history_and_binds_production(self) -> None:
        registry = {
            "schema_version": 1,
            "production": 3,
            "versions": [
                {"version": 1, "parent_version": None, "executable": False},
                {"version": 2, "parent_version": 1, "executable": False},
                {"version": 3, "parent_version": 2, "executable": False},
            ],
            "rollback_log": [{"from": 3, "to": 1}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with patch("app.versioning.registry_repo._registry_path", return_value=path):
                result = registry_repo.prune_unexecutable_history_and_bind_production("abc1234")
                saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(result["removed_versions"], [1, 2])
        self.assertEqual(saved["rollback_log"], [])
        self.assertEqual(
            saved["versions"],
            [
                {
                    "version": 3,
                    "parent_version": None,
                    "executable": True,
                    "commit_hash": "abc1234",
                }
            ],
        )

    def test_keeps_already_bound_history(self) -> None:
        registry = {
            "schema_version": 1,
            "production": 2,
            "versions": [
                {"version": 1, "parent_version": None, "commit_hash": "old"},
                {"version": 2, "parent_version": 1, "commit_hash": "current"},
            ],
            "rollback_log": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with patch("app.versioning.registry_repo._registry_path", return_value=path):
                result = registry_repo.prune_unexecutable_history_and_bind_production("ignored")

        self.assertFalse(result["changed"])
        self.assertEqual(result["removed_versions"], [])


if __name__ == "__main__":
    unittest.main()
