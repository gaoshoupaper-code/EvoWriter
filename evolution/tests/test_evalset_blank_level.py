"""evalset 元信息头 blank_level 字段解析测试（REQ-20260920-150253 / FR-002 / AC-003）。

blank_level 是分档留白度的机读标注（full / semi / minimal），供按档聚合与
终审核对。解析约定与 parse_title 相同：只看文件头 HTML 注释块（前 300 字符）。
"""
from __future__ import annotations

import unittest

from app.common.evalset import parse_blank_level

_HEADER = """<!--
元信息（评估集预置，程序可解析）：
- mode: auto
- status: confirmed
- genre: 玄幻
- subtype: 热血升级
- title: 万器图录（玄幻·热血升级流）
- blank_level: {level}
- updated: 2026-09-20T00:00:00Z
-->

# 创作需求文档
"""


class ParseBlankLevelTest(unittest.TestCase):
    def test_parses_each_level(self):
        for level in ("full", "semi", "minimal"):
            with self.subTest(level=level):
                demand = _HEADER.format(level=level)
                self.assertEqual(parse_blank_level(demand), level)

    def test_missing_field_returns_none(self):
        demand = _HEADER.format(level="full").replace("- blank_level: full\n", "")
        self.assertIsNone(parse_blank_level(demand))

    def test_invalid_value_returns_none(self):
        demand = _HEADER.format(level="全填")
        self.assertIsNone(parse_blank_level(demand))

    def test_only_reads_head_block(self):
        """正文里出现的 blank_level 字样不得影响解析（只认文件头）。"""
        demand = _HEADER.format(level="semi") + "\n- blank_level: minimal\n"
        self.assertEqual(parse_blank_level(demand), "semi")

    def test_real_golden_cases_parse(self):
        """真实 golden 集 6 条全部可解析且档位与 STANDARD.md 分档映射一致。"""
        from app.common.evalset import list_cases, load_case_demand

        expected = {
            "case-001": "full",
            "case-002": "semi",
            "case-003": "minimal",
            "case-004": "semi",
            "case-005": "full",
            "case-006": "semi",
        }
        cases = list_cases(layer="golden")
        self.assertEqual(
            sorted(cases), sorted(expected.keys()),
            "golden 集 case 清单与分档映射不一致",
        )
        for case_id, level in expected.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(parse_blank_level(load_case_demand(case_id)), level)


if __name__ == "__main__":
    unittest.main()
