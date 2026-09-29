from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

# StorylineSingleLineLimitMiddleware 已迁进 harness 包（Phase 7），通过包加载后 import
from pathlib import Path as _Path
from app.platform.agent.loader import load_package
# 直读 harness 工作目录（与生产 artifact 同源）；生产拉取链路属于 artifact_client/loader 的测试
_HARNESS_DIR = _Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
load_package(_HARNESS_DIR)
from harness_current.middleware.storyline_single_line_limit import (
    StorylineSingleLineLimitMiddleware,
)

_HEADER_MAIN = "## 复仇线 · 主线 · 活跃"
_HEADER_SUB = "## 神域线 · 支线 · 活跃"


def _request_write(file_path: str, content: str, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": "write_file", "args": {"file_path": file_path, "content": content}, "id": call_id},
    )


def _request_edit(file_path: str, old: str, new: str, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={
            "name": "edit_file",
            "args": {"file_path": file_path, "old_string": old, "new_string": new},
            "id": call_id,
        },
    )


class _CallTracker:
    """记录 handler 是否被调用 + 返回固定值，用于断言「放行 vs 拦截」。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: object) -> str:
        self.calls += 1
        return "passed-through"


def _seed_storyline(workspace: Path, headers: int = 1) -> None:
    """预置 storyline.md（故事核心 + N 个线区块骨架）。"""
    blocks = "\n\n".join(
        [f"{_HEADER_MAIN if i == 0 else _HEADER_SUB}\n- 主要地点：某处\n- 全局走向：略"
         for i in range(headers)]
    )
    (workspace / "storyline.md").write_text(
        f"# 故事核心\n\n- Logline：略\n\n{blocks}\n", encoding="utf-8",
    )


class SingleLineLimitWriteTest(unittest.TestCase):
    """write_file 整文件写入：按磁盘旧内容 vs 新内容的区块头数差计净增。"""

    def test_initial_write_with_one_block_passes(self) -> None:
        """初构：storyline.md 不存在，写入含 1 个主线区块 → 净增 1，放行计数 1。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineSingleLineLimitMiddleware(Path(tmpdir), max_new_lines=1)
            tracker = _CallTracker()

            content = f"# 故事核心\n\n- Logline：略\n\n{_HEADER_MAIN}\n- 主要地点：青云宗\n"
            result = mw.wrap_tool_call(_request_write("/storyline.md", content), tracker)

            self.assertEqual(result, "passed-through")
            self.assertEqual(tracker.calls, 1)
            self.assertEqual(mw._new_line_count, 1)

    def test_write_two_new_blocks_blocked(self) -> None:
        """单次写入一次新增 2 个区块 → 超上限（1）拦截。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineSingleLineLimitMiddleware(Path(tmpdir), max_new_lines=1)
            tracker = _CallTracker()

            content = f"# 故事核心\n\n{_HEADER_MAIN}\n\n{_HEADER_SUB}\n"
            result = mw.wrap_tool_call(_request_write("/storyline.md", content), tracker)

            self.assertIsInstance(result, ToolMessage)
            self.assertIn("上限", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_rewrite_with_same_block_count_passes_without_count(self) -> None:
        """整文件重写但区块数不变（修订既有内容）→ 放行且不占额度。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed_storyline(workspace, headers=1)
            mw = StorylineSingleLineLimitMiddleware(workspace, max_new_lines=1)
            tracker = _CallTracker()

            content = f"# 故事核心\n\n- Logline：改写后的核心\n\n{_HEADER_MAIN}\n- 主要地点：另一处\n"
            result = mw.wrap_tool_call(_request_write("/storyline.md", content), tracker)

            self.assertEqual(result, "passed-through")
            self.assertEqual(mw._new_line_count, 0)

    def test_second_incremental_write_blocked(self) -> None:
        """增量：磁盘 1 区块 → 新内容 2 区块（第 1 次放行）；再 2→3（第 2 次拦截）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed_storyline(workspace, headers=1)
            mw = StorylineSingleLineLimitMiddleware(workspace, max_new_lines=1)
            tracker = _CallTracker()

            content2 = f"# 故事核心\n\n{_HEADER_MAIN}\n\n{_HEADER_SUB}\n"
            r1 = mw.wrap_tool_call(_request_write("/storyline.md", content2, "c1"), tracker)
            self.assertEqual(r1, "passed-through")
            self.assertEqual(mw._new_line_count, 1)

            _seed_storyline(workspace, headers=2)  # 模拟第 1 次写入已落盘
            content3 = f"# 故事核心\n\n{_HEADER_MAIN}\n\n{_HEADER_SUB}\n\n{_HEADER_SUB}\n"
            r2 = mw.wrap_tool_call(_request_write("/storyline.md", content3, "c2"), tracker)
            self.assertIsInstance(r2, ToolMessage)
            self.assertEqual(tracker.calls, 1)


class SingleLineLimitEditTest(unittest.TestCase):
    """edit_file：按 new_string 与 old_string 的区块头数差计净增。"""

    def test_edit_appending_new_block_passes(self) -> None:
        """edit 在文末追加 1 个新区块 → 净增 1，放行计数 1。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed_storyline(workspace, headers=1)
            mw = StorylineSingleLineLimitMiddleware(workspace, max_new_lines=1)
            tracker = _CallTracker()

            old = "- 主要地点：某处\n- 全局走向：略"
            new = old + f"\n\n{_HEADER_SUB}\n- 主要地点：九天神域\n- 全局走向：略"
            result = mw.wrap_tool_call(_request_edit("/storyline.md", old, new), tracker)

            self.assertEqual(result, "passed-through")
            self.assertEqual(mw._new_line_count, 1)

    def test_edit_modification_without_new_block_passes(self) -> None:
        """edit 只改字段文本（无区块头变化）→ 放行不计数。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed_storyline(workspace, headers=1)
            mw = StorylineSingleLineLimitMiddleware(workspace, max_new_lines=1)
            tracker = _CallTracker()

            result = mw.wrap_tool_call(
                _request_edit("/storyline.md", "- 全局走向：略", "- 全局走向：改写"),
                tracker,
            )
            self.assertEqual(result, "passed-through")
            self.assertEqual(mw._new_line_count, 0)

    def test_edit_two_new_blocks_blocked(self) -> None:
        """edit 一次塞进 2 个新区块 → 超上限拦截。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed_storyline(workspace, headers=1)
            mw = StorylineSingleLineLimitMiddleware(workspace, max_new_lines=1)
            tracker = _CallTracker()

            old = "- 全局走向：略"
            new = (f"{_HEADER_MAIN}\n- 略\n\n{_HEADER_SUB}\n- 略\n\n- 全局走向：略")
            result = mw.wrap_tool_call(_request_edit("/storyline.md", old, new), tracker)

            self.assertIsInstance(result, ToolMessage)
            self.assertEqual(tracker.calls, 0)


class SingleLineLimitScopeTest(unittest.TestCase):
    def test_non_storyline_file_passes(self) -> None:
        """写其他文件（worldview/character 等）不受约束。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineSingleLineLimitMiddleware(Path(tmpdir), max_new_lines=1)
            tracker = _CallTracker()

            result = mw.wrap_tool_call(
                _request_write("/worldview.md", f"# 世界观\n\n{_HEADER_MAIN}\n"), tracker,
            )
            self.assertEqual(result, "passed-through")
            self.assertEqual(mw._new_line_count, 0)

    def test_old_storyline_directory_write_passes(self) -> None:
        """旧格式路径 storyline/S01-x.md 已不受约束（新格式下该目录不存在）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineSingleLineLimitMiddleware(Path(tmpdir), max_new_lines=1)
            tracker = _CallTracker()

            result = mw.wrap_tool_call(
                _request_write("/storyline/S01-旧格式.md", "x"), tracker,
            )
            self.assertEqual(result, "passed-through")
            self.assertEqual(mw._new_line_count, 0)

    def test_read_tool_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineSingleLineLimitMiddleware(Path(tmpdir), max_new_lines=1)
            tracker = _CallTracker()

            request = SimpleNamespace(
                tool_call={"name": "read_file", "args": {"file_path": "/storyline.md"}, "id": "c1"},
            )
            result = mw.wrap_tool_call(request, tracker)
            self.assertEqual(result, "passed-through")
            self.assertEqual(mw._new_line_count, 0)

    def test_before_agent_resets_count(self) -> None:
        """同一实例多调用周期：before_agent 重置后重新享有额度。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            _seed_storyline(workspace, headers=1)
            mw = StorylineSingleLineLimitMiddleware(workspace, max_new_lines=1)
            tracker = _CallTracker()

            mw.before_agent(state={}, runtime=None)
            old = "- 全局走向：略"
            mw.wrap_tool_call(
                _request_edit("/storyline.md", old, old + f"\n\n{_HEADER_SUB}\n- 略"), tracker,
            )
            _seed_storyline(workspace, headers=2)
            blocked = mw.wrap_tool_call(
                _request_edit("/storyline.md", old, old + f"\n\n{_HEADER_SUB}\n- 略2"), tracker,
            )
            self.assertIsInstance(blocked, ToolMessage)

            mw.before_agent(state={}, runtime=None)
            self.assertEqual(mw._new_line_count, 0)
            _seed_storyline(workspace, headers=2)
            result = mw.wrap_tool_call(
                _request_edit("/storyline.md", old, old + f"\n\n{_HEADER_SUB}\n- 略3"), tracker,
            )
            self.assertEqual(result, "passed-through")


class SingleLineLimitAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_awrap_blocks_two_new_blocks(self) -> None:
        """异步路径同样按区块净增拦截。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineSingleLineLimitMiddleware(Path(tmpdir), max_new_lines=1)

            class _AsyncTracker:
                def __init__(self) -> None:
                    self.calls = 0

                async def __call__(self, request: object) -> str:
                    self.calls += 1
                    return "passed-through"

            tracker = _AsyncTracker()
            content = f"# 故事核心\n\n{_HEADER_MAIN}\n\n{_HEADER_SUB}\n"
            result = await mw.awrap_tool_call(_request_write("/storyline.md", content), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertEqual(tracker.calls, 0)


if __name__ == "__main__":
    unittest.main()
