"""评分规则只读展示 API 测试（REQ-20260920-104714 / FR-002 / AC-003；v4 适配 REQ-20260921-210038）。

覆盖：
- 返回内容与 rubric 常量逐字段一致（单一事实源，防前端硬编码漂移）
- 校准状态字段存在且如实暴露草稿状态（DEC-006 明示要求）
- 五档锚点/纪律条款完整、词表字段不出现（DEC-001/009/016）
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class RubricApiTest(unittest.TestCase):
    def test_rubric_payload_matches_constants(self):
        from app.benchmark import api as bench_api
        from app.benchmark import rubric_v3

        resp = bench_api.get_rubric()

        self.assertEqual(resp["rubric_version"], rubric_v3.RUBRIC_VERSION)
        self.assertEqual(resp["calibration_status"], rubric_v3.CALIBRATION_STATUS)
        self.assertEqual(resp["anchor_status"], rubric_v3.ANCHOR_DRAFT_STATUS)
        self.assertEqual(resp["discipline_rules"], rubric_v3.DISCIPLINE_RULES)
        self.assertEqual(resp["dimensions"], rubric_v3.DIMENSIONS)
        self.assertEqual(resp["rule_delivery"], rubric_v3.RULE_DELIVERY_COMPLETE)

    def test_calibration_finalized_status_visible(self):
        """实测达标后的校准状态必须出现在响应里（DEC-006/007：界面明示的数据源）。"""
        from app.benchmark import api as bench_api

        resp = bench_api.get_rubric()
        self.assertIn("calibrated", resp["calibration_status"])
        self.assertIn("user_finalized", resp["anchor_status"])

    def test_dimensions_have_full_fields(self):
        """每个维度含判定问题 + 1-5 五档锚点；词表字段不出现（FR-002 展示完整性）。"""
        from app.benchmark import api as bench_api

        resp = bench_api.get_rubric()
        self.assertEqual(len(resp["dimensions"]), 5)
        for dim in resp["dimensions"]:
            with self.subTest(dim=dim["key"]):
                self.assertTrue(dim["question"])
                self.assertEqual(sorted(dim["anchors"].keys()), ["1", "2", "3", "4", "5"])
                self.assertNotIn("defect_tags", dim)

    def test_discipline_rules_finalized_six(self):
        """纪律条款六条全部下发（DEC-016 用户终审定稿，Rules 页展示数据源）。"""
        from app.benchmark import api as bench_api

        resp = bench_api.get_rubric()
        self.assertEqual(len(resp["discipline_rules"]), 6)
        self.assertTrue(all(rule.strip() for rule in resp["discipline_rules"]))


if __name__ == "__main__":
    unittest.main()
