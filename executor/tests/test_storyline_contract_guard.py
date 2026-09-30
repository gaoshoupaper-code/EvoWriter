"""StorylineContractGuardMiddleware 测试（REQ-20260930-194437 FR-003/004/005/006/007/008）。

覆盖：
  - contracts 判定器 check_storyline_write：范围化结构规则 / 全局唯一性 /
    事件数量模板（非交汇口径）/ 最终结局不可变
  - 运行时中间件：拦截（business_intercept）/ 防死循环强制收尾注入 /
    干净写入重置计数 / 非目标路径放行 / 护栏异常降级
  - harness path_guard 白名单收紧（10 → 4）
  - storybuilding 装配：max_revisions=1 且契约护栏已挂载
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
    """全量跑时既有测试可能把 harness_current 顶替成临时目录的旧副本
    （存量串扰）——无条件清前缀后从真实 harness 目录重载，保证本文件
    断言的是当前仓库内容。"""
    for k in [k for k in sys.modules if k == "harness_current" or k.startswith("harness_current.")]:
        sys.modules.pop(k, None)
    load_package(_HARNESS_DIR)


_load_real_harness()
from harness_current.middleware.storyline_contract_guard import (  # noqa: E402
    StorylineContractGuardMiddleware,
)

from contracts.storyline_contract import check_storyline_write  # noqa: E402


# ── 样例构造 ────────────────────────────────────────────────

_TYPES = ("冲突", "危机", "反转", "悬念")


def _events(n: int, prefix: str, t_start: int = 1) -> str:
    rows = [
        f"| T{t_start + i} | {prefix}-事件{i + 1} | {_TYPES[i % 4]} | 发展 | 青云宗 | 林寒 | | 略 |"
        for i in range(n)
    ]
    return "\n".join(rows)


def _block(name: str, ltype: str = "主线", n: int = 12, extra_crossing: int = 0) -> str:
    rows = _events(n, name)
    for j in range(extra_crossing):
        rows += f"\n| T8.{j + 1} | {name}-交汇{j + 1} | 交汇 | 发展 | 青云宗 | 林寒 | 别的线 | 交汇描述 |"
    return (
        f"## {name} · {ltype} · 活跃\n\n- 主要地点：青云宗\n- 全局走向：略\n\n"
        "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |\n"
        "|------|------|------|------|------|------|------|------|\n" + rows + "\n"
    )


_CORE = "# 故事核心\n\n- Logline：略\n- 最终结局：重铸天道\n"


# ── contracts 判定器 ────────────────────────────────────────


class CheckWriteTest(unittest.TestCase):
    def test_initial_valid_write_passes(self) -> None:
        projected = _CORE + _block("主线一", "主线", 12)
        self.assertEqual(check_storyline_write("", projected), [])

    def test_initial_write_without_block_blocks(self) -> None:
        violations = check_storyline_write("", _CORE)
        self.assertTrue(any(v.rule == "contract" and "初构" in v.message for v in violations))

    def test_new_mainline_short_count_blocks(self) -> None:
        current = _CORE + _block("主线一", "主线", 12)
        projected = current + _block("支线一", "支线", 6) + _block("主线二", "主线", 9)
        hits = [v for v in check_storyline_write(current, projected) if v.rule == "event_count"]
        self.assertEqual(len(hits), 1)
        self.assertIn("9", hits[0].message)
        self.assertIn("12", hits[0].message)

    def test_extra_crossing_events_not_counted(self) -> None:
        current = _CORE + _block("主线一", "主线", 12)
        projected = current + _block("支线一", "支线", 6, extra_crossing=2)
        self.assertEqual(
            [v for v in check_storyline_write(current, projected) if v.rule == "event_count"],
            [],
        )

    def test_character_line_four_blocks_five_passes(self) -> None:
        current = _CORE + _block("主线一", "主线", 12)
        bad = current + _block("角色线一", "角色线", 4)
        self.assertTrue(any(
            v.rule == "event_count" for v in check_storyline_write(current, bad)
        ))
        good = current + _block("角色线一", "角色线", 5)
        self.assertEqual(
            [v for v in check_storyline_write(current, good) if v.rule == "event_count"],
            [],
        )

    def test_existing_block_change_no_count_check(self) -> None:
        current = _CORE + _block("主线一", "主线", 12)
        projected = current + "\n| T99 | 主线一-新钩子 | 冲突 | 发展 | 青云宗 | 林寒 | | 埋钩子 |\n"
        self.assertEqual(
            [v for v in check_storyline_write(current, projected) if v.rule == "event_count"],
            [],
        )

    def test_legacy_flaw_in_untouched_block_not_flagged(self) -> None:
        flawed = _block("旧线", "支线", 6).replace("- 全局走向：略\n", "")
        current = _CORE + flawed
        projected = current + _block("新线", "支线", 6)
        self.assertEqual(check_storyline_write(current, projected), [])

    def test_changed_block_gets_structural_check(self) -> None:
        current = _CORE + _block("主线一", "主线", 12)
        projected = current.replace("- 全局走向：略\n", "")
        self.assertTrue(any(
            v.rule == "contract" and "全局走向" in v.message
            for v in check_storyline_write(current, projected)
        ))

    def test_duplicate_names_flagged_globally(self) -> None:
        current = _CORE + _block("主线一", "主线", 12)
        dup_line = current + _block("主线一", "支线", 6)
        self.assertTrue(any("线名重复" in v.message for v in check_storyline_write(current, dup_line)))

        dup_event = current + _block("支线一", "支线", 6).replace(
            "支线一-事件1", "主线一-事件1",
        )
        self.assertTrue(any(
            "事件名重复" in v.message for v in check_storyline_write(current, dup_event)
        ))

    def test_invalid_event_type_word_flagged(self) -> None:
        block = _block("主线一", "主线", 12).replace("主线一-事件2 | 危机", "主线一-事件2 | 打斗")
        violations = check_storyline_write("", _CORE + block)
        self.assertTrue(any("类型词非法" in v.message and "打斗" in v.message for v in violations))

    def test_invalid_header_type_flagged(self) -> None:
        block = _block("主线一", "主线", 12).replace("· 主线 ·", "· 副线 ·")
        violations = check_storyline_write("", _CORE + block)
        self.assertTrue(any("类型词非法" in v.message and "副线" in v.message for v in violations))

    def test_invalid_t_number_flagged(self) -> None:
        block = _block("主线一", "主线", 12).replace("| T3 |", "| 3月 |")
        violations = check_storyline_write("", _CORE + block)
        self.assertTrue(any("时序号非法" in v.message for v in violations))

    def test_ending_immutable(self) -> None:
        current = _CORE + _block("主线一", "主线", 12)
        changed = current.replace("最终结局：重铸天道", "最终结局：改写结局")
        self.assertTrue(any(
            v.rule == "ending" for v in check_storyline_write(current, changed)
        ))
        removed = current.replace("- 最终结局：重铸天道\n", "")
        self.assertTrue(any(
            v.rule == "ending" and "不可删除" in v.message
            for v in check_storyline_write(current, removed)
        ))
        # 结局不变的其他改动 → 无 ending 违规
        kept = current.replace("- 全局走向：略", "- 全局走向：改写")
        self.assertEqual(
            [v for v in check_storyline_write(current, kept) if v.rule == "ending"],
            [],
        )


# ── 运行时中间件 ────────────────────────────────────────────


def _request_write(content: str, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": "write_file", "args": {"file_path": "/storyline.md", "content": content}, "id": call_id},
    )


class _CallTracker:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: object) -> str:
        self.calls += 1
        return "passed-through"


class GuardMiddlewareTest(unittest.TestCase):
    def _seeded(self, tmpdir: str) -> Path:
        workspace = Path(tmpdir)
        (workspace / "storyline.md").write_text(
            _CORE + _block("主线一", "主线", 12), encoding="utf-8",
        )
        return workspace

    def test_short_count_write_blocked_with_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            bad = (workspace / "storyline.md").read_text(encoding="utf-8") + _block("主线二", "主线", 9)
            result = mw.wrap_tool_call(_request_write(bad), tracker)
            self.assertIsInstance(result, ToolMessage)
            self.assertEqual(result.response_metadata.get("business_intercept"), True)
            self.assertIn("9", result.content)
            self.assertIn("12", result.content)
            self.assertEqual(tracker.calls, 0)

    def test_death_loop_forces_pass_and_wrapup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            bad = (workspace / "storyline.md").read_text(encoding="utf-8") + _block("主线二", "主线", 9)

            for i in range(3):  # 前三次拒绝
                self.assertIsInstance(mw.wrap_tool_call(_request_write(bad, f"c{i}"), tracker), ToolMessage)
            self.assertEqual(tracker.calls, 0)

            result = mw.wrap_tool_call(_request_write(bad, "c3"), tracker)  # 第四次放行
            self.assertEqual(result, "passed-through")
            self.assertEqual(tracker.calls, 1)

            injected = mw.before_model(None, None)
            self.assertIsNotNone(injected)
            msg = injected["messages"][0]
            self.assertIsInstance(msg, HumanMessage)
            self.assertIn("护栏强制收尾", msg.content)
            self.assertIsNone(mw.before_model(None, None))  # 只注入一次

    def test_clean_write_resets_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")

            mw.wrap_tool_call(_request_write(text + _block("主线二", "主线", 9), "c1"), tracker)
            good = text + _block("支线一", "支线", 6)
            self.assertEqual(mw.wrap_tool_call(_request_write(good, "c2"), tracker), "passed-through")
            # 计数已重置：再次违规仍是第 1 次拒绝
            self.assertIsInstance(
                mw.wrap_tool_call(_request_write(text + _block("主线三", "主线", 9), "c3"), tracker),
                ToolMessage,
            )

    def test_non_storyline_write_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mw = StorylineContractGuardMiddleware(Path(tmpdir))
            tracker = _CallTracker()
            request = SimpleNamespace(
                tool_call={"name": "write_file", "args": {"file_path": "/worldview.md", "content": "x"}, "id": "c1"},
            )
            self.assertEqual(mw.wrap_tool_call(request, tracker), "passed-through")

    def test_guard_internal_error_degrades_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            mw._read_current = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
            tracker = _CallTracker()
            self.assertEqual(
                mw.wrap_tool_call(_request_write("任意内容"), tracker), "passed-through",
            )


# ── path_guard 白名单收紧（FR-008）─────────────────────────


class PathGuardWhitelistTest(unittest.TestCase):
    def test_harness_whitelist_is_four_entries(self) -> None:
        _load_real_harness()
        from harness_current.middleware.path_guard import WRITING_WRITE_PATTERNS

        patterns = {p.pattern for p in WRITING_WRITE_PATTERNS}
        self.assertEqual(patterns, {
            r"^/character/[^/]+\.md$",
            r"^/storyline\.md$",
            r"^/worldview\.md$",
            r"^/review/[^/]+\.md$",
        })
        for forbidden in ("/outline.md", "/novel.md", "/chapter/x.md", "/detail/x.md", "/state_log.md", "/storyline/x.md"):
            self.assertFalse(
                any(p.match(forbidden) for p in WRITING_WRITE_PATTERNS),
                f"{forbidden} 不应再被白名单放行",
            )


# ── 装配断言（FR-006 + 护栏挂载）───────────────────────────


class AssemblyTest(unittest.TestCase):
    def test_max_revisions_one_and_guard_mounted(self) -> None:
        _load_real_harness()
        # 类身份必须取自同一次加载，否则 isinstance 对不上（两次 load_package
        # 产生两个独立的类对象）
        import harness_current.subagents.storybuilding as sb_mod
        from harness_current.middleware.storyline_contract_guard import (
            StorylineContractGuardMiddleware as GuardCls,
        )

        captured: dict = {}

        def fake_build_deep_subagent(**kwargs):
            captured.update(kwargs)
            return {"name": kwargs["name"]}

        original = sb_mod.build_deep_subagent
        sb_mod.build_deep_subagent = fake_build_deep_subagent
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                sb_mod.build_storybuilding_deep_subagent(
                    workspace_root=Path(tmpdir),
                    model=None,
                    backend=None,
                    middleware_factory=lambda name: [],
                )
        finally:
            sb_mod.build_deep_subagent = original

        self.assertEqual(captured["max_revisions"], 1)
        mounted = captured["subagent_middleware"]
        self.assertTrue(any(isinstance(m, GuardCls) for m in mounted))


if __name__ == "__main__":
    unittest.main()
