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
        paths = [
            "__init__.py",
            "subagents/storybuilding.py",
            "subagents/factory.py",
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
            }
        )

    def test_projects_two_v7_lanes(self) -> None:
        """v7 两泳道：故事专家（顶层）+ 审查器，无 meta/多代理泳道。"""
        self.assertEqual(set(self.stacks), {"storybuilding", "storybuilding_review"})

    def test_projects_real_class_names_and_hooks(self) -> None:
        story = {item["class_name"]: item for item in self.stacks["storybuilding"]}
        self.assertEqual(
            story["StorylineSingleLineLimitMiddleware"]["hooks"],
            ["before_agent", "wrap_tool_call"],
        )
        # v7：__init__.assemble 传 context_file_paths=["demand.md"] → ContextAssembler 实挂
        self.assertIn("ContextAssemblerMiddleware", story)
        self.assertFalse(story["ArtifactValidationMiddleware"]["optional"])
        self.assertTrue(
            all(item["hooks"] for stack in self.stacks.values() for item in stack)
        )

    def test_story_expert_stack_has_guards_and_limits(self) -> None:
        """故事专家栈含护栏 + 修订上限 + 产物校验（v7 顶层装配语义）。"""
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
