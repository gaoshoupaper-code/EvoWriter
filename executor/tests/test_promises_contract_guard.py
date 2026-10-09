"""PromisesContractGuardMiddleware 测试（REQ-20261010-000638 FR-004 / AC-004 运行时）。

覆盖：台账写入拦截（锚点/枚举/放弃备注）、storyline 修订反查（删除被引用
事件即拦）、合法写入放行、异常降级。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"

for k in [k for k in sys.modules if k == "harness_current" or k.startswith("harness_current.")]:
    sys.modules.pop(k, None)
load_package(_HARNESS_DIR)

from harness_current.middleware.promises_contract_guard import (  # noqa: E402
    PromisesContractGuardMiddleware,
)

_STORYLINE = (
    "# 故事\n\n"
    "- Logline：略\n- 设计原则：略\n- 核心主题：略\n- 类型基调：略\n"
    "- 节奏曲线：首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5\n"
    "- 最终结局：略\n\n"
    "## 主线 · 主线 · 活跃\n\n- 主要地点：东荒\n- 全局走向：略\n\n"
    "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
    "| T1 | 灭门之夜 | 冲突 | 发展 | 东荒 | 林寒 |  | 5 | — | 略 |\n"
    "| T12 | 新教父立威 | 胜利 | 终局 | 东荒 | 林寒 |  | 5 | 大 | 略 |\n"
)

_LEDGER_HEADER = (
    "# 许诺台账\n\n"
    "| 编号 | 许诺 | 层级 | 所属线 | 状态 | 许诺事件 | 推进事件 | 兑现事件 | 备注 |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
)
_LEDGER_OK = _LEDGER_HEADER + (
    "| P1 | 查清灭门真相 | 主线大期待 | 全局 | 已兑现 | 灭门之夜 |  | 新教父立威 | 主悬念 |\n"
)


def _write_request(path: str, content: str, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": "write_file", "args": {"file_path": path, "content": content}, "id": call_id},
    )


def _edit_request(
    path: str, old: str, new: str, call_id: str = "c1", replace_all: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={
            "name": "edit_file",
            "args": {"file_path": path, "old_string": old, "new_string": new,
                     "replace_all": replace_all},
            "id": call_id,
        },
    )


class _Tracker:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: object) -> str:
        self.calls += 1
        return "passed-through"


class PromisesGuardTest(unittest.TestCase):
    def _seeded(self, tmpdir: str) -> Path:
        ws = Path(tmpdir)
        (ws / "storyline.md").write_text(_STORYLINE, encoding="utf-8")
        (ws / "promises.md").write_text(_LEDGER_OK, encoding="utf-8")
        return ws

    def test_valid_ledger_write_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            ledger = _LEDGER_OK + (
                "| P2 | 幕后黑手是谁 | 事件钩子 | 主线 | 已许诺 | 灭门之夜 |  |  |  |\n"
            )
            self.assertEqual(
                mw.wrap_tool_call(_write_request("/promises.md", ledger), tracker), "passed-through",
            )
            self.assertEqual(tracker.calls, 1)

    def test_missing_anchor_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            bad = _LEDGER_OK.replace("灭门之夜 |  | 新教父立威", "灭门夜 |  | 新教父立威")
            result = mw.wrap_tool_call(_write_request("/promises.md", bad), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("灭门夜", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_abandoned_without_note_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            bad = _LEDGER_OK.replace("已兑现 | 灭门之夜 |  | 新教父立威 | 主悬念",
                                     "已放弃 | 灭门之夜 |  |  | ")
            result = mw.wrap_tool_call(_write_request("/promises.md", bad), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("已放弃", result.content)

    def test_storyline_revision_removing_referenced_event_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            projected = _STORYLINE.replace(
                "| T12 | 新教父立威 | 胜利 | 终局 | 东荒 | 林寒 |  | 5 | 大 | 略 |\n", ""
            )
            result = mw.wrap_tool_call(_write_request("/storyline.md", projected), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("新教父立威", result.content)
            self.assertIn("台账", result.content)

    def test_storyline_revision_keeping_events_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            projected = _STORYLINE.replace("- 全局走向：略", "- 全局走向：改写")
            self.assertEqual(
                mw.wrap_tool_call(_write_request("/storyline.md", projected), tracker), "passed-through",
            )

    def test_internal_error_degrades_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            mw._read = lambda fn: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
            tracker = _Tracker()
            self.assertEqual(
                mw.wrap_tool_call(_write_request("/promises.md", _LEDGER_OK), tracker), "passed-through",
            )

    def test_non_target_path_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = PromisesContractGuardMiddleware(Path(tmpdir))
            tracker = _Tracker()
            self.assertEqual(
                mw.wrap_tool_call(_write_request("/worldview.md", "x"), tracker), "passed-through",
            )


class PromisesGuardEditFileTest(unittest.TestCase):
    """edit_file 投影路径（增量修订高频场景；评审补充：此前零覆盖）。"""

    def _seeded(self, tmpdir: str) -> Path:
        ws = Path(tmpdir)
        (ws / "storyline.md").write_text(_STORYLINE, encoding="utf-8")
        (ws / "promises.md").write_text(_LEDGER_OK, encoding="utf-8")
        return ws

    def test_edit_promises_to_missing_anchor_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            result = mw.wrap_tool_call(
                _edit_request("/promises.md", "| 新教父立威 |", "| 灭门夜 |"), tracker,
            )
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("灭门夜", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_edit_storyline_removing_referenced_event_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            result = mw.wrap_tool_call(
                _edit_request("/storyline.md", "| T12 | 新教父立威 | 胜利 | 终局 | 东荒 | 林寒 |  | 5 | 大 | 略 |\n", ""),
                tracker,
            )
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("新教父立威", result.content)

    def test_edit_old_string_not_on_disk_passes(self) -> None:
        """old_string 不在磁盘 → 放行交 file_state_tracker（与既有护栏口径一致）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            self.assertEqual(
                mw.wrap_tool_call(_edit_request("/promises.md", "不存在的原文", "x"), tracker),
                "passed-through",
            )

    def test_edit_replace_all_projection_full_replacement(self) -> None:
        """replace_all=True 且多处出现：投影须按全量替换校验（防首处外绕过）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = self._seeded(tmpdir)
            # 台账里「灭门之夜」出现两次（两行都引用）
            (ws / "promises.md").write_text(
                _LEDGER_OK
                + "| P2 | 复仇的代价 | 事件钩子 | 主线 | 已许诺 | 灭门之夜 |  |  |  |\n",
                encoding="utf-8",
            )
            mw = PromisesContractGuardMiddleware(ws)
            tracker = _Tracker()
            # replace_all 全量换成不存在的事件名 → 第二处也在投影内，应被锚点检查拦下
            result = mw.wrap_tool_call(
                _edit_request("/promises.md", "灭门之夜", "不存在的血案", replace_all=True), tracker,
            )
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("不存在的血案", result.content)


if __name__ == "__main__":
    unittest.main()
