from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.platform.agent.middleware.path_guard_middleware import normalize_workspace_write_path


class NormalizeWorkspaceWritePathTest(unittest.TestCase):
    def test_accepts_character_markdown_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            self.assertEqual(
                normalize_workspace_write_path("character/林映真.md", workspace),
                "/character/林映真.md",
            )
            self.assertEqual(
                normalize_workspace_write_path("/character/林映真.md", workspace),
                "/character/林映真.md",
            )
            self.assertEqual(
                normalize_workspace_write_path(r"character\林映真.md", workspace),
                "/character/林映真.md",
            )

    def test_accepts_worldview_and_review_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            self.assertEqual(normalize_workspace_write_path("worldview.md", workspace), "/worldview.md")
            self.assertEqual(normalize_workspace_write_path("/worldview.md", workspace), "/worldview.md")
            self.assertEqual(normalize_workspace_write_path("storyline.md", workspace), "/storyline.md")
            self.assertEqual(normalize_workspace_write_path("/storyline.md", workspace), "/storyline.md")
            self.assertEqual(normalize_workspace_write_path("review/chapter-01.md", workspace), "/review/chapter-01.md")
            self.assertEqual(normalize_workspace_write_path("/review/chapter-01.md", workspace), "/review/chapter-01.md")
            self.assertEqual(normalize_workspace_write_path("/review/storybuilding.md", workspace), "/review/storybuilding.md")
            self.assertEqual(normalize_workspace_write_path("/review/detail.md", workspace), "/review/detail.md")

    def test_rejects_v8_pipeline_leftover_paths(self) -> None:
        """FR-008（REQ-20260930-163019）：v8 多 Agent 流水线路径全部拒绝。

        outline.md / evaluation.md / novel / chapter / detail / state_log /
        storyline 目录均为 v8 遗留——单故事专家架构不产出，委托文本与
        白名单双重锁定（ba9d588 只清了委托文本，本测试锁定防线另一半）。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            rejected_paths = [
                "outline.md",
                "/outline.md",
                "evaluation.md",
                "/evaluation.md",
                "novel.md",
                "/novel.md",
                "chapter/chapter-01.md",
                "/chapter/chapter-01.md",
                "detail/chapter-01.md",
                "/detail/chapter-01.md",
                "state_log.md",
                "/state_log.md",
                "storyline/S01-代理觉醒.md",
                "/storyline/S01-代理觉醒.md",
                r"storyline\S01-代理觉醒.md",
            ]

            for path in rejected_paths:
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        normalize_workspace_write_path(path, workspace)

    def test_accepts_workspace_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir).resolve()
            target = workspace / "character" / "林映真.md"

            self.assertEqual(
                normalize_workspace_write_path(str(target), workspace),
                "/character/林映真.md",
            )
            self.assertEqual(
                normalize_workspace_write_path("\\\\?\\" + str(target), workspace),
                "/character/林映真.md",
            )

    def test_rejects_unsafe_or_out_of_scope_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            rejected_paths = [
                "../secret.md",
                "/../secret.md",
                "~/secret.md",
                "//server/share/a.md",
                "/character/../outline.md",
                "/character/a.txt",
                "/character/a/b.md",
                "/review.md",
                "/review/a.txt",
                "/review/a/b.md",
                "/evaluation.md",
                "/anything.md",
            ]

            for path in rejected_paths:
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        normalize_workspace_write_path(path, workspace)

    def test_rejects_workspace_external_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as external_dir:
            workspace = Path(workspace_dir).resolve()
            external = Path(external_dir).resolve() / "character" / "林映真.md"

            with self.assertRaises(ValueError):
                normalize_workspace_write_path(str(external), workspace)

    def test_rejects_empty_or_non_string_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)

            for path in ("", "   ", None, 123):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        normalize_workspace_write_path(path, workspace)


if __name__ == "__main__":
    unittest.main()
