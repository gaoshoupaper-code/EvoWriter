"""storyline 契约判定器测试（REQ-20261009-182730 FR-001：语义校验移除后口径）。

DEC-007 等值计数与事件类型词白名单已随本需求移除：数量/类型词不再拦截，
观测职责移交 harness 侧（DEC-004/DEC-006）。本文件固化 contracts 层新口径：
结构语法（区块头类型词、线头字段、事件表、T 号、列数一致）、唯一性、
最终结局不可变的判定行为与移除前完全一致。
"""

from __future__ import annotations

import unittest

from contracts.storyline_contract import check_storyline_write, iter_line_blocks

_TABLE_HEADER = "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |"
_TABLE_SEPARATOR = "|---|---|---|---|---|---|---|---|"


def _block(
    name: str = "青岚剑主",
    type_: str = "主线",
    type_words: tuple[str, ...] = ("冲突", "危机", "反转", "悬念"),
    t_prefix: str = "T",
) -> str:
    """构造一个合法主线区块；type_words 循环填「类型」列，行数=len(type_words)。"""
    rows = [
        f"| {t_prefix}{i + 1} | 事件{i + 1} | {w} | 发展 | 东荒 | 林澈 |  | 推进{i + 1} |"
        for i, w in enumerate(type_words)
    ]
    return (
        f"## {name} · {type_} · 奋起\n\n"
        "- **主要地点**：东荒\n"
        "- **全局走向**：由弱到强，终成剑主\n\n"
        f"{_TABLE_HEADER}\n{_TABLE_SEPARATOR}\n" + "\n".join(rows) + "\n"
    )


class SemanticChecksRemovedTest(unittest.TestCase):
    """FR-001：数量与类型词不再产生违规（AC-001 边界样例同源）。"""

    def test_count_inside_old_template_rejected_value_passes(self) -> None:
        # 9 个非交汇事件：旧口径要求主线恰 12，必弹；新口径放行
        md = _block(type_words=("冲突", "危机") * 4 + ("反转",))
        self.assertEqual(check_storyline_write("", md), [])

    def test_count_below_lower_and_above_upper_pass(self) -> None:
        # 3 个（低于参考下限）与 20 个（高于参考上限）同样放行——参考区间只观测不拦
        few = _block(type_words=("冲突",) * 3)
        many = _block(type_words=("冲突",) * 20)
        self.assertEqual(check_storyline_write("", few), [])
        self.assertEqual(check_storyline_write("", many), [])

    def test_custom_type_word_passes(self) -> None:
        # 自定义类型词「背叛」：旧白名单必弹；新口径放行
        md = _block(type_words=("冲突", "背叛", "反转", "悬念"))
        self.assertEqual(check_storyline_write("", md), [])


class StructureRulesUnchangedTest(unittest.TestCase):
    """FR-001：结构语法照常拦截（AC-002 同源场景）。"""

    def test_illegal_block_header_type_rejected(self) -> None:
        md = _block(type_="明线")  # 线类型词非法：须为主线/支线/角色线/暗线
        violations = check_storyline_write("", md)
        self.assertTrue(any("类型词非法" in v.message and "明线" in v.message for v in violations))

    def test_missing_line_header_field_rejected(self) -> None:
        md = _block().replace("- **主要地点**：东荒\n", "")
        violations = check_storyline_write("", md)
        self.assertTrue(any("主要地点" in v.message for v in violations))

    def test_illegal_t_number_rejected(self) -> None:
        md = _block(t_prefix="X")  # X1 形式非法
        violations = check_storyline_write("", md)
        self.assertTrue(any("时序号非法" in v.message for v in violations))

    def test_missing_event_table_rejected(self) -> None:
        md = _block().replace(_TABLE_HEADER, "| 时序 | 备注 |").replace(
            _TABLE_SEPARATOR, "|---|---|"
        )
        # 表头不含「事件」列 → 视为缺事件表
        violations = check_storyline_write("", md)
        self.assertTrue(any("缺事件表" in v.message for v in violations))


class GlobalRulesUnchangedTest(unittest.TestCase):
    """FR-001：唯一性与结局不可变行为不变。"""

    def test_duplicate_event_name_rejected(self) -> None:
        md = _block(type_words=("冲突", "冲突", "危机", "悬念"))
        # 行内容差异足够但事件名重复 → 弹
        md = md.replace("事件2", "事件1")
        violations = check_storyline_write("", md)
        self.assertTrue(any("事件名重复" in v.message for v in violations))

    def test_final_ending_immutable(self) -> None:
        base = "- **最终结局**：林澈持剑镇北荒\n\n" + _block()
        changed = base.replace("林澈持剑镇北荒", "林澈归隐")
        violations = check_storyline_write(base, changed)
        self.assertTrue(any(v.rule == "ending" for v in violations))
        deleted = base.replace("- **最终结局**：林澈持剑镇北荒\n\n", "")
        violations = check_storyline_write(base, deleted)
        self.assertTrue(any(v.rule == "ending" for v in violations))


class IterLineBlocksTest(unittest.TestCase):
    """公共结构解析 iter_line_blocks（REQ-20261009-182730：harness 观测侧复用）。"""

    def test_crossing_column_flags_and_type_words(self) -> None:
        md = (
            "## 交线 · 主线 · 活跃\n\n- **主要地点**：东荒\n- **全局走向**：略\n\n"
            + _TABLE_HEADER + "\n" + _TABLE_SEPARATOR + "\n"
            "| T1 | 事件1 | 冲突 | 发展 | 东荒 | 林澈 |  | 略 |\n"
            "| T2 | 事件2 | 交汇 | 发展 | 东荒 | 林澈 | 支线甲 | 略 |\n"
        )
        blocks = iter_line_blocks(md)
        self.assertEqual(len(blocks), 1)
        blk = blocks[0]
        self.assertEqual((blk.name, blk.type), ("交线", "主线"))
        self.assertEqual(blk.events, (("冲突", False), ("交汇", True)))

    def test_missing_type_column_yields_empty_word(self) -> None:
        md = (
            "## 无类型列 · 支线 · 活跃\n\n- **主要地点**：东荒\n- **全局走向**：略\n\n"
            "| 时序 | 事件 | 阶段 |\n|---|---|---|\n| T1 | 事件1 | 发展 |\n"
        )
        blocks = iter_line_blocks(md)
        self.assertEqual(blocks[0].events, (("", False),))

    def test_block_without_table_yields_empty_events(self) -> None:
        md = "## 缺表 · 暗线 · 暂伏\n\n- **主要地点**：东荒\n- **全局走向**：略\n"
        blocks = iter_line_blocks(md)
        self.assertEqual(blocks[0].events, ())


if __name__ == "__main__":
    unittest.main()
