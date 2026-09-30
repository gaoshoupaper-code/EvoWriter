"""benchmark 配比达成核对测试（REQ-20260930-002231 FR-012：单文件区块口径）。

承接 REQ-20260922-162823 FR-005 / DEC-011/013 的三态语义：
  skipped_minimal（demand 无配比）/ checked（区块头类型分布）/ checked_by_total（兜底总数）。
"""

from __future__ import annotations

import sys
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


def _storyline_md(names: list[tuple[str, str]]) -> str:
    """构造新格式 storyline.md 交付文本（### storyline.md 拼接头 + 线区块）。"""
    blocks = "\n\n".join(
        f"## {name} · {type_} · 活跃\n- 主要地点：某处\n- 全局走向：略\n\n"
        f"| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |\n"
        f"|------|------|------|------|------|------|------|------|\n"
        f"| T1 | 事件一 | 冲突 | 发展 | 某处 | 某人 | | 略 |"
        for name, type_ in names
    )
    return f"### storyline.md\n\n# 故事核心\n\n- Logline：略\n\n{blocks}\n"


_DELIVERIES_OK = {
    "主线 storyline": _storyline_md([
        ("主线一", "主线"), ("主线二", "主线"), ("主线三", "主线"),
        ("主线四", "主线"), ("主线五", "主线"),
        ("支线一", "支线"), ("角色线一", "角色线"),
    ]),
}

_DELIVERIES_SHORT = {
    "主线 storyline": _storyline_md([("主线一", "主线")]),
}

_DELIVERIES_NO_BLOCKS = {
    "主线 storyline": "### storyline.md\n\n# 故事核心\n\n- Logline：略\n（无任何线区块）",
}


class QuotaCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.benchmark import scorer

        self.scorer = scorer

    def test_full_achieved(self) -> None:
        """full 档达标：区块头 5主1支1角 → passed=True。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_FULL, _DELIVERIES_OK)
        self.assertEqual(rule["status"], "checked")
        self.assertTrue(rule["passed"])
        self.assertEqual(rule["target"]["主线"], 5)
        self.assertEqual(rule["actual"]["主线"], 5)
        self.assertIn("配比已达标", rule["summary"])

    def test_full_gap_reported(self) -> None:
        """未达标给差距明细。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_FULL, _DELIVERIES_SHORT)
        self.assertFalse(rule["passed"])
        self.assertEqual(rule["gaps"], {"主线": 4, "支线": 1, "角色线": 1})

    def test_minimal_skipped(self) -> None:
        """minimal 档（配比留白）跳过核对（DEC-013）。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_MINIMAL, _DELIVERIES_OK)
        self.assertEqual(rule["status"], "skipped_minimal")

    def test_no_blocks_fallback_to_total(self) -> None:
        """无区块头时按总数口径兜底（count=0，真实反映解析不出任何线）。"""
        rule = self.scorer.check_quota_attainment(_DEMAND_FULL, _DELIVERIES_NO_BLOCKS)
        self.assertEqual(rule["status"], "checked_by_total")
        self.assertFalse(rule["passed"])
        self.assertEqual(rule["actual_total"], 0)

    def test_score_case_includes_rule_quota(self) -> None:
        """score_case 集成：结果含 rule_quota 字段（judge 全 mock）。"""
        fake = {"score": 4, "达标": ["…"], "不足": []}
        single_judge = [{"config_id": 1, "name": "单评", "model": "m", "fingerprint": "f"}]
        with patch.object(self.scorer, "_score_dimension", return_value=fake):
            result = self.scorer.score_case(
                _DEMAND_FULL, dict(_DELIVERIES_OK, **{
                    "人物 character": "c" * 300, "世界观 worldview": "w" * 300}),
                single_judge,
            )
        self.assertIn("rule_quota", result)
        self.assertEqual(result["rule_quota"]["status"], "checked")
        self.assertTrue(result["rule_quota"]["passed"])
        # 交付完整规则项同时在场（不回归）
        self.assertIn("rule_delivery", result)
        self.assertTrue(result["rule_delivery"]["passed"])


if __name__ == "__main__":
    unittest.main()
