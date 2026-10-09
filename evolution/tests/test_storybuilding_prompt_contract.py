"""storybuilding 提示词结构契约测试（REQ-20261010-000638 FR-007 / AC-007）。

DEC-013：节拍序列从生成提示词退场，节拍知识转为诊断清单入观测配置
（storyline_observation_config.json），review 提示词引用体检报告；
契约测试锚从「节拍序列逐字保留」（182730 口径）改为「结构存在性」：

  - 提示词不含生成用节拍序列（同质化根源拆除）
  - 弹性区间表述与类型词参考保留（182730 既有保护）
  - 六字段（含设计原则）、槽位形态写法、张力/爽点列、台账章节存在
  - 观测配置含节拍诊断清单、爽点间隔默认值、形态分段阈值
  - review 提示词含节奏体检报告职责与台账读取范围
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
_PROMPT = (_REPO / "prompts" / "storybuilding_system.md").read_text(encoding="utf-8")
_REVIEW = (_REPO / "prompts" / "storybuilding_review.md").read_text(encoding="utf-8")
_CONFIG = json.loads(
    (_REPO / "middleware" / "storyline_observation_config.json").read_text(encoding="utf-8")
)

# 旧生成用节拍序列原文（v13 模板遗产；生成侧必须退场）
_RETIRED_BEATS = (
    "发展 8：冲突→危机→反转→悬念→冲突→危机→反转→揭露；终局 4：危机→冲突→反转→胜利",
    "发展 4：危机→冲突→危机→冲突；终局 2：冲突→胜利",
    "弧光：冲突→危机→冲突→危机→反转",
    "悬念→冲突→危机→反转→揭露",
)


class GenerativeBeatsRetiredTest(unittest.TestCase):
    def test_no_generative_beat_sequences_in_prompt(self) -> None:
        for beat in _RETIRED_BEATS:
            self.assertNotIn(beat, _PROMPT, f"生成用节拍序列必须退场: {beat[:20]}…")

    def test_no_generative_beat_wording(self) -> None:
        self.assertNotIn("建议节拍序列", _PROMPT)
        self.assertNotIn("都会被系统弹回", _PROMPT)


class ElasticityKeptTest(unittest.TestCase):
    """REQ-20261009-182730 既有保护：弹性区间与类型词参考保留。"""

    def test_interval_wording_present(self) -> None:
        for wording in ("8~15（经验值 ~12）", "4~9（经验值 ~6）", "3~8（经验值 ~5）"):
            self.assertIn(wording, _PROMPT)

    def test_type_word_reference_kept(self) -> None:
        self.assertIn("常用参考", _PROMPT)
        self.assertIn("贴切的自定义类型词", _PROMPT)
        for word in ("冲突", "危机", "反转", "揭露", "悬念", "胜利", "交汇"):
            self.assertIn(f"| {word} |", _PROMPT)


class ThreeStageSchemaTest(unittest.TestCase):
    """FR-001/002/003：六字段、槽位形态、张力/爽点列进提示词。"""

    def test_core_six_fields(self) -> None:
        for field in ("Logline", "设计原则", "核心主题", "类型基调", "节奏曲线", "最终结局"):
            self.assertIn(field, _PROMPT)
        self.assertIn("六字段", _PROMPT)
        # 设计原则与最终结局同钉死
        self.assertIn("初构落盘后与最终结局一样钉死", _PROMPT)

    def test_shape_slots_wording(self) -> None:
        # 槽位写法示例与四槽位名
        self.assertIn("首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5", _PROMPT)
        for slot in ("首事件", "前段末", "中点谷", "终局"):
            self.assertIn(slot, _PROMPT)
        self.assertIn("形态先行", _PROMPT)

    def test_tension_payoff_columns(self) -> None:
        self.assertIn("| 张力 |", _PROMPT)
        self.assertIn("| 爽点 |", _PROMPT)
        # 五档定义
        for level in ("日常蓄力", "小波澜", "正面对抗", "临界抉择", "峰值爆点"):
            self.assertIn(level, _PROMPT)
        # 爽点三值与三小一大
        self.assertIn("三小一大", _PROMPT)
        self.assertIn("`大`", _PROMPT)

    def test_promises_ledger_section(self) -> None:
        self.assertIn("九、许诺台账", _PROMPT)
        for token in ("P{n}", "主线大期待", "线级期待", "事件钩子", "已许诺", "推进中", "已兑现", "已放弃"):
            self.assertIn(token, _PROMPT)


class ReviewDiagnosticsTest(unittest.TestCase):
    """FR-005/007：review 提示词引用体检报告 + 台账读取范围。"""

    def test_review_mentions_health_report(self) -> None:
        self.assertIn("节奏体检报告", _REVIEW)
        self.assertIn("语义判断", _REVIEW)

    def test_review_reads_promises(self) -> None:
        self.assertIn("promises.md", _REVIEW)
        self.assertIn("设计原则", _REVIEW)


class ObservationConfigStructureTest(unittest.TestCase):
    """FR-005/007：观测配置承载节拍诊断清单与默认值（参数归 harness，DEC-004）。"""

    def test_payoff_rules_defaults(self) -> None:
        rules = _CONFIG["payoff_rules"]
        self.assertEqual(rules["small_gap_max"], 3)
        self.assertEqual(rules["big_gap_max"], 8)
        self.assertEqual(rules["big_payoff_min_tension"], 4)

    def test_shape_min_events_threshold(self) -> None:
        self.assertEqual(_CONFIG["shape_min_events"], 8)

    def test_beat_checklist_present(self) -> None:
        checklist = _CONFIG["beat_checklist"]
        self.assertGreaterEqual(len(checklist), 4)
        ids = {item["id"] for item in checklist}
        self.assertIn("midpoint_reversal", ids)
        self.assertIn("valley_depth", ids)
        self.assertIn("final_peak", ids)
        self.assertIn("post_victory_hook", ids)
        # 每条清单项可解释（id + kind + desc）
        for item in checklist:
            self.assertIn("id", item)
            self.assertIn("kind", item)
            self.assertIn("desc", item)

    def test_count_ranges_kept(self) -> None:
        self.assertEqual(_CONFIG["count_ranges"]["主线"], [8, 15])


if __name__ == "__main__":
    unittest.main()
