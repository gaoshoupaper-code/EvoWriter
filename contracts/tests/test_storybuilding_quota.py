"""故事构建配比契约测试（REQ-20260922-162823 FR-004 / DEC-011/013）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts.storybuilding_quota import (
    QuotaTarget,
    count_character_files,
    count_storyline_files,
    count_workspace,
    evaluate_quota,
    parse_demand_quota,
    parse_storyline_index,
)

# 与 golden case-001 同构的核心层字段片段（full 档）
_DEMAND_FULL = """# 创作需求文档

## 核心层

- **体裁类型**：玄幻 · 热血升级
- **篇幅档位**（≤20 / 21-50 / 51-90 / 91-130 / 131-170 / 171-200 章）：21-50章
- **目标配比**（主线 / 支线 / 角色线 / 暗线）：主线5 / 支线1 / 角色线1 / 暗线0
"""

# minimal 档（case-003 同构）：只有篇幅档位，配比留白
_DEMAND_MINIMAL = """# 创作需求文档

- **题材**：玄幻 · 轻松治愈（日常种田 + 美食 + 治愈系）
- **篇幅档位**（≤20 / 21-50 / 51-90 / 91-130 / 131-170 / 171-200 章）：21-50章
"""

# 承诺点兜底片段（核心层字段缺失）
_DEMAND_COMMITMENT = """## 承诺点（不可漂移项）

8. 结构约束：篇幅 21-50 章；配比主线4 / 支线2 / 角色线1 / 暗线1。
"""

_INDEX_MD = """# 故事核心
（略）

## 故事线一览表

| ID | 名称 | 类型 | 状态 |
|----|------|------|------|
| S01 | 代理觉醒 | 主线 | 活跃 |
| S02 | 人类火种 | 支线 | 活跃 |
| S03 | 师徒羁绊 | 角色线 | 活跃 |
"""


class ParseDemandQuotaTest(unittest.TestCase):
    def test_parse_core_field(self) -> None:
        """核心层「目标配比」字段优先解析（FR-004）。"""
        target = parse_demand_quota(_DEMAND_FULL)
        self.assertIsNotNone(target)
        self.assertEqual(
            (target.main, target.sub, target.character_line, target.dark),
            (5, 1, 1, 0),
        )
        self.assertEqual(target.source, "core")
        self.assertEqual(target.total(), 7)

    def test_minimal_returns_none(self) -> None:
        """minimal 档配比留白返回 None，走 DEC-013 软终止，不猜测。"""
        self.assertIsNone(parse_demand_quota(_DEMAND_MINIMAL))

    def test_commitment_fallback(self) -> None:
        """核心层缺失时承诺点「结构约束」兜底（FR-004）。"""
        target = parse_demand_quota(_DEMAND_COMMITMENT)
        self.assertIsNotNone(target)
        self.assertEqual(
            (target.main, target.sub, target.character_line, target.dark),
            (4, 2, 1, 1),
        )
        self.assertEqual(target.source, "commitment")

    def test_empty_demand_returns_none(self) -> None:
        """空文档 / 无配比信息返回 None（失败语义：软终止，不抛异常）。"""
        self.assertIsNone(parse_demand_quota(""))
        self.assertIsNone(parse_demand_quota("# 创作需求文档\n\n正文无配比"))


class ParseStorylineIndexTest(unittest.TestCase):
    def test_count_by_type(self) -> None:
        """一览表按类型列计数（主线 1 / 支线 1 / 角色线 1）。"""
        counts = parse_storyline_index(_INDEX_MD)
        self.assertEqual(
            counts, {"主线": 1, "支线": 1, "角色线": 1, "暗线": 0},
        )

    def test_missing_index_returns_none(self) -> None:
        """一览表缺失或无数据行返回 None（调用方退回文件计数口径）。"""
        self.assertIsNone(parse_storyline_index("# 只有核心\n\n无表"))
        self.assertIsNone(parse_storyline_index(""))

    def test_wide_index_variant_counted(self) -> None:
        """宽表变体（S{XX} 粘名字 + 多列 + 类型词粗体包裹）也按类型计数（pilot v14 实测形态）。"""
        wide = (
            "## 故事线一览表\n\n"
            "| S01-铁匠铺起点 | 铁匠铺起点 | 主线 | 摘要… | 角色 | 事件 | 时间 | 活跃 |\n"
            "| S06-秩序囚徒 | 秩序囚徒 | **角色线** | 摘要… | 角色 | 事件 | 时间 | 活跃 |\n"
            "| S07-散修之网 | 散修之网 | **支线** | 摘要… | 角色 | 事件 | 时间 | 活跃 |\n"
        )
        counts = parse_storyline_index(wide)
        self.assertEqual(counts, {"主线": 1, "支线": 1, "角色线": 1, "暗线": 0})


class CountFilesTest(unittest.TestCase):
    def test_storyline_file_pattern(self) -> None:
        """S{XX} 详情文件计入；timeline.md 不计入。"""
        keys = [
            "storyline/S01-成长主线.md",
            "storyline/S02-暗流.md",
            "storyline/timeline.md",
            "storyline.md",
            "character/陈远.md",
            "worldview.md",
        ]
        self.assertEqual(count_storyline_files(keys), 2)
        self.assertEqual(count_character_files(keys), 1)


class EvaluateQuotaTest(unittest.TestCase):
    def test_achieved_when_all_types_met(self) -> None:
        """各类型实际 >= 目标即达标（暗线目标 0 恒达标）。"""
        target = QuotaTarget(1, 1, 0, 0, source="core")
        status = evaluate_quota(target, {"主线": 1, "支线": 1, "角色线": 0, "暗线": 0})
        self.assertTrue(status.achieved)
        self.assertEqual(status.gaps, {})
        self.assertIn("配比已达标", status.summary_line())

    def test_gap_reported(self) -> None:
        """未达标类型给差额；已达标的类型不进 gaps。"""
        target = QuotaTarget(5, 1, 1, 0, source="core")
        status = evaluate_quota(target, {"主线": 2, "支线": 1, "角色线": 0, "暗线": 0})
        self.assertFalse(status.achieved)
        self.assertEqual(status.gaps, {"主线": 3, "角色线": 1})
        self.assertIn("主线还差3条", status.summary_line())

    def test_actual_missing_keys_counted_as_zero(self) -> None:
        """actual 缺键按 0 计，不误判、不抛异常（一览表部分缺失场景）。"""
        target = QuotaTarget(1, 1, 0, 0, source="core")
        status = evaluate_quota(target, {"主线": 1})
        self.assertFalse(status.achieved)
        self.assertEqual(status.gaps, {"支线": 1})


class CountWorkspaceTest(unittest.TestCase):
    def test_physical_workspace_count(self) -> None:
        """物理工作区：一览表类型分布 + S 文件数 + 人物档案数。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "storyline.md").write_text(_INDEX_MD, encoding="utf-8")
            (ws / "storyline").mkdir()
            (ws / "storyline" / "S01-代理觉醒.md").write_text("x" * 10, encoding="utf-8")
            (ws / "storyline" / "S02-人类火种.md").write_text("y" * 10, encoding="utf-8")
            (ws / "storyline" / "timeline.md").write_text("t" * 10, encoding="utf-8")
            (ws / "character").mkdir()
            (ws / "character" / "陈远.md").write_text("c" * 10, encoding="utf-8")

            result = count_workspace(ws)
            self.assertEqual(
                result["by_type"], {"主线": 1, "支线": 1, "角色线": 1, "暗线": 0},
            )
            self.assertEqual(result["line_files"], 2)
            self.assertEqual(result["characters"], 1)

    def test_empty_workspace(self) -> None:
        """空工作区（初构前）不抛异常，by_type=None、计数为 0。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = count_workspace(Path(tmp))
            self.assertIsNone(result["by_type"])
            self.assertEqual(result["line_files"], 0)
            self.assertEqual(result["characters"], 0)


if __name__ == "__main__":
    unittest.main()
