"""storybuilding 提示词结构契约测试（REQ-20261010-000638 FR-007 / AC-007 合并版）。

底座 = 生产 v42 谱系（specs 三层架构 + confirm 拍板流）。合并版断言：
  - 节拍序列从生成侧退场（specs 5.1 无「建议节拍序列」列，改为弹性区间）
  - specs 六字段（含设计原则钉死）、槽位形态、十列事件表、张力/爽点五档
  - hooks.md 九列登记（H{n}/期待分层/推进环节/已放弃备注）
  - 观测配置承载节拍诊断清单与爽点间隔默认值（参数归 harness，DEC-004/013）
  - review 提示词含节奏体检报告职责；架构清单挂载 hooks 护栏与 pacing_report
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
_SYSTEM = (_REPO / "prompts" / "storybuilding_system.md").read_text(encoding="utf-8")
_REVIEW = (_REPO / "prompts" / "storybuilding_review.md").read_text(encoding="utf-8")
_SPECS = (_REPO / "skills" / "storybuilding-specs" / "SKILL.md").read_text(encoding="utf-8")
_INITIAL = (_REPO / "skills" / "storybuilding-initial" / "SKILL.md").read_text(encoding="utf-8")
_EXPAND_S = (_REPO / "skills" / "storybuilding-expand-storyline" / "SKILL.md").read_text(encoding="utf-8")
_CONFIG = json.loads(
    (_REPO / "middleware" / "storyline_observation_config.json").read_text(encoding="utf-8")
)
_ARCH = json.loads((_REPO / "architecture.json").read_text(encoding="utf-8"))

# 旧生成用节拍序列原文（生成侧必须退场）
_RETIRED_BEATS = (
    "发展 8：冲突→危机→反转→悬念→冲突→危机→反转→揭露；终局 4：危机→冲突→反转→胜利",
    "弧光：冲突→危机→冲突→危机→反转",
)


class GenerativeBeatsRetiredTest(unittest.TestCase):
    def test_no_generative_beat_columns_in_specs(self) -> None:
        self.assertNotIn("建议节拍序列", _SPECS)
        self.assertNotIn("数量是硬约束", _SPECS)
        self.assertNotIn("都会被系统弹回", _SPECS)

    def test_old_beat_sequences_retired(self) -> None:
        for beat in _RETIRED_BEATS:
            self.assertNotIn(beat, _SPECS)
            self.assertNotIn(beat, _SYSTEM)


class SpecsSchemaTest(unittest.TestCase):
    """六字段/槽位形态/十列/张力爽点/九列登记进 specs（唯一内容真相源）。"""

    def test_core_six_fields_with_design_principle(self) -> None:
        for field in ("Logline", "设计原则", "核心主题", "类型基调", "节奏曲线", "最终结局"):
            self.assertIn(field, _SPECS)
        self.assertIn("六字段", _SPECS)
        self.assertIn("初构落盘后钉死", _SPECS)
        self.assertIn("草稿基线", _SPECS)

    def test_shape_slots_wording(self) -> None:
        self.assertIn("首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5", _SPECS)
        self.assertIn("形态先行", _INITIAL)

    def test_ten_column_event_table(self) -> None:
        for col in ("| 时序 |", "| 张力 |", "| 爽点 |", "| 描述 |"):
            self.assertIn(col, _SPECS)
        self.assertIn("十列", _EXPAND_S)

    def test_tension_five_levels_and_payoff(self) -> None:
        for level in ("日常蓄力", "小波澜", "正面对抗", "临界抉择", "峰值爆点"):
            self.assertIn(level, _SPECS)
        self.assertIn("三小一大", _SPECS)

    def test_hooks_ledger_nine_columns(self) -> None:
        self.assertIn("八、钩子登记", _SPECS)
        for token in ("H{n}", "主线大期待", "线级期待", "事件钩子", "推进即记账", "已放弃"):
            self.assertIn(token, _SPECS)

    def test_elastic_interval_wording(self) -> None:
        for wording in ("8~15（经验值 ~12）", "4~9（经验值 ~6）", "3~8（经验值 ~5）"):
            self.assertIn(wording, _SPECS)


class SystemFlowTest(unittest.TestCase):
    """系统提示词：设计原则进方案包 + 体检报告注入说明。"""

    def test_design_principle_in_direction_packages(self) -> None:
        self.assertIn("设计原则", _SYSTEM)
        self.assertIn("关键锚点", _SYSTEM)

    def test_health_report_protocol(self) -> None:
        self.assertIn("节奏体检报告", _SYSTEM)
        self.assertIn("节奏体检报告", _REVIEW)
        self.assertIn("语义判断", _REVIEW)
        self.assertIn("hooks.md", _REVIEW)


class ObservationConfigStructureTest(unittest.TestCase):
    def test_payoff_rules_defaults(self) -> None:
        rules = _CONFIG["payoff_rules"]
        self.assertEqual(rules["small_gap_max"], 3)
        self.assertEqual(rules["big_gap_max"], 8)
        self.assertEqual(rules["big_payoff_min_tension"], 4)

    def test_beat_checklist_present(self) -> None:
        ids = {item["id"] for item in _CONFIG["beat_checklist"]}
        self.assertGreaterEqual(len(ids), 4)
        self.assertIn("midpoint_reversal", ids)

    def test_count_ranges_match_specs_intervals(self) -> None:
        for line_type, (lo, hi) in _CONFIG["count_ranges"].items():
            self.assertIn(f"| {line_type} | {lo}~{hi}", _SPECS)


class ArchitectureMountTest(unittest.TestCase):
    def test_guards_and_pacing_mounted(self) -> None:
        story = next(a for a in _ARCH["agents"] if a["name"] == "storybuilding")
        review = next(a for a in _ARCH["agents"] if a["name"] == "storybuilding_review")
        self.assertIn("hooks_contract_guard", story["middleware"])
        self.assertIn("pacing_report", review["middleware"])
        self.assertIn("/hooks.md", story["write_permissions"])
        for mw in ("receipt_gate", "skill_activation_guard", "review_gate"):
            self.assertIn(mw, story["middleware"])

    def test_v42_skill_structure_preserved(self) -> None:
        story = next(a for a in _ARCH["agents"] if a["name"] == "storybuilding")
        self.assertEqual(
            set(story["skills"]),
            {"storybuilding-initial", "storybuilding-expand-storyline",
             "storybuilding-expand-character", "storybuilding-specs"},
        )


if __name__ == "__main__":
    unittest.main()
