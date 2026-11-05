"""Target task contracts reject ambiguous scope and mutable input identities."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from contracts.target_tasks import TaskInput, TargetSpec, TargetTaskRequest


class TargetContractTests(unittest.TestCase):
    def test_book_scope_covers_every_volume(self):
        target = TargetSpec(task_type="volume_outlines", volume_ids=[1, 2, 3])
        self.assertEqual(target.volume_ids, [1, 2, 3])
        with self.assertRaises(ValidationError):
            TargetSpec(task_type="volume_outlines", volume_ids=[1, 3])

    def test_chapter_outlines_require_four_distinct_volumes(self):
        self.assertEqual(
            TargetSpec(task_type="chapter_outlines", volume_ids=[2, 3, 4, 5]).volume_ids,
            [2, 3, 4, 5],
        )
        for volumes in ([1], [1, 2, 3], [1, 2, 3, 3], [0, 1, 2, 3]):
            with self.subTest(volumes=volumes), self.assertRaises(ValidationError):
                TargetSpec(task_type="chapter_outlines", volume_ids=volumes)

    def test_sparse_giant_scope_is_rejected_without_large_allocation(self):
        def bounded_range(*args):
            if args[-1] > 10000:
                raise MemoryError("scope validation attempted an unbounded allocation")
            return range(*args)
        with patch("contracts.target_tasks.range", side_effect=bounded_range, create=True):
            with self.assertRaises(ValidationError):
                TargetSpec(task_type="volume_outlines", volume_ids=[1, 10**12])

    def test_skills_require_the_executable_snapshot_fields(self):
        for skill in ({"files": {"SKILL.md": "x"}}, {"name": "x", "content": "x"},
                      {"stage": "text", "name": "x", "content": ""}):
            with self.subTest(skill=skill), self.assertRaises(ValidationError):
                TaskInput(skills=[skill], confirmation_rules="continue")
        value = TaskInput(skills=[{"stage": "volume", "name": "因果", "content": "衔接前因后果"}],
                          confirmation_rules="continue")
        self.assertEqual(value.skills[0]["stage"], "volume")

    def test_single_chapter_has_explicit_positive_chapter(self):
        self.assertEqual(TargetSpec(task_type="chapter_text", chapter=7).chapter, 7)
        for chapter in (None, 0, -1):
            with self.subTest(chapter=chapter), self.assertRaises(ValidationError):
                TargetSpec(task_type="chapter_text", chapter=chapter)

    def test_business_files_cannot_replace_control_files(self):
        for name in ("../a.md", "/tmp/a.md", "C:/a.md", ".writing/state.md", "a/../b.md", "x.py"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                TaskInput(files={name: "content"}, confirmation_rules="continue")

    def test_input_revision_freezes_content_and_settings(self):
        base = {"files": {"主题.md": "主题"}, "confirmation_rules": "continue"}
        first = TaskInput(**base)
        self.assertEqual(len(first.input_revision), 64)
        self.assertEqual(first.input_revision, TaskInput(**base).input_revision)
        other = TaskInput(**{**base, "confirmation_rules": "different"})
        self.assertNotEqual(first.input_revision, other.input_revision)
        with self.assertRaises(ValidationError):
            TaskInput(**base, input_revision="0" * 64)

    def test_internal_identity_is_required_and_unknown_fields_rejected(self):
        with self.assertRaises(ValidationError):
            TargetTaskRequest(
                task_id="run-1", attempt_id="attempt-1", source_commit="--unsafe",
                target={"task_type": "chapter_text", "chapter": 1},
                input={"confirmation_rules": "continue"},
            )
        with self.assertRaises(ValidationError):
            TargetSpec(task_type="chapter_text", chapter=1, source_root="/etc")


if __name__ == "__main__":
    unittest.main()
