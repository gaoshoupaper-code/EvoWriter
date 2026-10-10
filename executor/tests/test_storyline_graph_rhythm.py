"""节奏数据派生测试（REQ-20261010-000638 FR-006 后端 / DEC-009/015）。

覆盖 build_rhythm_data：主线/合成/暗线三路曲线、同 T 取最大张力、
形态槽位解析、许诺进度解析、旧大纲（无张力）降级 None；
以及路由层 _attach_panorama 对 rhythm 与 PanoramaEvent.tension 的组装。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.domains.writing.expert_agent.services.storyline_graph import build_rhythm_data
from app.routers.workspaces import _attach_panorama
from app.schemas.screenplay import WorkspaceStorylineContent

_CORE = (
    "# 故事\n\n"
    "- Logline：略\n- 设计原则：略\n- 核心主题：略\n- 类型基调：略\n"
    "- 节奏曲线：首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5\n"
    "- 最终结局：略\n\n"
)

_MAIN_BLOCK = (
    "## 主线 · 主线 · 活跃\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
    "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
    "| T1 | 主事件一 | 冲突 | 发展 | 东荒 | 林寒 |  | 2 | — | 略 |\n"
    "| T2 | 主事件二 | 危机 | 发展 | 东荒 | 林寒 |  | 4 | 小 | 略 |\n"
    "| T5 | 主事件三 | 反转 | 终局 | 东荒 | 林寒 |  | 5 | 大 | 略 |\n"
)

# 支线 T2 与主线同 T：张力 3 < 主线 4 → 合成曲线该时点取 4（读者体感由最强线决定）
_SUPPORT_BLOCK = (
    "## 支线 · 支线 · 活跃\n\n- 主要地点：南疆\n- 全局走向：略\n\n"
    "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
    "| T2 | 支事件一 | 冲突 | 发展 | 南疆 | 阿禾 |  | 3 | — | 略 |\n"
    "| T6 | 支事件二 | 胜利 | 终局 | 南疆 | 阿禾 |  | 2 | 小 | 略 |\n"
)

_DARK_BLOCK = (
    "## 暗流 · 暗线 · 暂伏\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
    "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
    "| T3 | 暗流涌动 | 悬念 | 发展 | 东荒 | 影 |  | 4 | — | 略 |\n"
)

_HOOKS = (
    "# 钩子登记\n\n"
    "| 编号 | 钩子 | 层级 | 类型 | 埋设事件 | 推进事件 | 兑现事件 | 状态 | 备注 |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
    "| H1 | 查清真相 | 主线大期待 | 悬念 | 主事件一 | 主事件二 | 主事件三 | 已收 |  |\n"
    "| H2 | 支线的宝藏 | 线级期待 | 期待 | 支事件一 | 支事件二 |  | 推进中 |  |\n"
)


def _workspace(tmpdir: str, storyline: str, promises: str | None = _HOOKS) -> Path:
    ws = Path(tmpdir)
    (ws / "storyline.md").write_text(storyline, encoding="utf-8")
    if promises is not None:
        (ws / "hooks.md").write_text(promises, encoding="utf-8")
    return ws


class BuildRhythmDataTest(unittest.TestCase):
    def test_full_rhythm_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + _MAIN_BLOCK + _SUPPORT_BLOCK + _DARK_BLOCK)
            rhythm = build_rhythm_data(ws)
            self.assertIsNotNone(rhythm)
            assert rhythm is not None  # narrowing

            # 主线曲线：3 点、含张力爽点
            self.assertEqual([p.name for p in rhythm.mainline], ["主事件一", "主事件二", "主事件三"])
            self.assertEqual([p.tension for p in rhythm.mainline], [2, 4, 5])
            self.assertEqual(rhythm.mainline[2].payoff, "大")

            # 合成曲线：明线按 T 序（T1 主、T2 主+支取 4、T5 主、T6 支）；暗线 T3 不进
            self.assertEqual(
                [(p.t, p.tension) for p in rhythm.synthesis],
                [("T1", 2), ("T2", 4), ("T5", 5), ("T6", 2)],
            )
            self.assertNotIn("暗流涌动", [p.name for p in rhythm.synthesis])

            # 暗线单独曲线
            self.assertEqual([p.name for p in rhythm.dark.get("暗流", [])], ["暗流涌动"])

            # 形态槽位
            self.assertEqual([s.slot for s in rhythm.shape_slots], ["首事件", "前段末", "中点谷", "终局"])
            self.assertTrue(rhythm.shape_slots[3].twin_peak)
            self.assertEqual(rhythm.shape_slots[3].values, (5, 5))

            # 许诺进度
            self.assertEqual([r.id for r in rhythm.hooks], ["H1", "H2"])
            self.assertEqual(rhythm.hooks[1].status, "推进中")

    def test_legacy_storyline_without_tension_returns_none(self) -> None:
        legacy = (
            "# 故事\n\n- Logline：x\n- 最终结局：y\n\n"
            "## 主线 · 主线 · 活跃\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
            "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| T1 | 旧事件 | 冲突 | 发展 | 东荒 | 林寒 |  | 略 |\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, legacy)
            self.assertIsNone(build_rhythm_data(ws))

    def test_synthesis_same_t_takes_higher_tension_both_directions(self) -> None:
        """同 T 合并取最大张力：支线高于主线时也取支线；同张力保留其一。"""
        support_higher = _SUPPORT_BLOCK.replace(
            "| T2 | 支事件一 | 冲突 | 发展 | 南疆 | 阿禾 |  | 3 | — | 略 |",
            "| T2 | 支事件一 | 冲突 | 发展 | 南疆 | 阿禾 |  | 5 | — | 略 |",
        )
        support_equal = _SUPPORT_BLOCK.replace(
            "| T2 | 支事件一 | 冲突 | 发展 | 南疆 | 阿禾 |  | 3 | — | 略 |",
            "| T2 | 支事件一 | 冲突 | 发展 | 南疆 | 阿禾 |  | 4 | — | 略 |",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + _MAIN_BLOCK + support_higher)
            rhythm = build_rhythm_data(ws)
            assert rhythm is not None
            t2 = [p for p in rhythm.synthesis if p.t == "T2"]
            self.assertEqual(len(t2), 1)
            self.assertEqual((t2[0].tension, t2[0].name), (5, "支事件一"))
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + _MAIN_BLOCK + support_equal)
            rhythm = build_rhythm_data(ws)
            assert rhythm is not None
            self.assertEqual(len([p for p in rhythm.synthesis if p.t == "T2"]), 1)

    def test_unparseable_t_events_not_collapsed_in_synthesis(self) -> None:
        """空 T 号事件不参与同点合并——全部保留，不坍缩消失。"""
        rows = "\n".join([
            "| T1 | 主事件一 | 冲突 | 发展 | 东荒 | 林寒 |  | 2 | — | 略 |",
            "|  | 主事件二 | 冲突 | 发展 | 东荒 | 林寒 |  | 3 | 小 | 略 |",
            "|  | 主事件三 | 冲突 | 发展 | 东荒 | 林寒 |  | 5 | 大 | 略 |",
            "| T2 | 主事件四 | 冲突 | 发展 | 东荒 | 林寒 |  | 4 | — | 略 |",
        ])
        block = (
            "## 主线 · 主线 · 活跃\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
            "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n" + rows + "\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + block)
            rhythm = build_rhythm_data(ws)
            assert rhythm is not None
            names = [p.name for p in rhythm.synthesis]
            self.assertEqual(len(names), 4)  # 空 T 两事件均保留
            self.assertIn("主事件二", names)
            self.assertIn("主事件三", names)

    def test_dark_line_surface_point_marked(self) -> None:
        """状态 可浮出 的暗线：最后事件标 surface=True（FR-006 浮出时点打标）。"""
        surfaced = _DARK_BLOCK.replace("## 暗流 · 暗线 · 暂伏", "## 暗流 · 暗线 · 可浮出")
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + _MAIN_BLOCK + surfaced)
            rhythm = build_rhythm_data(ws)
            assert rhythm is not None
            pts = rhythm.dark.get("暗流", [])
            self.assertEqual([p.surface for p in pts], [True])
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + _MAIN_BLOCK + _DARK_BLOCK)
            rhythm = build_rhythm_data(ws)
            assert rhythm is not None
            self.assertEqual([p.surface for p in rhythm.dark.get("暗流", [])], [False])

    def test_missing_storyline_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(build_rhythm_data(Path(tmpdir)))


class AttachPanoramaRhythmTest(unittest.TestCase):
    def test_attach_sets_rhythm_and_tension_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + _MAIN_BLOCK + _SUPPORT_BLOCK + _DARK_BLOCK)
            content = WorkspaceStorylineContent(workspace_id="w1", format="v2", markdown="x")
            result = _attach_panorama(content, ws)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIsNotNone(result.rhythm)
            assert result.rhythm is not None
            self.assertEqual(len(result.rhythm.mainline), 3)
            self.assertEqual(result.rhythm.hooks[0].id, "H1")
            # 全景表行带上张力/爽点
            by_name = {ev.name: ev for ev in result.panorama}
            self.assertEqual(by_name["主事件三"].tension, 5)
            self.assertEqual(by_name["主事件三"].payoff, "大")
            self.assertIsNone(by_name.get("旧事件"))

    def test_legacy_format_not_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = _workspace(tmpdir, _CORE + _MAIN_BLOCK)
            content = WorkspaceStorylineContent(workspace_id="w1", format="legacy", markdown="x")
            result = _attach_panorama(content, ws)
            assert result is not None
            self.assertIsNone(result.rhythm)
            self.assertEqual(result.panorama, [])


if __name__ == "__main__":
    unittest.main()
