"""ObjectContractGuardMiddleware 测试（REQ-20261004-221109 FR-004 / AC-002/003）。

覆盖：
  - 物品卡写入拦截：合法卡放行 / 锚点不存在拒（business_intercept）
  - storyline 修订反查：删除被引用事件拒（AC-003）/ 删未被引用事件放行（回归）
  - edit_file 卡片路径同样受控
  - 非目标路径放行 / 熔断放行 + 强制收尾注入 / 护栏异常降级放行
  - storybuilding 装配：白名单含 /object/*.md 且物品护栏已挂载
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, ToolMessage

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"


def _load_real_harness() -> None:
    """无条件清前缀后从真实 harness 目录重载（防存量串扰，同 storyline 测试口径）。"""
    for k in [k for k in sys.modules if k == "harness_current" or k.startswith("harness_current.")]:
        sys.modules.pop(k, None)
    load_package(_HARNESS_DIR)


_load_real_harness()
from harness_current.middleware.object_contract_guard import (  # noqa: E402
    ObjectContractGuardMiddleware,
)

# ── 样例 ────────────────────────────────────────────────────

_STORYLINE = """# 故事核心

- Logline：略
- 最终结局：断剑重铸

## 主线一 · 主线 · 活跃

- 主要地点：秘境
- 全局走向：从拾剑到断剑

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T1 | 少年拾剑 | 冲突 | 发展 | 秘境 | 林岸 | | 拾得古剑 |
| T2 | 魔主夺剑 | 危机 | 发展 | 青云宗 | 林岸、玄夜 | | 剑被夺 |
| T3 | 断剑之誓 | 胜利 | 终局 | 青云宗 | 林岸 | | 断剑退敌 |
"""

_CARD = """# 青云剑

## 基本信息

- 名称：青云剑
- 类型：武器
- 叙事可见性：明线

## 详情

上古遗剑。

## 轨迹

| 事件 | 变化 | 归属 | 备注 |
|------|------|------|------|
| 少年拾剑 | 登场 | 林岸 | 秘境拾得 |
| 魔主夺剑 | 易主 | 玄夜 | 强夺 |
| 断剑之誓 | 损坏 | 无主 | 自爆断剑 |
"""


def _request(path: str, content: str, call_id: str = "c1", tool: str = "write_file") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool, "args": {"file_path": path, "content": content}, "id": call_id},
    )


def _request_edit(path: str, old: str, new: str, call_id: str = "e1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": "edit_file", "args": {"file_path": path, "old_string": old, "new_string": new}, "id": call_id},
    )


class _CallTracker:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: object) -> str:
        self.calls += 1
        return "passed-through"


class ObjectGuardMiddlewareTest(unittest.TestCase):
    def _seeded(self, tmpdir: str, *, with_card: bool = True) -> Path:
        workspace = Path(tmpdir)
        (workspace / "storyline.md").write_text(_STORYLINE, encoding="utf-8")
        if with_card:
            obj_dir = workspace / "object"
            obj_dir.mkdir()
            (obj_dir / "青云剑.md").write_text(_CARD, encoding="utf-8")
        return workspace

    def test_valid_card_write_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir, with_card=False)
            mw = ObjectContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            self.assertEqual(
                mw.wrap_tool_call(_request("/object/青云剑.md", _CARD), tracker), "passed-through",
            )

    def test_dangling_anchor_write_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir, with_card=False)
            mw = ObjectContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            bad = _CARD.replace("| 少年拾剑 | 登场 |", "| 少年拾剑x | 登场 |")
            result = mw.wrap_tool_call(_request("/object/青云剑.md", bad), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertEqual(result.response_metadata.get("business_intercept"), True)
            self.assertIn("少年拾剑x", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_storyline_revision_deleting_referenced_event_blocked(self) -> None:
        """AC-003：删掉被卡片引用的事件 → storyline 写入被拒，报错含事件名与卡名。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)  # 磁盘已种 青云剑.md 引用 魔主夺剑
            mw = ObjectContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            revised = _STORYLINE.replace(
                "| T2 | 魔主夺剑 | 危机 | 发展 | 青云宗 | 林岸、玄夜 | | 剑被夺 |\n", "",
            )
            result = mw.wrap_tool_call(_request("/storyline.md", revised), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertEqual(result.response_metadata.get("business_intercept"), True)
            self.assertIn("魔主夺剑", result.content)
            self.assertIn("青云剑.md", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_storyline_revision_unreferenced_delete_passes(self) -> None:
        """回归：删未被引用的事件 / 改描述 → 物品护栏不拦（结构护栏另管）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = ObjectContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            revised = _STORYLINE.replace("剑被夺", "古剑易主")
            self.assertEqual(
                mw.wrap_tool_call(_request("/storyline.md", revised), tracker), "passed-through",
            )

    def test_edit_card_trajectory_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = ObjectContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            result = mw.wrap_tool_call(_request_edit(
                "/object/青云剑.md", "| 魔主夺剑 | 易主 |", "| 夺剑夜 | 易主 |",
            ), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("夺剑夜", result.content)

    def test_non_target_path_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = ObjectContractGuardMiddleware(Path(tmpdir))
            tracker = _CallTracker()
            self.assertEqual(
                mw.wrap_tool_call(_request("/character/林岸.md", "任意"), tracker), "passed-through",
            )

    def test_death_loop_forces_pass_and_wrapup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir, with_card=False)
            mw = ObjectContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            bad = _CARD.replace("| 少年拾剑 | 登场 |", "| 少年拾剑x | 登场 |")

            for i in range(3):  # 前三次拒绝
                self.assertIsInstance(
                    mw.wrap_tool_call(_request("/object/青云剑.md", bad, f"c{i}"), tracker), ToolMessage,
                )
            self.assertEqual(tracker.calls, 0)

            result = mw.wrap_tool_call(_request("/object/青云剑.md", bad, "c3"), tracker)  # 第四次放行
            self.assertEqual(result, "passed-through")
            self.assertEqual(tracker.calls, 1)

            injected = mw.before_model(None, None)
            self.assertIsNotNone(injected)
            self.assertIn("护栏强制收尾", injected["messages"][0].content)
            self.assertIsNone(mw.before_model(None, None))  # 只注入一次

    def test_guard_internal_error_degrades_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir, with_card=False)
            mw = ObjectContractGuardMiddleware(workspace)
            mw._read_file = lambda path: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
            tracker = _CallTracker()
            self.assertEqual(
                mw.wrap_tool_call(_request("/object/青云剑.md", _CARD), tracker), "passed-through",
            )

    def test_initial_storyline_write_passes(self) -> None:
        """初构（磁盘无 storyline、无卡）→ 反查无基线，放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = ObjectContractGuardMiddleware(Path(tmpdir))
            tracker = _CallTracker()
            self.assertEqual(
                mw.wrap_tool_call(_request("/storyline.md", _STORYLINE), tracker), "passed-through",
            )


class AssemblyTest(unittest.TestCase):
    def test_object_whitelist_and_guard_mounted(self) -> None:
        """M2 清单架构：物品卡写白名单 + 护栏挂载都以 architecture.json 为准。"""
        import json

        pkg_dir = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
        manifest = json.loads(
            (pkg_dir / "architecture.json").read_text(encoding="utf-8")
        )
        story = next(a for a in manifest["agents"] if a["name"] == "storybuilding")

        # 物品卡写白名单（REQ-20260804-221109：/object/*.md）
        self.assertIn("/object/*.md", story["write_permissions"])
        # 护栏挂载（有序 domain middleware 列表内）
        self.assertIn("object_contract_guard", story["middleware"])


if __name__ == "__main__":
    unittest.main()
