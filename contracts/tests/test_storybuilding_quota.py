"""故事构建配比契约测试（REQ-20260930-002231 FR-006：单文件区块头口径）。

demand 侧（parse_demand_quota / evaluate_quota）不随产物格式变化，测试维持；
产物侧计数从「一览表行 + storyline/S{XX} 文件名」切换为「storyline.md 区块头
`## {线名} · {类型} · {状态}` 类型词计数」。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts.storybuilding_quota import (
    QuotaTarget,
    count_character_files,
    count_workspace,
    evaluate_quota,
    parse_demand_quota,
    parse_storyline_blocks,
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

# 单文件新格式样例（FR-001 结构）
_STORYLINE_MD = """# 故事核心

- Logline：修士逆天改命
- 核心主题：反抗秩序

## 复仇线 · 主线 · 活跃
- 主要地点：青云宗、九天神域
- 全局走向：从家族弃子到执掌天道

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 描述 |
|------|------|------|------|------|------|------|
| T1 | 灭门之夜 | 冲突 | 发展 | 青云宗 | 林寒 | 一夜之间家破人亡。 |
| T2.5 | 拜入外门 | 转折 | 发展 | 青云宗 | 林寒、王长老 | 残锤非废，外门收留。 |

## 神域线 · 支线 · 活跃
- 主要地点：九天神域
- 全局走向：神域门槛的进与出

## 师徒线 · 角色线 · 活跃
- 主要地点：青云宗

## 暗流 · 暗线 · 暂伏
- 主要地点：九州各地
"""


class ParseDemandQuotaTest(unittest.TestCase):
    def test_parse_core_field(self) -> None:
        """核心层「目标配比」字段优先解析（demand 侧口径不变）。"""
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
        """核心层缺失时承诺点「结构约束」兜底。"""
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


class ParseStorylineBlocksTest(unittest.TestCase):
    def test_count_by_type(self) -> None:
        """区块头类型词计数（主线 1 / 支线 1 / 角色线 1 / 暗线 1）。"""
        counts = parse_storyline_blocks(_STORYLINE_MD)
        self.assertEqual(
            counts, {"主线": 1, "支线": 1, "角色线": 1, "暗线": 1},
        )

    def test_no_blocks_returns_none(self) -> None:
        """无区块头返回 None（调用方按软终止处理），空文档同理。"""
        self.assertIsNone(parse_storyline_blocks("# 故事核心\n\n只有核心没有线"))
        self.assertIsNone(parse_storyline_blocks(""))

    def test_bold_type_variant_counted(self) -> None:
        """类型词被 **粗体** 包裹的变体也计数（宽表兼容先例的同类容错）。"""
        md = (
            "## 复仇线 · **主线** · 活跃\n\n正文\n\n"
            "## 秩序线 · **支线** · 暂伏\n"
        )
        counts = parse_storyline_blocks(md)
        self.assertEqual(counts, {"主线": 1, "支线": 1, "角色线": 0, "暗线": 0})

    def test_non_header_lines_not_counted(self) -> None:
        """事件表行 / 列表行即使含类型词也不计（只认 ## 区块头）。"""
        md = (
            "# 故事核心\n\n"
            "- 全局走向：主线推进、支线交织\n\n"
            "| 时序 | 事件 | 类型 | 阶段 |\n"
            "|------|------|------|------|\n"
            "| T1 | x | 冲突 | 主线铺垫 |\n\n"
            "### 复仇线 · 主线 · 活跃\n"
        )
        # ### 三级标题不是线区块头（线区块固定 ## 二级）
        self.assertIsNone(parse_storyline_blocks(md))

    def test_trailing_bold_status_variant(self) -> None:
        """状态词缺失（标题只有两段）时按行尾类型词计数——宽容一档。"""
        md = "## 复仇线 · 主线\n"
        counts = parse_storyline_blocks(md)
        self.assertEqual(counts, {"主线": 1, "支线": 0, "角色线": 0, "暗线": 0})


class CountFilesTest(unittest.TestCase):
    def test_character_file_pattern(self) -> None:
        """人物档案路径计数维持（产物格式重构不影响 character 侧）。"""
        keys = [
            "storyline.md",
            "timeline.md",
            "character/陈远.md",
            "character/林玄霜.md",
            "worldview.md",
        ]
        self.assertEqual(count_character_files(keys), 2)


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
        """actual 缺键按 0 计，不误判、不抛异常（区块部分缺失场景）。"""
        target = QuotaTarget(1, 1, 0, 0, source="core")
        status = evaluate_quota(target, {"主线": 1})
        self.assertFalse(status.achieved)
        self.assertEqual(status.gaps, {"支线": 1})


class CountWorkspaceTest(unittest.TestCase):
    def test_single_file_workspace_count(self) -> None:
        """新格式工作区：storyline.md 区块头类型分布 + 线数 + 人物档案数。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "storyline.md").write_text(_STORYLINE_MD, encoding="utf-8")
            (ws / "character").mkdir()
            (ws / "character" / "林寒.md").write_text("c" * 10, encoding="utf-8")

            result = count_workspace(ws)
            self.assertEqual(
                result["by_type"], {"主线": 1, "支线": 1, "角色线": 1, "暗线": 1},
            )
            self.assertEqual(result["lines"], 4)
            self.assertEqual(result["characters"], 1)

    def test_empty_workspace(self) -> None:
        """空工作区（初构前）不抛异常，by_type=None、计数为 0。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = count_workspace(Path(tmp))
            self.assertIsNone(result["by_type"])
            self.assertEqual(result["lines"], 0)
            self.assertEqual(result["characters"], 0)

    def test_unparsable_storyline_returns_none_by_type(self) -> None:
        """storyline.md 存在但无区块头：by_type=None（类型词不可识别→软终止）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "storyline.md").write_text("# 故事核心\n\n只有核心", encoding="utf-8")
            result = count_workspace(ws)
            self.assertIsNone(result["by_type"])
            self.assertEqual(result["lines"], 0)


if __name__ == "__main__":
    unittest.main()
