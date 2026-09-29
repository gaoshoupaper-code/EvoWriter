"""StorylineIntegrityMiddleware 测试：单文件写入的完整性护栏（REQ-20260930-002231 FR-011）。

规则：写入 storyline.md 不得丢失既有线区块、区块内事件、线名/事件名。
护栏拦截后 Agent 重试同一写入（坚持语义）→ 放行一次（合法删减的逃生门）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from pathlib import Path as _Path
from app.platform.agent.loader import load_package

_HARNESS_DIR = _Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
load_package(_HARNESS_DIR)
from harness_current.middleware.storyline_integrity import (
    StorylineIntegrityMiddleware,
)


def _request_write(content: str, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": "write_file", "args": {"file_path": "/storyline.md", "content": content}, "id": call_id},
    )


def _request_edit(old: str, new: str, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={
            "name": "edit_file",
            "args": {"file_path": "/storyline.md", "old_string": old, "new_string": new},
            "id": call_id,
        },
    )


class _CallTracker:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: object) -> str:
        self.calls += 1
        return "passed-through"


def _seed(workspace: Path) -> None:
    """预置：1 主线（2 事件）+ 1 支线（1 事件）。"""
    (workspace / "storyline.md").write_text(
        "# 故事核心\n\n- Logline：略\n\n"
        "## 复仇线 · 主线 · 活跃\n- 主要地点：青云宗\n- 全局走向：略\n\n"
        "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |\n"
        "|------|------|------|------|------|------|------|------|\n"
        "| T1 | 灭门之夜 | 冲突 | 发展 | 青云宗 | 林寒 | | 略 |\n"
        "| T2 | 拜入外门 | 反转 | 发展 | 青云宗 | 林寒 | | 略 |\n\n"
        "## 感情线 · 支线 · 活跃\n- 主要地点：云岚城\n- 全局走向：略\n\n"
        "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |\n"
        "|------|------|------|------|------|------|------|------|\n"
        "| T1.5 | 初次相遇 | 悬念 | 发展 | 云岚城 | 苏晚 | | 略 |\n",
        encoding="utf-8",
    )


class IntegrityPassTest(unittest.TestCase):
    def test_adding_new_block_and_events_passes(self) -> None:
        """增量：加新线区块 + 新事件行 → 放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            content = (workspace / "storyline.md").read_text(encoding="utf-8") + (
                "\n## 暗流 · 暗线 · 暂伏\n- 主要地点：九州\n- 全局走向：略\n\n"
                "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |\n"
                "|------|------|------|------|------|------|------|------|\n"
                "| T2.2 | 黑手初现 | 悬念 | 发展 | 九州 | ？ | | 略 |\n"
            )
            result = mw.wrap_tool_call(_request_write(content), tracker)
            self.assertEqual(result, "passed-through")
            self.assertEqual(tracker.calls, 1)

    def test_modifying_field_text_passes(self) -> None:
        """改字段（走向/描述）→ 名称与结构不变 → 放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            result = mw.wrap_tool_call(
                _request_edit("- 全局走向：略", "- 全局走向：改写后的走向"), tracker,
            )
            self.assertEqual(result, "passed-through")
            self.assertEqual(tracker.calls, 1)

    def test_non_storyline_write_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineIntegrityMiddleware(Path(tmpdir))
            tracker = _CallTracker()
            request = SimpleNamespace(
                tool_call={"name": "write_file", "args": {"file_path": "/worldview.md", "content": "x"}, "id": "c1"},
            )
            self.assertEqual(mw.wrap_tool_call(request, tracker), "passed-through")


class IntegrityBlockTest(unittest.TestCase):
    def test_dropping_event_row_blocks(self) -> None:
        """整文件重写丢失一个事件行 → 拦截，消息点名丢失事件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            bad = (workspace / "storyline.md").read_text(encoding="utf-8").replace(
                "| T2 | 拜入外门 | 反转 | 发展 | 青云宗 | 林寒 | | 略 |\n", "",
            )
            result = mw.wrap_tool_call(_request_write(bad), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("拜入外门", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_dropping_block_blocks(self) -> None:
        """整文件重写丢失整个线区块 → 拦截。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            bad = text.split("## 感情线")[0]  # 砍掉感情线区块
            result = mw.wrap_tool_call(_request_write(bad), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("感情线", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_event_rename_blocks(self) -> None:
        """事件改名（旧名消失）→ 拦截（FR-003 名称稳定性）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            result = mw.wrap_tool_call(
                _request_edit("灭门之夜", "灭门惨案"), tracker,
            )
            self.assertIsInstance(result, ToolMessage)
            self.assertIn("灭门之夜", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_blocked_then_retried_same_loss_passes(self) -> None:
        """逃生门：同一丢失项第一次拦截，Agent 重试同一写入 → 放行（合法删减）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            bad = text.split("## 感情线")[0]
            blocked = mw.wrap_tool_call(_request_write(bad, "c1"), tracker)
            self.assertIsInstance(blocked, ToolMessage)

            retried = mw.wrap_tool_call(_request_write(bad, "c2"), tracker)
            self.assertEqual(retried, "passed-through")
            self.assertEqual(tracker.calls, 1)

    def test_different_loss_after_block_still_blocks(self) -> None:
        """拦截后换一个不同的丢失项重试 → 仍拦截（逃生门只认同一坚持）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            mw.wrap_tool_call(_request_write(text.split("## 感情线")[0], "c1"), tracker)
            bad2 = text.replace(
                "| T1 | 灭门之夜 | 冲突 | 发展 | 青云宗 | 林寒 | | 略 |\n", "",
            )
            result = mw.wrap_tool_call(_request_write(bad2, "c2"), tracker)
            self.assertIsInstance(result, ToolMessage)


class IntegrityEdgeTest(unittest.TestCase):
    def test_edit_with_nonexistent_old_string_passes(self) -> None:
        """old_string 不在磁盘内容中（模拟替换失败）→ 放行交 file_state_tracker 拦。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            tracker = _CallTracker()

            result = mw.wrap_tool_call(
                _request_edit("不存在的旧文本", "## 新线 · 支线 · 活跃\n"), tracker,
            )
            self.assertEqual(result, "passed-through")

    def test_initial_write_when_file_absent_passes(self) -> None:
        """初构：storyline.md 不存在 → 无旧内容可比 → 放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineIntegrityMiddleware(Path(tmpdir))
            tracker = _CallTracker()
            result = mw.wrap_tool_call(
                _request_write("# 故事核心\n\n## 复仇线 · 主线 · 活跃\n"), tracker,
            )
            self.assertEqual(result, "passed-through")

    def test_guard_internal_error_degrades_to_pass(self) -> None:
        """护栏自身异常 → 降级放行 + 不中断（FR-011 失败语义）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed(workspace)
            mw = StorylineIntegrityMiddleware(workspace)
            # 注入内部异常：让 _read_current 抛错
            mw._read_current = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
            tracker = _CallTracker()
            result = mw.wrap_tool_call(_request_write("任意内容"), tracker)
            self.assertEqual(result, "passed-through")


if __name__ == "__main__":
    unittest.main()
