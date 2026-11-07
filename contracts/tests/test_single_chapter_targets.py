"""New single-chapter targets coexist with the original four-volume records."""
import unittest

from pydantic import ValidationError

from contracts.target_tasks import TargetSpec


class SingleChapterTargetTests(unittest.TestCase):
    def test_single_outline_declares_its_chapter_and_volume(self):
        target = TargetSpec(task_type="chapter_outlines", volume_ids=[2], chapter=25)
        self.assertEqual(target.chapter, 25)
        self.assertEqual(target.volume_ids, [2])

    def test_single_outline_rejects_missing_or_ambiguous_volume(self):
        for volumes in ([], [1, 2], [1, 2, 3, 4]):
            with self.subTest(volumes=volumes), self.assertRaises(ValidationError):
                TargetSpec(task_type="chapter_outlines", volume_ids=volumes, chapter=25)

    def test_legacy_four_volume_target_remains_unchanged(self):
        target = TargetSpec(task_type="chapter_outlines", volume_ids=[1, 2, 3, 4])
        self.assertIsNone(target.chapter)

    def test_book_can_freeze_twenty_chapters_per_volume(self):
        target = TargetSpec(task_type="volume_outlines", volume_ids=[1, 2, 3], chapters_per_volume=20)
        self.assertEqual(target.chapters_per_volume, 20)

    def test_fixed_chapter_count_is_exclusive_to_book_planning(self):
        with self.assertRaises(ValidationError):
            TargetSpec(task_type="chapter_text", volume_ids=[1], chapter=1, chapters_per_volume=20)

    def test_new_optional_field_does_not_change_legacy_request_fingerprints(self):
        legacy = TargetSpec(task_type="chapter_outlines", volume_ids=[1, 2, 3, 4]).model_dump(mode="json")
        self.assertNotIn("chapters_per_volume", legacy)
        current = TargetSpec(task_type="volume_outlines", volume_ids=[1, 2, 3], chapters_per_volume=20)
        self.assertEqual(current.model_dump(mode="json")["chapters_per_volume"], 20)


if __name__ == "__main__":
    unittest.main()
