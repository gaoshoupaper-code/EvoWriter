"""三步制提示词结构契约测试（REQ-20261015-161200 FR-004/009，AC-008/AC-011）。

底座 = 大纲三步简化（DEC-001/004/005）。断言：
  - 四份主文档的关键结构：单元流程（拍板后落盘/严禁自动连跑）、
    世界观四小节、总纲卷纲模板、章纲批四列表、期待台账六列
  - 5 条输出风格铁律写进全部 Prompt（FR-004）
  - demand 模板 4 字段 + 元信息（FR-007）
  - 架构清单单主控 + 三子代理与工具/白名单挂载（DEC-005/009）
  - 双泳道/七步制口径退场（storydesign/checkup/rework 工单不残留）
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
_WRITER = (_REPO / "prompts" / "writer_system.md").read_text(encoding="utf-8")
_WORLDVIEW = (_REPO / "prompts" / "worldview_system.md").read_text(encoding="utf-8")
_OUTLINE = (_REPO / "prompts" / "outline_system.md").read_text(encoding="utf-8")
_CHAPTERS = (_REPO / "prompts" / "chapters_system.md").read_text(encoding="utf-8")
_DEMAND = (_REPO / "prompts" / "demand_template.md").read_text(encoding="utf-8")
_ARCH = json.loads((_REPO / "architecture.json").read_text(encoding="utf-8"))

_ALL_DOCS = (_WRITER, _WORLDVIEW, _OUTLINE, _CHAPTERS)


class StyleRulesTest(unittest.TestCase):
    """5 条输出风格铁律（FR-004，DEC-004）——四份主文档全部钉死。"""

    def test_style_rules_in_all_prompts(self) -> None:
        for doc in _ALL_DOCS:
            self.assertIn("禁破折号", doc)
            self.assertIn("禁解释性引导语", doc)
            self.assertIn("一句话", doc)
            self.assertIn("表格", doc)
            self.assertIn("不写设计理由", doc)

    def test_style_rules_are_concrete(self) -> None:
        # 规则②的引导语清单与规则①的目标符号必须具体可查（AC-003 程序检查口径）
        for doc in _ALL_DOCS:
            self.assertIn("——", doc)
            self.assertIn("这意味着", doc)


class WriterDocTest(unittest.TestCase):
    """主控主文档（FR-002/003，DEC-005/006/009）。"""

    def test_unit_flow_confirm_then_write(self) -> None:
        self.assertIn("confirm_with_user", _WRITER)
        self.assertIn("严禁自动连跑", _WRITER)

    def test_delegation_and_upstream_revision(self) -> None:
        self.assertIn("worldview", _WRITER)
        self.assertIn("outline", _WRITER)
        self.assertIn("chapters", _WRITER)
        self.assertIn("谁产出谁能改", _WRITER)

    def test_legacy_readonly_and_receipt_skip(self) -> None:
        self.assertIn("只读", _WRITER)
        self.assertIn("receipt_skip", _WRITER)

    def test_stop_points_per_unit(self) -> None:
        """DEC-002：产出单元粒度（世界观/总纲/每卷卷纲/章纲批）。"""
        for unit in ("世界观（1 次）", "总纲（1 次）", "每卷卷纲（每卷 1 次）", "章纲批（每批 5-8 章）"):
            self.assertIn(unit, _WRITER)


class WorldviewDocTest(unittest.TestCase):
    """世界观子代理（FR-001，DEC-008 单文件）。"""

    def test_four_sections(self) -> None:
        for section in ("核心冲突", "力量体系", "金手指", "大法则"):
            self.assertIn(section, _WORLDVIEW)

    def test_golden_finger_cost_redline(self) -> None:
        self.assertIn("代价与限制", _WORLDVIEW)
        self.assertIn("境界段数", _WORLDVIEW)

    def test_single_file_output(self) -> None:
        self.assertIn("outline/世界观.md", _WORLDVIEW)


class OutlineDocTest(unittest.TestCase):
    """总纲卷纲子代理（FR-001，DEC-001 人物/BOSS 挪入②）。"""

    def test_master_and_volume_templates(self) -> None:
        for marker in ("结局锚点", "分卷表", "本卷反派", "本卷期待", "大爽点布局表", "黄金三章"):
            self.assertIn(marker, _OUTLINE)

    def test_character_archive_ownership(self) -> None:
        self.assertIn("character/core/", _OUTLINE)

    def test_worldview_readonly(self) -> None:
        self.assertIn("只读", _OUTLINE)


class ChaptersDocTest(unittest.TestCase):
    """章纲子代理（FR-005，DEC-010 台账六列）。"""

    def test_batch_four_columns(self) -> None:
        self.assertIn("前情接点", _CHAPTERS)
        self.assertIn("| 章 | 剧情走向 | 章末钩子 | 期待动作 |", _CHAPTERS)

    def test_ledger_six_columns(self) -> None:
        self.assertIn("| 编号 | 层级 | 钩子 | 埋设章 | 兑现章 | 状态 |", _CHAPTERS)
        for status in ("活跃", "已兑现", "已放弃：原因"):
            self.assertIn(status, _CHAPTERS)
        self.assertIn("埋设必登记", _CHAPTERS)

    def test_upstream_readonly(self) -> None:
        self.assertIn("只读", _CHAPTERS)
        self.assertIn("矛盾报告主控", _CHAPTERS)


class DemandTemplateTest(unittest.TestCase):
    """demand 模板：4 字段 + 元信息（FR-007，DEC-007）。"""

    def test_four_fields(self) -> None:
        for field in ("平台（默认番茄）", "品类（默认玄幻）", "一句话点子", "金手指"):
            self.assertIn(field, _DEMAND)

    def test_meta_receipt_skip(self) -> None:
        self.assertIn("receipt_skip", _DEMAND)
        self.assertIn("严禁自动连跑", _DEMAND)


class ArchitectureTest(unittest.TestCase):
    """架构清单：单主控 + 三子代理、工具与护栏挂载、白名单切分（DEC-005/009）。"""

    def test_single_main_three_subs(self) -> None:
        mains = [a["name"] for a in _ARCH["agents"] if a["role"] == "main"]
        self.assertEqual(mains, ["writer"])
        subs = sorted(a["name"] for a in _ARCH["agents"] if a["role"] == "sub")
        self.assertEqual(subs, ["chapters", "outline", "worldview"])

    def test_middleware_and_tools(self) -> None:
        writer = next(a for a in _ARCH["agents"] if a["name"] == "writer")
        self.assertEqual(writer["middleware"], ["receipt_gate"])
        self.assertEqual(writer["tools"], ["confirm_with_user"])
        for name in ("worldview", "outline", "chapters"):
            sub = next(a for a in _ARCH["agents"] if a["name"] == name)
            self.assertEqual(sub["middleware"], ["receipt_gate", "outline_contract_guard"])
            self.assertEqual(sub["tools"], [])
        # 已清理的中间件不得残留
        for agent in _ARCH["agents"]:
            for retired in (
                "review_gate", "skill_activation_guard", "quota_convergence",
                "pacing_report", "object_contract_guard",
            ):
                self.assertNotIn(retired, agent["middleware"])

    def test_whitelist_split(self) -> None:
        worldview = next(a for a in _ARCH["agents"] if a["name"] == "worldview")
        outline = next(a for a in _ARCH["agents"] if a["name"] == "outline")
        chapters = next(a for a in _ARCH["agents"] if a["name"] == "chapters")
        self.assertEqual(worldview["write_permissions"], ["/outline/世界观.md"])
        self.assertIn("/outline/总纲.md", outline["write_permissions"])
        self.assertIn("/character/core/*.md", outline["write_permissions"])
        self.assertIn("/outline/期待台账.md", chapters["write_permissions"])
        self.assertIn("/character/support/*.md", chapters["write_permissions"])
        # 跨步骤只读互斥：章纲子代理不得写世界观/总纲
        for upstream in ("/outline/世界观.md", "/outline/总纲.md"):
            self.assertNotIn(upstream, chapters["write_permissions"])


class RetiredOldModelTest(unittest.TestCase):
    """双泳道/七步制口径退场（DEC-003/005）：主文档不得残留。"""

    def test_old_model_wording_retired(self) -> None:
        for doc in _ALL_DOCS:
            for retired in (
                "storydesign", "storystructure", "checkup", "submit_rework_ticket",
                "回炉工单", "卷末盘点", "体检报告", "悬置🔴", "配比",
                "三层流程", "方案包", "storybuilding",
            ):
                self.assertNotIn(retired, doc, f"主文档残留旧口径: {retired}")


if __name__ == "__main__":
    unittest.main()
