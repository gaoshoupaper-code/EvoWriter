"""双泳道提示词结构契约测试（REQ-20261011-142529 FR-013 / AC-011）。

底座 = outline 工业七步法替换创作 agent（DEC-004 合并版：outline 目录为骨，
现有深度配套）。断言：
  - 两份自包含主文档的关键结构：单元流程（拍板后落盘/严禁自动连跑）、
    检验清单（双检验/对立测试/金手指代价/境界段≥卷数/前情接点/防崩三问）、
    沉淀拍板制（DEC-021）、回炉协议（FR-004）、legacy 修订模式（FR-011）
  - 期待台账八列 + 硬指标口径（DEC-007：观测不拦截）+ 爽点/钩子类型参考
  - demand 模板赛道字段 + 双泳道元信息（FR-009/DEC-013）
  - 体检参数配置（FR-007：随赛道）
  - 架构清单双 main 泳道与工具挂载（DEC-006/011/019）
  - 旧单泳道口径退场（storybuilding 三段式/配比/方案包在主文档中不出现）
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
_DESIGN = (_REPO / "prompts" / "storydesign_system.md").read_text(encoding="utf-8")
_STRUCTURE = (_REPO / "prompts" / "storystructure_system.md").read_text(encoding="utf-8")
_DEMAND = (_REPO / "prompts" / "demand_template.md").read_text(encoding="utf-8")
_CHECKUP_CFG = json.loads(
    (_REPO / "middleware" / "checkup_config.json").read_text(encoding="utf-8")
)
_ARCH = json.loads((_REPO / "architecture.json").read_text(encoding="utf-8"))


class UnitProtocolTest(unittest.TestCase):
    """单元流程铁律（FR-003，DEC-010/021）——两份主文档都必须钉死。"""

    def test_unit_flow_confirm_then_write(self) -> None:
        for doc in (_DESIGN, _STRUCTURE):
            self.assertIn("confirm_with_user", doc)
            self.assertIn("拍板后才能落盘", doc)
            self.assertIn("严禁自动", doc)

    def test_sedimentation_requires_approval(self) -> None:
        """DEC-021 沉淀拍板制：跨会话只认文件沉淀，沉淀须经用户拍板。"""
        for doc in (_DESIGN, _STRUCTURE):
            self.assertIn("沉淀拍板制", doc)
            self.assertIn("不得静默", doc)


class DesignDocTest(unittest.TestCase):
    """设计端主文档（第 0-3 步）。"""

    def test_two_iron_laws(self) -> None:
        self.assertIn("创意归用户", _DESIGN)
        self.assertIn("流程归你", _DESIGN)

    def test_four_steps_with_checklists(self) -> None:
        for step in ("第 0 步 赛道确认", "第 1 步 设计原则", "第 2 步 冲突引擎", "第 3 步 力量体系"):
            self.assertIn(step, _DESIGN)
        # 双检验（场景推演 3 个 + 爽点预感）
        self.assertIn("场景推演", _DESIGN)
        self.assertIn("爽点预感", _DESIGN)
        # 对立测试 + 镜像建议
        self.assertIn("对立测试", _DESIGN)
        # 金手指代价红线 + 境界段≥卷数
        self.assertIn("代价与限制", _DESIGN)
        self.assertIn("境界段数 ≥ 计划卷数", _DESIGN)

    def test_pipeline_position_and_rework(self) -> None:
        self.assertIn("只读", _DESIGN)
        self.assertIn("回炉", _DESIGN)

    def test_legacy_mode(self) -> None:
        """FR-011：旧五件套工作区走自由修订模式。"""
        self.assertIn("旧版工作区", _DESIGN)
        self.assertIn("storyline.md", _DESIGN)


class StructureDocTest(unittest.TestCase):
    """结构端主文档（第 4-7 步 + 台账）。"""

    def test_four_steps_with_checklists(self) -> None:
        for step in ("第 4 步 总纲", "第 5 步 卷纲", "第 6 步 章纲滚动", "第 7 步 卷末盘点"):
            self.assertIn(step, _STRUCTURE)
        self.assertIn("前情接点", _STRUCTURE)
        self.assertIn("黄金三章", _STRUCTURE)
        self.assertIn("防崩三问", _STRUCTURE)

    def test_ledger_eight_columns(self) -> None:
        """DEC-017：单一期待台账，八列 + 状态机 + 悬置 30 章。"""
        for col in ("ID", "层级", "期待内容", "埋设章", "最近推进章", "计划兑现章", "状态", "备注"):
            self.assertIn(col, _STRUCTURE)
        for status in ("活跃", "悬置🔴", "已兑现", "已放弃"):
            self.assertIn(status, _STRUCTURE)
        self.assertIn("30 章", _STRUCTURE)

    def test_hard_indicators_observed_not_enforced(self) -> None:
        """DEC-007：硬指标是体检口径（只建议不拦截）。"""
        self.assertIn("2000-2200", _STRUCTURE)
        self.assertIn("3-5 章", _STRUCTURE)
        self.assertIn("只建议", _STRUCTURE)

    def test_rework_ticket_protocol(self) -> None:
        """FR-004 回炉提案协议：白名单拦截 → submit_rework_ticket。"""
        self.assertIn("submit_rework_ticket", _STRUCTURE)
        self.assertIn("波及清单", _STRUCTURE)
        self.assertIn("否决", _STRUCTURE)

    def test_depth_companion_rules(self) -> None:
        """DEC-009 配套深度：配角三条件 / setting 同步 / 物品卡三段式。"""
        self.assertIn("配角引入三条件", _STRUCTURE)
        self.assertIn("worldview/setting.md", _STRUCTURE)
        self.assertIn("轨迹", _STRUCTURE)


class DemandTemplateTest(unittest.TestCase):
    """demand 模板：赛道字段 + 双泳道元信息（FR-009/DEC-013）。"""

    def test_track_fields(self) -> None:
        self.assertIn("平台（默认番茄）", _DEMAND)
        self.assertIn("品类（默认玄幻）", _DEMAND)

    def test_meta_declares_dual_lanes(self) -> None:
        self.assertIn('"storydesign"', _DEMAND)
        self.assertIn('"storystructure"', _DEMAND)
        self.assertIn("review_required: false", _DEMAND)
        self.assertIn("receipt_skip", _DEMAND)


class CheckupConfigTest(unittest.TestCase):
    """体检参数（FR-007/FR-009）：默认值 + 赛道覆盖。"""

    def test_defaults(self) -> None:
        self.assertEqual(_CHECKUP_CFG["default"]["max_big_payoff_gap"], 5)
        self.assertEqual(_CHECKUP_CFG["default"]["stale_chapters"], 30)

    def test_track_overrides(self) -> None:
        self.assertIn("番茄", _CHECKUP_CFG["tracks"])
        self.assertIn("起点", _CHECKUP_CFG["tracks"])


class ArchitectureTest(unittest.TestCase):
    """架构清单：双 main、工具与护栏挂载、白名单切分（DEC-006/011/019）。"""

    def test_dual_main_lanes(self) -> None:
        mains = [a["name"] for a in _ARCH["agents"] if a["role"] == "main"]
        self.assertEqual(mains, ["storydesign", "storystructure"])

    def test_middleware_keep_list(self) -> None:
        """DEC-011：只留拍板闸 + outline 契约（+ structure 物品卡）。"""
        design = next(a for a in _ARCH["agents"] if a["name"] == "storydesign")
        structure = next(a for a in _ARCH["agents"] if a["name"] == "storystructure")
        self.assertEqual(design["middleware"], ["receipt_gate", "outline_contract_guard"])
        self.assertEqual(
            structure["middleware"],
            ["receipt_gate", "outline_contract_guard", "object_contract_guard"],
        )
        # 已清理的旧中间件不得残留
        for agent in _ARCH["agents"]:
            for retired in ("review_gate", "skill_activation_guard", "quota_convergence", "pacing_report"):
                self.assertNotIn(retired, agent["middleware"])

    def test_tools_and_whitelist_split(self) -> None:
        design = next(a for a in _ARCH["agents"] if a["name"] == "storydesign")
        structure = next(a for a in _ARCH["agents"] if a["name"] == "storystructure")
        self.assertEqual(design["tools"], ["confirm_with_user"])
        self.assertEqual(structure["tools"], ["confirm_with_user", "submit_rework_ticket"])
        # 上游只读（FR-002）
        self.assertNotIn("/outline/00-赛道.md", structure["write_permissions"])
        self.assertIn("/outline/期待台账.md", structure["write_permissions"])
        self.assertIn("/character/core/*.md", design["write_permissions"])
        self.assertIn("/character/support/*.md", structure["write_permissions"])


class RetiredOldModelTest(unittest.TestCase):
    """旧单泳道口径退场（DEC-011）：主文档不得残留。"""

    def test_old_model_wording_retired(self) -> None:
        for doc in (_DESIGN, _STRUCTURE):
            for retired in ("三层流程", "方案包", "配比", "storybuilding"):
                self.assertNotIn(retired, doc)


if __name__ == "__main__":
    unittest.main()
