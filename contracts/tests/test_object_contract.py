"""object_contract 判定器测试（REQ-20261004-221109 FR-001/FR-004）。

覆盖：
  - 卡片三段式结构（基本信息字段 / 类型与可见性枚举 / 轨迹表）
  - 名称与文件名一致（文件名即唯一 ID，DEC-012）
  - 重名查重（新建卡与现有卡名称冲突 → 拒）
  - 轨迹锚点存在性（引用 storyline.md 不存在的事件 → 拒，AC-002）
  - 变化枚举白名单（AC-004）
  - storyline 反查（删除被卡片引用的事件 → 悬挂引用，AC-003 判定器半）
"""

from __future__ import annotations

import unittest

from contracts.object_contract import (
    CHANGE_TYPES,
    check_object_write,
    find_dangling_refs,
    parse_object_card,
)
from contracts.storyline_contract import extract_event_names

_STORYLINE = """# 故事核心

- Logline：测试
- 核心主题：测试
- 类型基调：玄幻
- 节奏曲线：快
- 最终结局：测试

## 主线 · 主线 · 活跃

- 主要地点：青云宗
- 全局走向：从被压制到反击

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T1 | 少年拾剑 | 冲突 | 发展 | 秘境 | 林岸 | | 拾得古剑 |
| T2 | 魔主夺剑 | 危机 | 发展 | 青云宗 | 林岸、玄夜 | | 剑被夺 |
| T3 | 断剑之誓 | 胜利 | 终局 | 青云宗 | 林岸 | | 断剑退敌 |
"""

_VALID_CARD = """# 青云剑

## 基本信息

- 名称：青云剑
- 类型：武器
- 叙事可见性：明线

## 详情

上古遗剑，剑灵沉睡。

## 轨迹

| 事件 | 变化 | 归属 | 备注 |
|------|------|------|------|
| 少年拾剑 | 登场 | 林岸 | 秘境拾得，剑灵沉睡 |
| 魔主夺剑 | 易主 | 玄夜 | 强夺 |
| 断剑之誓 | 损坏 | 无主 | 自爆断剑 |
"""


def _rules(violations) -> set[str]:
    return {v.rule for v in violations}


class ParseObjectCardTests(unittest.TestCase):
    def test_parse_valid_card(self):
        card = parse_object_card(_VALID_CARD)
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "青云剑")
        self.assertEqual(card.type, "武器")
        self.assertEqual(card.visibility, "明线")
        self.assertEqual(
            [(r.event, r.change, r.owner) for r in card.trajectory],
            [("少年拾剑", "登场", "林岸"), ("魔主夺剑", "易主", "玄夜"), ("断剑之誓", "损坏", "无主")],
        )

    def test_parse_card_without_trajectory(self):
        md = _VALID_CARD.split("## 轨迹")[0]
        card = parse_object_card(md)
        self.assertIsNotNone(card)
        self.assertEqual(card.trajectory, ())


class CheckObjectWriteTests(unittest.TestCase):
    def test_valid_card_passes(self):
        violations = check_object_write(
            "青云剑.md", _VALID_CARD, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertEqual(violations, [])

    def test_missing_visibility_field_rejected(self):
        md = _VALID_CARD.replace("- 叙事可见性：明线\n", "")
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertIn("contract", _rules(violations))
        self.assertTrue(any("叙事可见性" in v.message for v in violations))

    def test_invalid_type_rejected(self):
        md = _VALID_CARD.replace("- 类型：武器", "- 类型：法宝")
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("类型" in v.message and "法宝" in v.message for v in violations))

    def test_invalid_visibility_rejected(self):
        md = _VALID_CARD.replace("- 叙事可见性：明线", "- 叙事可见性：隐藏")
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("叙事可见性" in v.message for v in violations))

    def test_name_filename_mismatch_rejected(self):
        violations = check_object_write(
            "青云剑（残）.md", _VALID_CARD, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("名称" in v.message and "文件名" in v.message for v in violations))

    def test_duplicate_name_rejected(self):
        """名称字段与现有卡撞名 → 拒；修法 = 加消歧后缀（名称与文件名同步改）。"""
        md = _VALID_CARD  # 名称字段「青云剑」，写入另一个文件名
        violations = check_object_write(
            "青云剑（宗门珍藏）.md", md,
            existing_cards={"青云剑.md": _VALID_CARD},
            storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("重名" in v.message or "消歧" in v.message for v in violations))

    def test_updating_same_card_not_flagged_duplicate(self):
        violations = check_object_write(
            "青云剑.md",
            _VALID_CARD.replace("上古遗剑，剑灵沉睡。", "上古遗剑，剑灵已醒。"),
            existing_cards={"青云剑.md": _VALID_CARD},
            storyline_markdown=_STORYLINE,
        )
        self.assertEqual(violations, [])

    def test_dangling_event_ref_rejected(self):
        md = _VALID_CARD.replace("| 少年拾剑 | 登场 |", "| 少年拾剑x | 登场 |")
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("少年拾剑x" in v.message for v in violations))

    def test_invalid_change_rejected(self):
        md = _VALID_CARD.replace("| 魔主夺剑 | 易主 |", "| 魔主夺剑 | 顺走 |")
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("顺走" in v.message and "枚举" in v.message for v in violations))

    def test_missing_trajectory_section_rejected(self):
        md = _VALID_CARD.split("## 轨迹")[0]
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("轨迹" in v.message for v in violations))

    def test_empty_trajectory_table_rejected(self):
        md = _VALID_CARD.split("| 事件 |")[0] + "| 事件 | 变化 | 归属 | 备注 |\n|------|------|------|------|\n"
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("轨迹" in v.message for v in violations))

    def test_event_name_variant_not_matched(self):
        """事件名差一个字 = 不存在（变体同样按字面比对）。"""
        md = _VALID_CARD.replace("| 少年拾剑 | 登场 | 林岸 |", "| 少年拾剑。| 登场 | 林岸 |")
        violations = check_object_write(
            "青云剑.md", md, existing_cards={}, storyline_markdown=_STORYLINE,
        )
        self.assertTrue(any("少年拾剑。" in v.message for v in violations))

    def test_change_enum_complete(self):
        self.assertEqual(
            set(CHANGE_TYPES),
            {"提及", "登场", "获得", "易主", "损坏", "修复", "遗失", "销毁", "消耗", "升级"},
        )


class FindDanglingRefsTests(unittest.TestCase):
    def test_removed_event_referenced_by_card(self):
        projected = _STORYLINE.replace(
            "| T2 | 魔主夺剑 | 危机 | 发展 | 青云宗 | 林岸、玄夜 | | 剑被夺 |\n", "",
        )
        violations = find_dangling_refs(
            storyline_current=_STORYLINE,
            storyline_projected=projected,
            all_cards={"青云剑.md": _VALID_CARD},
        )
        self.assertTrue(any("魔主夺剑" in v.message and "青云剑" in v.message for v in violations))

    def test_rename_counts_as_removed(self):
        projected = _STORYLINE.replace("魔主夺剑", "魔主劫剑")
        violations = find_dangling_refs(
            storyline_current=_STORYLINE,
            storyline_projected=projected,
            all_cards={"青云剑.md": _VALID_CARD},
        )
        self.assertTrue(any("魔主夺剑" in v.message for v in violations))

    def test_clean_rewrite_no_violations(self):
        projected = _STORYLINE.replace("剑被夺", "古剑易主")
        violations = find_dangling_refs(
            storyline_current=_STORYLINE,
            storyline_projected=projected,
            all_cards={"青云剑.md": _VALID_CARD},
        )
        self.assertEqual(violations, [])


class ExtractEventNamesTests(unittest.TestCase):
    def test_extracts_all_event_names(self):
        self.assertEqual(
            set(extract_event_names(_STORYLINE)),
            {"少年拾剑", "魔主夺剑", "断剑之誓"},
        )


if __name__ == "__main__":
    unittest.main()
