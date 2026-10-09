"""storybuilding 系统提示词 §五契约测试（REQ-20261009-182730 FR-002 / AC-003）。

固化弹性化改写三要素：区间表述、节拍序列保留、类型词常用参考 + 自定义允许；
并回归断言旧的硬约束表述已删除。

REQ-20261009-224433 扩展：需求澄清前置（FR-003）与澄清共识回写 demand.md
（FR-004）的提示词契约，及架构清单层的 ask_user 挂载 / demand.md 白名单。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1] / "harnesses" / "repo"

_PROMPT = _REPO / "prompts" / "storybuilding_system.md"
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


class ClarifyContractTest(unittest.TestCase):
    """FR-003：首次构建前必须先澄清（ask_user 连问，可被用户跳过）。"""

    def test_clarify_gate_present(self) -> None:
        self.assertIn("需求澄清", _TEXT)
        self.assertIn("ask_user", _TEXT)
        self.assertIn("demand-clarification", _TEXT)

    def test_skip_semantics_present(self) -> None:
        self.assertIn("跳过澄清", _TEXT)

    def test_clarify_scoped_to_first_build(self) -> None:
        # 修订/增量场景不澄清——规则必须显式限定 storyline.md 不存在
        self.assertIn("storyline.md 不存在", _TEXT)


class DemandWritebackContractTest(unittest.TestCase):
    """FR-004：澄清共识按 demand.md 原结构合并回写，不动用户原文。"""

    def test_writeback_rule_present(self) -> None:
        self.assertIn("合并写回 demand.md", _TEXT)
        self.assertIn("只增补", _TEXT)

    def test_user_input_protected(self) -> None:
        self.assertIn("不删除或改写", _TEXT)


class ArchitectureClarifyTest(unittest.TestCase):
    """架构清单层：ask_user 工具挂载与 demand.md 写入白名单（FR-003/FR-004 载体）。"""

    def setUp(self) -> None:
        self.manifest = json.loads(
            (_REPO / "architecture.json").read_text(encoding="utf-8")
        )

    def _storybuilding(self) -> dict:
        return next(a for a in self.manifest["agents"] if a["name"] == "storybuilding")

    def test_storybuilding_mounts_ask_user(self) -> None:
        self.assertIn("ask_user", self._storybuilding()["tools"])

    def test_storybuilding_may_write_demand_md(self) -> None:
        self.assertIn("/demand.md", self._storybuilding()["write_permissions"])


if __name__ == "__main__":
    unittest.main()
