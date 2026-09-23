"""benchmark 配比达成核对测试（REQ-20260922-162823 FR-005 / DEC-011/013）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_DEMAND_FULL = """# 创作需求文档

## 核心层

- **篇幅档位**：21-50章
- **目标配比**（主线 / 支线 / 角色线 / 暗线）：主线5 / 支线1 / 角色线1 / 暗线0
"""

_DEMAND_MINIMAL = """# 创作需求文档

- **题材**：玄幻 · 轻松治愈
- **篇幅档位**：21-50章
"""

_DELIVERIES_INDEX_OK = {
    "主线 storyline": (
        "### storyline.md\n\n## 故事线一览表\n\n"
        "| ID | 名称 | 类型 | 状态 |\n|----|------|------|------|\n"
        "| S01 | 主线一 | 主线 | 活跃 |\n| S02 | 主线二 | 主线 | 活跃 |\n"
        "| S03 | 主线三 | 主线 | 活跃 |\n| S04 | 主线四 | 主线 | 活跃 |\n"
        "| S05 | 主线五 | 主线 | 活跃 |\n| S06 | 支线一 | 支线 | 活跃 |\n"
        "| S07 | 角色线一 | 角色线 | 活跃 |\n"
    ),
}

_DELIVERIES_INDEX_SHORT = {
    "主线 storyline": (
        "### storyline.md\n\n## 故事线一览表\n\n"
        "| ID | 名称 | 类型 | 状态 |\n|----|------|------|------|\n"
        "| S01 | 主线一 | 主线 | 活跃 |\n"
    ),
}

_DELIVERIES_NO_INDEX = {
    "主线 storyline": (
        "### storyline/S01-主线.md\n\n内容略\n\n### storyline/S02-支线.md\n\n内容略\n"
    ),
}


class QuotaCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.benchmark import scorer

        self.scorer = scorer

    def test_full_achieved(self) -> None:
        """full 档达标：一览表 5主1支1角 → passed=True（FR-005）。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_FULL, _DELIVERIES_INDEX_OK)
        self.assertEqual(rule["status"], "checked")
        self.assertTrue(rule["passed"])
        self.assertEqual(rule["target"]["主线"], 5)
        self.assertEqual(rule["actual"]["主线"], 5)
        self.assertIn("配比已达标", rule["summary"])

    def test_full_gap_reported(self) -> None:
        """未达标给差距明细。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_FULL, _DELIVERIES_INDEX_SHORT)
        self.assertFalse(rule["passed"])
        self.assertEqual(rule["gaps"], {"主线": 4, "支线": 1, "角色线": 1})

    def test_minimal_skipped(self) -> None:
        """minimal 档（配比留白）跳过核对（DEC-013）。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_MINIMAL, _DELIVERIES_INDEX_OK)
        self.assertEqual(rule["status"], "skipped_minimal")

    def test_index_missing_fallback_to_total(self) -> None:
        """一览表不可解析时按 storyline 文件标题总数兜底。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_FULL, _DELIVERIES_NO_INDEX)
        self.assertEqual(rule["status"], "checked_by_total")
        self.assertFalse(rule["passed"])
        self.assertEqual(rule["actual_total"], 2)

    def test_fallback_handles_leading_slash_headers(self) -> None:
        """拼接标题带前导斜杠（### /storyline/S01-…）时兜底计数不归零（pilot 修复）。"""
        deliveries = {
            "主线 storyline": (
                "### /storyline/S01-主线.md\n\n内容\n\n### /storyline/S02-支线.md\n\n内容\n"
            ),
        }
        rule = self.scorer.check_quota_attainment(_DEMAND_FULL, deliveries)
        self.assertEqual(rule["status"], "checked_by_total")
        self.assertEqual(rule["actual_total"], 2)

    def test_score_case_includes_rule_quota(self) -> None:
        """score_case 集成：结果含 rule_quota 字段（FR-005 ③，judge 全 mock）。"""
        fake = {"score": 4, "达标": ["…"], "不足": []}
        with patch.object(self.scorer, "_score_dimension", return_value=fake):
            result = self.scorer.score_case(
                _DEMAND_FULL, dict(_DELIVERIES_INDEX_OK, **{
                    "人物 character": "c" * 300, "世界观 worldview": "w" * 300}),
            )
        self.assertIn("rule_quota", result)
        self.assertEqual(result["rule_quota"]["status"], "checked")
        self.assertTrue(result["rule_quota"]["passed"])
        # 交付完整规则项同时在场（不回归）
        self.assertIn("rule_delivery", result)
        self.assertTrue(result["rule_delivery"]["passed"])


if __name__ == "__main__":
    unittest.main()
