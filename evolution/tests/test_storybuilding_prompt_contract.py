"""storybuilding 系统提示词 §五契约测试（REQ-20261009-182730 FR-002 / AC-003）。

固化弹性化改写三要素：区间表述、节拍序列保留、类型词常用参考 + 自定义允许；
并回归断言旧的硬约束表述已删除。
"""

from __future__ import annotations

import unittest
from pathlib import Path

_PROMPT = (
    Path(__file__).resolve().parents[1]
    / "harnesses" / "repo" / "prompts" / "storybuilding_system.md"
)
_TEXT = _PROMPT.read_text(encoding="utf-8")

# §5.1 建议节拍序列原文（改写不得动）
_BEATS = (
    "发展 8：冲突→危机→反转→悬念→冲突→危机→反转→揭露；终局 4：危机→冲突→反转→胜利",
    "发展 4：危机→冲突→危机→冲突；终局 2：冲突→胜利",
    "弧光：冲突→危机→冲突→危机→反转",
    "悬念→冲突→危机→反转→揭露",
)


class PromptElasticIntervalTest(unittest.TestCase):
    def test_interval_wording_present(self) -> None:
        for wording in ("8~15（经验值 ~12）", "4~9（经验值 ~6）", "3~8（经验值 ~5）"):
            self.assertIn(wording, _TEXT)

    def test_beat_sequences_preserved(self) -> None:
        for beat in _BEATS:
            self.assertIn(beat, _TEXT)

    def test_hard_constraint_wording_removed(self) -> None:
        self.assertNotIn("都会被系统弹回", _TEXT)
        self.assertNotIn("数量模板（非交汇计数，系统写前校验）", _TEXT)


class PromptTypeWordsTest(unittest.TestCase):
    def test_reference_wording_present(self) -> None:
        self.assertIn("常用参考", _TEXT)
        self.assertIn("贴切的自定义类型词", _TEXT)

    def test_seven_reference_words_table_intact(self) -> None:
        for word in ("冲突", "危机", "反转", "揭露", "悬念", "胜利", "交汇"):
            self.assertIn(f"| {word} |", _TEXT)


if __name__ == "__main__":
    unittest.main()
