"""hooks.md 钩子登记契约测试（REQ-20261010-000638 FR-004 合并版 / AC-004）。

固化登记契约判定行为：九列结构、H{n} 编号、层级/类型/状态枚举、
已放弃必填备注、已收必有兑现事件、事件名锚点存在性、
storyline 修订反查（删除/改名被引用事件即拦）。
"""

from __future__ import annotations

import unittest

from contracts.hooks_contract import (
    check_hooks_write,
    find_dangling_hook_refs,
    parse_hooks,
)

_STORYLINE = (
    "# 故事\n\n"
    "- Logline：略\n- 设计原则：略\n- 核心主题：略\n- 类型基调：略\n"
    "- 节奏曲线：首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5\n"
    "- 最终结局：略\n\n"
    "## 主线 · 主线 · 活跃\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
    "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
    "| T1 | 灭门之夜 | 冲突 | 发展 | 东荒 | 林寒 |  | 5 | — | 家破人亡 |\n"
    "| T4 | 祭祖大典的闯入 | 反转 | 发展 | 东荒 | 林寒 |  | 4 | 小 | 撕开伪面 |\n"
    "| T9 | 秘境问剑 | 揭露 | 发展 | 秘境 | 林寒 |  | 3 | — | 剑灵苏醒 |\n"
    "| T14 | 新教父立威 | 胜利 | 终局 | 东荒 | 林寒 |  | 5 | 大 | 持剑镇北荒 |\n"
)

_TABLE_HEADER = "| 编号 | 钩子 | 层级 | 类型 | 埋设事件 | 推进事件 | 兑现事件 | 状态 | 备注 |"
_TABLE_SEPARATOR = "|---|---|---|---|---|---|---|---|---|"

_LEDGER_OK = (
    "# 钩子登记\n\n"
    + _TABLE_HEADER + "\n" + _TABLE_SEPARATOR + "\n"
    "| H1 | 查清灭门真相 | 主线大期待 | 悬念 | 灭门之夜 | 祭祖大典的闯入 | 新教父立威 | 已收 | 全书主悬念 |\n"
    "| H7 | 剑灵的真实来历 | 事件钩子 | 期待 | 秘境问剑 |  |  | 未收 |  |\n"
)


class ParseHooksTest(unittest.TestCase):
    def test_parse_rows(self) -> None:
        rows = parse_hooks(_LEDGER_OK)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].id, "H1")
        self.assertEqual(rows[0].level, "主线大期待")
        self.assertEqual(rows[0].type, "悬念")
        self.assertEqual(rows[0].status, "已收")
        self.assertEqual(rows[0].plant_events, ("灭门之夜",))
        self.assertEqual(rows[0].progress_events, ("祭祖大典的闯入",))
        self.assertEqual(rows[0].payoff_events, ("新教父立威",))
        self.assertEqual(rows[1].note, "")

    def test_parse_empty_returns_empty(self) -> None:
        self.assertEqual(parse_hooks(""), [])

    def test_parse_short_row_pads_missing_columns(self) -> None:
        md = _LEDGER_OK.replace(
            "| H7 | 剑灵的真实来历 | 事件钩子 | 期待 | 秘境问剑 |  |  | 未收 |  |",
            "| H7 | 剑灵的真实来历 | 事件钩子 | 期待 | 秘境问剑 |",
        )
        rows = parse_hooks(md)
        self.assertEqual(rows[1].id, "H7")
        self.assertEqual(rows[1].progress_events, ())
        self.assertEqual(rows[1].status, "")


class CheckHooksWriteTest(unittest.TestCase):
    def test_valid_ledger_passes(self) -> None:
        self.assertEqual(check_hooks_write(_LEDGER_OK, _STORYLINE), [])

    def test_missing_anchor_rejected(self) -> None:
        md = _LEDGER_OK.replace("秘境问剑", "灭门夜")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("灭门夜" in v.message and "不存在" in v.message for v in violations))

    def test_illegal_level_rejected(self) -> None:
        md = _LEDGER_OK.replace("事件钩子", "超级期待")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("层级" in v.message and "超级期待" in v.message for v in violations))

    def test_illegal_type_rejected(self) -> None:
        md = _LEDGER_OK.replace("悬念 | 灭门之夜", "疑惑 | 灭门之夜")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("类型" in v.message and "疑惑" in v.message for v in violations))

    def test_illegal_status_rejected(self) -> None:
        md = _LEDGER_OK.replace("| 未收 |  |", "| 进行中 |  |")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("状态" in v.message and "进行中" in v.message for v in violations))

    def test_abandoned_without_note_rejected(self) -> None:
        md = _LEDGER_OK.replace(
            "| H7 | 剑灵的真实来历 | 事件钩子 | 期待 | 秘境问剑 |  |  | 未收 |  |",
            "| H7 | 剑灵的真实来历 | 事件钩子 | 期待 | 秘境问剑 |  |  | 已放弃 |  |",
        )
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("已放弃" in v.message and "备注" in v.message for v in violations))

    def test_duplicate_id_rejected(self) -> None:
        md = _LEDGER_OK.replace("| H7 |", "| H1 |")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("编号" in v.message and "H1" in v.message for v in violations))

    def test_collected_without_payoff_event_rejected(self) -> None:
        md = _LEDGER_OK.replace("祭祖大典的闯入 | 新教父立威 | 已收", "祭祖大典的闯入 |  | 已收")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("已收" in v.message and "兑现事件" in v.message for v in violations))

    def test_missing_plant_event_rejected(self) -> None:
        md = _LEDGER_OK.replace("期待 | 秘境问剑 |", "期待 |  |")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("埋设事件" in v.message for v in violations))

    def test_empty_ledger_rejected(self) -> None:
        violations = check_hooks_write("# 钩子登记\n", _STORYLINE)
        self.assertTrue(any("数据行" in v.message for v in violations))

    def test_short_row_rejected(self) -> None:
        # 八列短行（漏「备注」格）→ 列数不齐
        md = _LEDGER_OK.replace(
            "| H7 | 剑灵的真实来历 | 事件钩子 | 期待 | 秘境问剑 |  |  | 未收 |  |",
            "| H7 | 剑灵的真实来历 | 事件钩子 | 期待 | 秘境问剑 |  |  | 未收 |",
        )
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("列数不齐" in v.message for v in violations))

    def test_malformed_id_rejected(self) -> None:
        md = _LEDGER_OK.replace("| H7 |", "| X7 |")
        violations = check_hooks_write(md, _STORYLINE)
        self.assertTrue(any("编号" in v.message and "X7" in v.message for v in violations))


class DanglingRefsTest(unittest.TestCase):
    def test_removed_referenced_event_rejected(self) -> None:
        projected = _STORYLINE.replace(
            "| T14 | 新教父立威 | 胜利 | 终局 | 东荒 | 林寒 |  | 5 | 大 | 持剑镇北荒 |\n", ""
        )
        violations = find_dangling_hook_refs(_STORYLINE, projected, _LEDGER_OK)
        self.assertTrue(any("新教父立威" in v.message and "登记" in v.message for v in violations))

    def test_kept_events_pass(self) -> None:
        self.assertEqual(find_dangling_hook_refs(_STORYLINE, _STORYLINE, _LEDGER_OK), [])


if __name__ == "__main__":
    unittest.main()
