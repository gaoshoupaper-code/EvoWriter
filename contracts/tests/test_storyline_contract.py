"""storyline 契约判定器测试（REQ-20261009-182730 口径 + REQ-20261010-000638 新契约）。

REQ-20261009-182730：数量/类型词不再拦截，观测职责在 harness 侧——结构语法、
唯一性、最终结局不可变行为保持不变。

REQ-20261010-000638 新增契约（FR-001/002/003）：
  - 故事核心六字段（含「设计原则」）初构必齐；设计原则与最终结局同钉死
  - 「节奏曲线」四槽位锚点语法校验（首事件/前段末/中点谷/终局）
  - 新增/变更区块事件表须含「张力」「爽点」列且取值合法
"""

from __future__ import annotations

import unittest

from contracts.storyline_contract import (
    EventRow,
    check_storyline_write,
    iter_line_blocks,
    parse_shape_slots,
)

_TABLE_HEADER = "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |"
_TABLE_SEPARATOR = "|---|---|---|---|---|---|---|---|---|---|"

_SHAPE_OK = "首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5"

_CORE = (
    "- **Logline**：废柴少年持剑崛起\n"
    "- **设计原则**：越强的力量越要付出人性代价\n"
    "- **核心主题**：代价与成长\n"
    "- **类型基调**：东方玄幻·热血\n"
    f"- **节奏曲线**：{_SHAPE_OK}\n"
    "- **最终结局**：林澈持剑镇北荒\n\n"
)


def _block(
    name: str = "青岚剑主",
    type_: str = "主线",
    type_words: tuple[str, ...] = ("冲突", "危机", "反转", "悬念"),
    t_prefix: str = "T",
    event_prefix: str = "事件",
) -> str:
    """构造一个合法主线区块（十列含张力/爽点）；type_words 循环填「类型」列。"""
    rows = [
        f"| {t_prefix}{i + 1} | {event_prefix}{i + 1} | {w} | 发展 | 东荒 | 林澈 |  | {i % 5 + 1} | — | 推进{i + 1} |"
        for i, w in enumerate(type_words)
    ]
    return (
        f"## {name} · {type_} · 奋起\n\n"
        "- **主要地点**：东荒\n"
        "- **全局走向**：由弱到强，终成剑主\n\n"
        f"{_TABLE_HEADER}\n{_TABLE_SEPARATOR}\n" + "\n".join(rows) + "\n"
    )


def _initial(type_words: tuple[str, ...] = ("冲突", "危机", "反转", "悬念")) -> str:
    """合法初构写入：六字段故事核心 + 主线区块。"""
    return _CORE + _block(type_words=type_words)


class SemanticChecksRemovedTest(unittest.TestCase):
    """REQ-20261009-182730 FR-001：数量与类型词不再产生违规。"""

    def test_count_inside_old_template_rejected_value_passes(self) -> None:
        # 9 个非交汇事件：旧口径要求主线恰 12，必弹；新口径放行
        md = _initial(type_words=("冲突", "危机") * 4 + ("反转",))
        self.assertEqual(check_storyline_write("", md), [])

    def test_count_below_lower_and_above_upper_pass(self) -> None:
        few = _initial(type_words=("冲突",) * 3)
        many = _initial(type_words=("冲突",) * 20)
        self.assertEqual(check_storyline_write("", few), [])
        self.assertEqual(check_storyline_write("", many), [])

    def test_custom_type_word_passes(self) -> None:
        md = _initial(type_words=("冲突", "背叛", "反转", "悬念"))
        self.assertEqual(check_storyline_write("", md), [])


class StructureRulesUnchangedTest(unittest.TestCase):
    """结构语法照常拦截。"""

    def test_illegal_block_header_type_rejected(self) -> None:
        md = _CORE + _block(type_="明线")  # 线类型词非法：须为主线/支线/角色线/暗线
        violations = check_storyline_write("", md)
        self.assertTrue(any("类型词非法" in v.message and "明线" in v.message for v in violations))

    def test_missing_line_header_field_rejected(self) -> None:
        md = _CORE + _block().replace("- **主要地点**：东荒\n", "")
        violations = check_storyline_write("", md)
        self.assertTrue(any("主要地点" in v.message for v in violations))

    def test_illegal_t_number_rejected(self) -> None:
        md = _CORE + _block(t_prefix="X")
        violations = check_storyline_write("", md)
        self.assertTrue(any("时序号非法" in v.message for v in violations))

    def test_missing_event_table_rejected(self) -> None:
        md = _CORE + _block().replace(_TABLE_HEADER, "| 时序 | 备注 |").replace(
            _TABLE_SEPARATOR, "|---|---|"
        )
        violations = check_storyline_write("", md)
        self.assertTrue(any("缺事件表" in v.message for v in violations))


class GlobalRulesUnchangedTest(unittest.TestCase):
    """唯一性不变；最终结局为草稿制（v42 进化口径，卡②A）——可改不拦。"""

    def test_duplicate_event_name_rejected(self) -> None:
        md = _initial().replace("事件2", "事件1")
        violations = check_storyline_write("", md)
        self.assertTrue(any("事件名重复" in v.message for v in violations))

    def test_final_ending_is_draft_and_editable(self) -> None:
        base = _initial()
        changed = base.replace("林澈持剑镇北荒", "林澈归隐")
        self.assertEqual(check_storyline_write(base, changed), [])
        deleted = base.replace("- **最终结局**：林澈持剑镇北荒\n\n", "\n")
        self.assertEqual(check_storyline_write(base, deleted), [])


class CoreSixFieldsTest(unittest.TestCase):
    """FR-001 合并版：初构必含六字段；设计原则钉死；结局草稿制。"""

    def test_initial_missing_design_principle_rejected(self) -> None:
        md = _initial().replace("- **设计原则**：越强的力量越要付出人性代价\n", "")
        violations = check_storyline_write("", md)
        self.assertTrue(any("设计原则" in v.message and "缺" in v.message for v in violations))

    def test_initial_missing_each_field_rejected(self) -> None:
        for field in ("Logline", "核心主题", "类型基调", "节奏曲线"):
            with self.subTest(field=field):
                md = _initial().replace(f"- **{field}**", f"- **{field}X**")
                violations = check_storyline_write("", md)
                self.assertTrue(any(field in v.message for v in violations))

    def test_initial_with_six_fields_passes(self) -> None:
        self.assertEqual(check_storyline_write("", _initial()), [])

    def test_design_principle_immutable(self) -> None:
        base = _initial()
        changed = base.replace("越强的力量越要付出人性代价", "弱者逆袭无需代价")
        violations = check_storyline_write(base, changed)
        self.assertTrue(any(v.rule == "design_principle" for v in violations))
        deleted = base.replace("- **设计原则**：越强的力量越要付出人性代价\n", "")
        violations = check_storyline_write(base, deleted)
        self.assertTrue(any(v.rule == "design_principle" for v in violations))

    def test_empty_field_value_is_missing(self) -> None:
        """空值字段=缺失：初构拦截（防空值吞下一行内容后被钉死成赃值）。"""
        md = _initial().replace("- **设计原则**：越强的力量越要付出人性代价\n", "- **设计原则**：\n")
        violations = check_storyline_write("", md)
        self.assertTrue(any("设计原则" in v.message and "空" in v.message for v in violations))

    def test_empty_principle_on_disk_allows_backfill_and_neighbor_edits(self) -> None:
        """磁盘空值设计原则：extract 返回 None——回填真值与编辑相邻行均不误拦。"""
        base = _initial().replace("- **设计原则**：越强的力量越要付出人性代价\n", "- **设计原则**：\n")
        # 编辑相邻「核心主题」行 → 不触发 design_principle 规则
        neighbor = base.replace("- **核心主题**：代价与成长", "- **核心主题**：选择与代价")
        self.assertFalse(any(v.rule == "design_principle" for v in check_storyline_write(base, neighbor)))
        # 回填设计原则真值 → 放行
        backfill = base.replace("- **设计原则**：\n", "- **设计原则**：力量必有代价\n")
        self.assertFalse(any(v.rule == "design_principle" for v in check_storyline_write(base, backfill)))

    def test_design_principle_absent_in_legacy_current_not_enforced(self) -> None:
        # 存量大纲无设计原则：追加新线区块不因头部缺字段被拦（范围化）
        legacy = (
            "# 故事\n\n- **Logline**：x\n- **最终结局**：y\n\n"
            + _block(type_words=("冲突", "危机", "反转", "悬念"))
        )
        appended = legacy + _block(
            name="暗流", type_="暗线", type_words=("悬念", "冲突", "危机"), event_prefix="暗流事件"
        )
        violations = check_storyline_write(legacy, appended)
        self.assertEqual(violations, [])


class ShapeSlotsTest(unittest.TestCase):
    """FR-002：节奏曲线四槽位锚点语法校验。"""

    def test_illegal_value_rejected(self) -> None:
        md = _initial().replace(_SHAPE_OK, "首事件≈7 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5")
        violations = check_storyline_write("", md)
        self.assertTrue(any("节奏曲线" in v.message and "槽位" in v.message for v in violations))
        self.assertIn("首事件≈2", "；".join(v.message for v in violations))  # 错误提示带合法写法

    def test_non_numeric_rejected(self) -> None:
        md = _initial().replace(_SHAPE_OK, "首事件=高 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5")
        violations = check_storyline_write("", md)
        self.assertTrue(any("节奏曲线" in v.message for v in violations))

    def test_missing_slot_rejected(self) -> None:
        md = _initial().replace(_SHAPE_OK, "首事件≈2 · 前段末≥4 · 中点谷≤2")
        violations = check_storyline_write("", md)
        self.assertTrue(any("终局" in v.message and "节奏曲线" in v.message for v in violations))

    def test_rewrite_to_invalid_rejected(self) -> None:
        base = _initial()
        changed = base.replace(_SHAPE_OK, "起步低走慢慢升")
        violations = check_storyline_write(base, changed)
        self.assertTrue(any("节奏曲线" in v.message for v in violations))

    def test_parse_shape_slots_valid(self) -> None:
        slots = parse_shape_slots(_SHAPE_OK)
        self.assertIsNotNone(slots)
        by_name = {s.slot: s for s in slots or ()}
        self.assertEqual(by_name["首事件"].op, "≈")
        self.assertEqual(by_name["首事件"].values, (2,))
        self.assertEqual(by_name["前段末"].op, ">=")
        self.assertEqual(by_name["终局"].values, (5, 5))

    def test_parse_shape_slots_invalid_returns_none(self) -> None:
        self.assertIsNone(parse_shape_slots("起步低走慢慢升"))
        self.assertIsNone(parse_shape_slots("首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰9,5"))
        # 双峰标记但只给一值
        self.assertIsNone(parse_shape_slots("首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5"))
        # 非终局槽给双值
        self.assertIsNone(parse_shape_slots("首事件2,3 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5"))
        # 槽位重复
        self.assertIsNone(parse_shape_slots("首事件≈2 · 首事件≥4 · 中点谷≤2 · 终局双峰5,5"))

    def test_rewrite_adding_invalid_rhythm_to_legacy_blocked(self) -> None:
        """存量大纲（无节奏曲线字段）新增非法槽位串 → 拦截（与 docstring 口径一致）。"""
        legacy = (
            "# 故事\n\n- **Logline**：x\n- **最终结局**：y\n\n"
            + _block(type_words=("冲突", "危机", "反转", "悬念"))
        )
        projected = legacy.replace(
            "- **Logline**：x\n", "- **Logline**：x\n- **节奏曲线**：起步低走慢慢升\n"
        )
        violations = check_storyline_write(legacy, projected)
        self.assertTrue(any("节奏曲线" in v.message and "槽位" in v.message for v in violations))


class TensionPayoffColumnsTest(unittest.TestCase):
    """FR-003：新增/变更区块须含张力/爽点列且取值合法。"""

    def test_new_block_missing_columns_rejected(self) -> None:
        md = _CORE + _block().replace(
            _TABLE_HEADER, "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |"
        ).replace(_TABLE_SEPARATOR, "|---|---|---|---|---|---|---|---|")
        violations = check_storyline_write("", md)
        self.assertTrue(any("张力" in v.message and "爽点" in v.message for v in violations))

    def test_tension_out_of_range_rejected(self) -> None:
        block = _block(type_words=("冲突", "危机")).replace(
            "| T1 | 事件1 | 冲突 | 发展 | 东荒 | 林澈 |  | 1 | — | 推进1 |",
            "| T1 | 事件1 | 冲突 | 发展 | 东荒 | 林澈 |  | 6 | — | 推进1 |",
        )
        violations = check_storyline_write("", _CORE + block)
        self.assertTrue(any("张力" in v.message and "6" in v.message for v in violations))

    def test_payoff_illegal_value_rejected(self) -> None:
        block = _block(type_words=("冲突", "危机"))
        block = block.replace("| 1 | — | 推进1 |", "| 1 | 中 | 推进1 |")
        violations = check_storyline_write("", _CORE + block)
        self.assertTrue(any("爽点" in v.message and "中" in v.message for v in violations))

    def test_valid_rows_pass(self) -> None:
        block = _block(type_words=("冲突", "危机"))
        block = block.replace("| 1 | — | 推进1 |", "| 5 | 大 | 推进1 |")
        self.assertEqual(check_storyline_write("", _CORE + block), [])

    def test_empty_payoff_passes(self) -> None:
        # 爽点格留空 = 普通事件（提示词 §5.2 明示写法之一，须放行）
        block = _block(type_words=("冲突", "危机")).replace("| 1 | — | 推进1 |", "| 1 |  | 推进1 |")
        self.assertEqual(check_storyline_write("", _CORE + block), [])

    def test_legacy_block_untouched_not_checked(self) -> None:
        # 存量八列区块未变更：追加其他区块时不因缺列被拦（DEC-015 范围化）
        legacy = (
            "# 故事\n\n- **Logline**：x\n- **最终结局**：y\n\n"
            "## 旧线 · 主线 · 活跃\n\n- **主要地点**：东荒\n- **全局走向**：略\n\n"
            "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| T1 | 旧事件 | 冲突 | 发展 | 东荒 | 林澈 |  | 略 |\n"
        )
        appended = legacy + _block(
            name="新线", type_="支线", type_words=("危机", "冲突"), event_prefix="新线事件"
        )
        violations = check_storyline_write(legacy, appended)
        self.assertEqual(violations, [])


class IterLineBlocksTest(unittest.TestCase):
    """公共结构解析 iter_line_blocks（观测侧复用）。"""

    def test_crossing_column_flags_and_type_words(self) -> None:
        md = (
            "## 交线 · 主线 · 活跃\n\n- **主要地点**：东荒\n- **全局走向**：略\n\n"
            + _TABLE_HEADER + "\n" + _TABLE_SEPARATOR + "\n"
            "| T1 | 事件1 | 冲突 | 发展 | 东荒 | 林澈 |  | 3 | — | 略 |\n"
            "| T2 | 事件2 | 交汇 | 发展 | 东荒 | 林澈 | 支线甲 | 4 | 小 | 略 |\n"
        )
        blocks = iter_line_blocks(md)
        self.assertEqual(len(blocks), 1)
        blk = blocks[0]
        self.assertEqual((blk.name, blk.type), ("交线", "主线"))
        self.assertEqual(blk.events, (
            EventRow("事件1", "冲突", False, 1.0, "3", "—"),
            EventRow("事件2", "交汇", True, 2.0, "4", "小"),
        ))

    def test_missing_type_column_yields_empty_word(self) -> None:
        md = (
            "## 无类型列 · 支线 · 活跃\n\n- **主要地点**：东荒\n- **全局走向**：略\n\n"
            "| 时序 | 事件 | 阶段 |\n|---|---|---|\n| T1 | 事件1 | 发展 |\n"
        )
        blocks = iter_line_blocks(md)
        self.assertEqual(blocks[0].events, (EventRow("事件1", "", False, 1.0),))

    def test_block_without_table_yields_empty_events(self) -> None:
        md = "## 缺表 · 暗线 · 暂伏\n\n- **主要地点**：东荒\n- **全局走向**：略\n"
        blocks = iter_line_blocks(md)
        self.assertEqual(blocks[0].events, ())


if __name__ == "__main__":
    unittest.main()
