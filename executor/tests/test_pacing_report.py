"""节奏体检报告测试（REQ-20261010-000638 FR-005 / AC-005）。

覆盖 build_pacing_report 四类指标（形态偏差 / 爽点间隔 / 许诺健康 / 节拍
清单对照）、短主线降级、暗线不计入读者体验序列、review 注入与异常降级。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from langchain_core.messages import HumanMessage

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"

for k in [k for k in sys.modules if k == "harness_current" or k.startswith("harness_current.")]:
    sys.modules.pop(k, None)
load_package(_HARNESS_DIR)

from harness_current.middleware.pacing_config import PacingConfig, PayoffRules  # noqa: E402
import harness_current.middleware.pacing_report as pacing_mod  # noqa: E402
from harness_current.middleware.pacing_report import (  # noqa: E402
    PacingReportMiddleware,
    build_pacing_report,
)

# ── 样例：12 事件主线（T1~T12），形态 首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5 ──
# 中段张力全 4（违反中点谷≤2）；大爽在第 2 与第 12 位（间隔 10 > 8）。

_TENSIONS = (2, 3, 4, 4, 4, 4, 4, 4, 4, 4, 5, 5)
_PAYOFFS = ("—", "大", "小", "—", "—", "小", "—", "—", "小", "—", "—", "大")

_SHAPE = "首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5"

_MAINLINE_ROWS = "\n".join(
    f"| T{i + 1} | 主事件{i + 1} | 冲突 | 发展 | 东荒 | 林澈 |  | {_TENSIONS[i]} | {_PAYOFFS[i]} | 略 |"
    for i in range(12)
)

_STORYLINE = (
    "# 故事\n\n"
    "- Logline：略\n- 设计原则：略\n- 核心主题：略\n- 类型基调：略\n"
    f"- 节奏曲线：{_SHAPE}\n- 最终结局：略\n\n"
    "## 主线 · 主线 · 活跃\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
    "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n" + _MAINLINE_ROWS + "\n"
)

_PROMISES = (
    "# 许诺台账\n\n"
    "| 编号 | 许诺 | 层级 | 所属线 | 状态 | 许诺事件 | 推进事件 | 兑现事件 | 备注 |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
    "| P1 | 查清真相 | 主线大期待 | 全局 | 已兑现 | 主事件1 | 主事件6 | 主事件12 | |\n"
    "| P9 | 幕后黑手是谁 | 事件钩子 | 主线 | 已许诺 | 主事件2 |  |  |  |\n"
)

_CONFIG = PacingConfig(
    count_ranges={}, reference_types=frozenset(),
    payoff=PayoffRules(small_gap_max=3, big_gap_max=8, big_payoff_min_tension=4, big_payoff_lookback=2),
    shape_min_events=8,
    beat_checklist=(
        {"id": "midpoint_reversal", "desc": "中点段应有反转或揭露", "kind": "types_in_segment",
         "types": ["反转", "揭露"], "segment": "middle"},
        {"id": "valley_depth", "desc": "中点段应有低谷（张力 ≤2）", "kind": "valley",
         "max_tension": 2, "segment": "middle"},
        {"id": "final_peak", "desc": "终局段应有张力 ≥4 的峰", "kind": "peak",
         "min_tension": 4, "segment": "final"},
        {"id": "post_victory_hook", "desc": "胜利事件后 2 个事件内应开新钩子（悬念类型事件）",
         "kind": "post_victory_hook", "window": 2},
    ),
)


class BuildReportTest(unittest.TestCase):
    def test_all_four_indicator_kinds_detected(self) -> None:
        report = build_pacing_report(_STORYLINE, _PROMISES, _CONFIG)
        self.assertIsNotNone(report)
        assert report is not None  # narrowing
        self.assertIn("[节奏体检报告]", report)
        # 1. 形态偏差：中段最低 4 > 目标 ≤2
        self.assertIn("形态偏差·中点谷", report)
        # 2. 爽点间隔：大爽 主事件2 → 主事件12 间隔 10 > 8；铺垫峰值 3 < 4
        self.assertIn("爽点间隔·大", report)
        self.assertIn("间隔 10", report)
        self.assertIn("爽点铺垫·大", report)
        # 3. 许诺健康：P9 已许诺无推进
        self.assertIn("许诺无推进：P9 幕后黑手是谁", report)
        self.assertIn("许诺悬置", report)
        # 4. 节拍清单：中段无 反转/揭露 且最低张力 4 > 2
        self.assertIn("中点段应有反转或揭露", report)
        self.assertIn("中点段应有低谷", report)

    def test_small_payoff_gap_and_missing_twin_peaks_detected(self) -> None:
        """小爽间隔超限与终局单峰（双峰缺一）两个失败方向。"""
        # 小爽在 1/6 位（间隔 5 > 3）；终局段只有末位一个 ≥5 峰
        tensions = (2, 3, 4, 4, 2, 3, 4, 4, 3, 3, 4, 5)
        payoffs = ("小", "—", "—", "—", "—", "小", "—", "—", "—", "—", "—", "—")
        rows = "\n".join(
            f"| T{i + 1} | 密事件{i + 1} | 冲突 | 发展 | 东荒 | 林澈 |  | {tensions[i]} | {payoffs[i]} | 略 |"
            for i in range(12)
        )
        md = _STORYLINE.replace(_MAINLINE_ROWS, rows)
        report = build_pacing_report(md, "", _CONFIG)
        assert report is not None
        self.assertIn("爽点间隔·小", report)
        self.assertIn("间隔 5", report)
        self.assertIn("形态偏差·终局双峰", report)

    def test_final_slot_operator_respected(self) -> None:
        """终局槽非双峰按运算符判定：≤3 实际 2 达标不报；≈4 实际 2 报。"""
        base_rows = "\n".join(
            f"| T{i + 1} | 算事件{i + 1} | 冲突 | 发展 | 东荒 | 林澈 |  | {2 if i < 8 else 2} | — | 略 |"
            for i in range(12)
        )
        md_le = _STORYLINE.replace(_MAINLINE_ROWS, base_rows).replace(
            "终局双峰5,5", "终局≤3"
        )
        report = build_pacing_report(md_le, "", _CONFIG)
        assert report is not None
        self.assertNotIn("形态偏差·终局", report)
        md_eq = _STORYLINE.replace(_MAINLINE_ROWS, base_rows).replace(
            "终局双峰5,5", "终局≈4"
        )
        report = build_pacing_report(md_eq, "", _CONFIG)
        assert report is not None
        self.assertIn("形态偏差·终局峰", report)

    def test_compliant_storyline_reports_all_pass(self) -> None:
        # 合规布局：大爽在第 4/11 位（间隔 7 ≤8，前窗张力峰值 4/5 ≥4）；
        # 小爽在第 2/5/8 位（间隔 3 ≤3）；中段 pos5 张力 2 且类型「揭露」。
        tensions = (2, 3, 4, 4, 2, 2, 3, 4, 4, 5, 5, 4)
        payoffs = ("—", "小", "—", "大", "小", "—", "—", "小", "—", "—", "大", "—")
        types = ("冲突", "危机", "反转", "悬念", "揭露", "危机", "悬念", "冲突", "危机", "反转", "悬念", "冲突")
        rows = "\n".join(
            f"| T{i + 1} | 良事件{i + 1} | {types[i]} | 发展 | 东荒 | 林澈 |  | {tensions[i]} | {payoffs[i]} | 略 |"
            for i in range(12)
        )
        md = _STORYLINE.replace(_MAINLINE_ROWS, rows)
        promises = _PROMISES.replace(
            "| P1 | 查清真相 | 主线大期待 | 全局 | 已兑现 | 主事件1 | 主事件6 | 主事件12 |  |",
            "| P1 | 查清真相 | 主线大期待 | 全局 | 已兑现 | 良事件1 | 良事件6 | 良事件12 |  |",
        ).replace(
            "| P9 | 幕后黑手是谁 | 事件钩子 | 主线 | 已许诺 | 主事件2 |  |  |  |",
            "| P9 | 幕后黑手是谁 | 事件钩子 | 主线 | 已兑现 | 良事件2 | 良事件6 | 良事件11 |  |",
        )
        report = build_pacing_report(md, promises, _CONFIG)
        assert report is not None
        self.assertIn("全部指标达标", report)
        self.assertNotIn("形态偏差", report)
        self.assertNotIn("爽点间隔", report)
        self.assertNotIn("爽点铺垫", report)
        self.assertNotIn("许诺无推进", report)
        self.assertNotIn("节拍对照", report)

    def test_short_mainline_degrades_to_peak_valley(self) -> None:
        rows = "\n".join(
            f"| T{i + 1} | 短事件{i + 1} | 冲突 | 发展 | 东荒 | 林澈 |  | 3 | — | 略 |"
            for i in range(6)
        )
        md = _STORYLINE.replace(_MAINLINE_ROWS, rows)
        report = build_pacing_report(md, "", _CONFIG)
        assert report is not None
        self.assertIn("形态对比降级", report)
        self.assertNotIn("形态偏差·中点谷", report)

    def test_dark_line_excluded_from_reader_sequence(self) -> None:
        # 暗线 T5 是「反转」：若误计入读者序列，中点反转清单会命中而不报
        dark = (
            "## 暗流 · 暗线 · 暂伏\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
            "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n"
            "| T5 | 暗流揭露 | 反转 | 发展 | 东荒 | 影 |  | 5 | — | 略 |\n"
        )
        report = build_pacing_report(_STORYLINE + dark, "", _CONFIG)
        assert report is not None
        self.assertIn("中点段应有反转或揭露", report)

    def test_no_storyline_returns_none(self) -> None:
        self.assertIsNone(build_pacing_report("", "", _CONFIG))


class MiddlewareInjectTest(unittest.TestCase):
    def test_injects_report_into_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "storyline.md").write_text(_STORYLINE, encoding="utf-8")
            (ws / "promises.md").write_text(_PROMISES, encoding="utf-8")
            mw = PacingReportMiddleware(ws)
            injected = mw.before_agent(None, None)
            self.assertIsNotNone(injected)
            msg = injected["messages"][0]
            self.assertIsInstance(msg, HumanMessage)
            self.assertIn("[节奏体检报告]", msg.content)

    def test_exception_degrades_to_no_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "storyline.md").write_text(_STORYLINE, encoding="utf-8")
            mw = PacingReportMiddleware(ws)
            # 其他测试文件会在导入期整包重载 harness_current——按本文件导入时
            # 拿到的模块对象打 patch（类与模块同批加载，globals 一致）
            with mock.patch.object(
                pacing_mod, "build_pacing_report", side_effect=RuntimeError("boom"),
            ), self.assertLogs(pacing_mod.__name__, level="ERROR") as cm:
                self.assertIsNone(mw.before_agent(None, None))
            self.assertIn("节奏体检报告计算失败", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
