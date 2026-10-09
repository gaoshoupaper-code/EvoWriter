"""StorylineContractGuardMiddleware 测试（REQ-20260930-194437 FR-003/004/005/006/007/008；
REQ-20261009-182730：数量/类型词拦截移除 + 观测模式）。

覆盖：
  - contracts 判定器 check_storyline_write：范围化结构规则 / 全局唯一性 /
    最终结局不可变；事件数量与类型词不再产生违规（语义校验已移除）
  - 运行时中间件：结构拦截（business_intercept）/ 防死循环强制收尾注入 /
    干净写入重置计数 / 非目标路径放行 / 护栏异常降级
  - 观测模式（REQ-20261009-182730 FR-004）：数量/类型词放行 + 遵循率日志
    （区间判定 / 自定义词标注 / 配置缺失降级 / 观测异常降级）
  - harness path_guard 白名单收紧（10 → 4）
  - storybuilding 装配：max_revisions=1 且契约护栏已挂载
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
import harness_current.middleware.pacing_config as pacing_config_mod  # noqa: E402
import harness_current.middleware.storyline_contract_guard as guard_mod  # noqa: E402

from contracts.storyline_contract import check_storyline_write  # noqa: E402


# ── 样例构造 ────────────────────────────────────────────────

_TYPES = ("冲突", "危机", "反转", "悬念")
_GUARD_LOGGER = "harness_current.middleware.storyline_contract_guard"


def _events(n: int, prefix: str, t_start: int = 1) -> str:
    rows = [
        f"| T{t_start + i} | {prefix}-事件{i + 1} | {_TYPES[i % 4]} | 发展 | 青云宗 | 林寒 | | {(i % 5) + 1} | — | 略 |"
        for i in range(n)
    ]
    return "\n".join(rows)


def _block(
    name: str,
    ltype: str = "主线",
    n: int = 12,
    extra_crossing: int = 0,
    types: tuple[str, ...] | None = None,
) -> str:
    words = list(types) if types is not None else [_TYPES[i % 4] for i in range(n)]
    rows = [
        f"| T{i + 1} | {name}-事件{i + 1} | {w} | 发展 | 青云宗 | 林寒 | | {(i % 5) + 1} | — | 略 |"
        for i, w in enumerate(words)
    ]
    for j in range(extra_crossing):
        rows.append(f"| T8.{j + 1} | {name}-交汇{j + 1} | 交汇 | 发展 | 青云宗 | 林寒 | 别的线 | 4 | 小 | 交汇描述 |")
    return (
        f"## {name} · {ltype} · 活跃\n\n- 主要地点：青云宗\n- 全局走向：略\n\n"
        "| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 张力 | 爽点 | 描述 |\n"
        "|------|------|------|------|------|------|------|------|------|------|\n" + "\n".join(rows) + "\n"
    )


# REQ-20261010-000638：六字段故事核心（设计原则 + 槽位目标形态）
_CORE = (
    "# 故事核心\n\n"
    "- Logline：略\n"
    "- 设计原则：越强的力量越要付出人性代价\n"
    "- 核心主题：代价与成长\n"
    "- 类型基调：东方玄幻·热血\n"
    "- 节奏曲线：首事件≈2 · 前段末≥4 · 中点谷≤2 · 终局双峰5,5\n"
    "- 最终结局：重铸天道\n"
)


# ── contracts 判定器 ────────────────────────────────────────


class CheckWriteTest(unittest.TestCase):
    def test_initial_valid_write_passes(self) -> None:
        projected = _CORE + _block("主线一", "主线", 12)
        self.assertEqual(check_storyline_write("", projected), [])

    def test_initial_write_without_block_blocks(self) -> None:
        violations = check_storyline_write("", _CORE)
        self.assertTrue(any(v.rule == "contract" and "初构" in v.message for v in violations))

    def test_semantic_count_and_type_words_not_enforced(self) -> None:
        """REQ-20261009-182730 FR-001：数量与类型词不再产生违规（AC-001 边界样例同源）。"""
        current = _CORE + _block("主线一", "主线", 12)
        for block in (
            _block("主线二", "主线", 9),                       # 区间内（旧口径弹）
            _block("主线三", "主线", 3),                       # 低于参考下限
            _block("主线四", "主线", 20),                      # 高于参考上限
            _block("支线一", "支线", 6, types=("冲突", "背叛", "危机", "危机", "悬念", "胜利")),
        ):
            self.assertEqual(
                check_storyline_write(current, current + block), [],
                f"语义校验已移除，不应拦截：{block[:30]}…",
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

    def test_custom_event_type_word_passes(self) -> None:
        block = _block("主线一", "主线", 12).replace("主线一-事件2 | 危机", "主线一-事件2 | 背叛")
        self.assertEqual(check_storyline_write("", _CORE + block), [])

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

    def test_count_and_custom_type_write_passes(self) -> None:
        """AC-001：数量/类型词不合参考值不再拦截——写入直接放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            for block in (
                _block("主线二", "主线", 3),   # 低于下限
                _block("主线三", "主线", 20),  # 高于上限
                _block("支线一", "支线", 6, types=("冲突", "背叛", "危机", "危机", "悬念", "胜利")),
            ):
                self.assertEqual(
                    mw.wrap_tool_call(_request_write(text + block), tracker), "passed-through",
                )
            self.assertEqual(tracker.calls, 3)

    def test_death_loop_forces_pass_and_wrapup(self) -> None:
        """结构违规（非法时序号）触发防死循环：3 拒后放行 + 强制收尾注入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            bad = text + _block("主线二", "主线", 12).replace("| T3 |", "| 3月 |")

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
            bad = _block("主线二", "主线", 12).replace("| T3 |", "| 3月 |")

            mw.wrap_tool_call(_request_write(text + bad, "c1"), tracker)
            good = text + _block("支线一", "支线", 6)
            self.assertEqual(mw.wrap_tool_call(_request_write(good, "c2"), tracker), "passed-through")
            # 计数已重置：再次违规仍是第 1 次拒绝
            self.assertIsInstance(
                mw.wrap_tool_call(_request_write(text + bad, "c3"), tracker),
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


# ── 观测模式（REQ-20261009-182730 FR-004：只记日志，永不拦截）──


class ObservationModeTest(unittest.TestCase):
    def _seeded(self, tmpdir: str) -> Path:
        workspace = Path(tmpdir)
        (workspace / "storyline.md").write_text(
            _CORE + _block("主线一", "主线", 12), encoding="utf-8",
        )
        return workspace

    def test_in_range_observation_logged(self) -> None:
        """观测日志含线类型 / 实际数 / 区间判定 / 类型词分布 / 张力爽点分布 / 自定义词。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            block = _block(
                "支线一", "支线", 6,
                types=("危机", "危机", "反转", "牺牲", "牺牲", "牺牲"),
            )
            with self.assertLogs(_GUARD_LOGGER, level="INFO") as cm:
                self.assertEqual(
                    mw.wrap_tool_call(_request_write(text + block), tracker), "passed-through",
                )
            out = "\n".join(cm.output)
            self.assertIn("line_type=支线", out)
            self.assertIn("block=支线一", out)
            self.assertIn("non_crossing=6", out)
            self.assertIn("range=[4,9]", out)
            self.assertIn("in_range=True", out)
            self.assertIn("危机x2", out)
            self.assertIn("牺牲x3", out)
            self.assertIn("custom=牺牲x3", out)
            # 张力分布（夹具 n=6 → 1,2,3,4,5,1）与爽点分布（全 —）
            self.assertIn("tension=1x2/2x1/3x1/4x1/5x1", out)
            self.assertIn("payoff=—x6", out)

    def test_out_of_range_observation_logged_but_never_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            with self.assertLogs(_GUARD_LOGGER, level="INFO") as cm:
                self.assertEqual(
                    mw.wrap_tool_call(_request_write(text + _block("支线二", "支线", 2)), tracker),
                    "passed-through",
                )
            out = "\n".join(cm.output)
            self.assertIn("non_crossing=2", out)
            self.assertIn("in_range=False", out)
            self.assertEqual(tracker.calls, 1)

    def test_config_missing_degrades_to_actual_counts_only(self) -> None:
        """FR-003 失败语义：配置缺失 → 只记实际数，无区间判定，仍不影响写入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            with mock.patch.object(guard_mod, "_PACING_CONFIG", None), \
                    self.assertLogs(_GUARD_LOGGER, level="INFO") as cm:
                self.assertEqual(
                    mw.wrap_tool_call(_request_write(text + _block("支线一", "支线", 6)), tracker),
                    "passed-through",
                )
            out = "\n".join(cm.output)
            self.assertIn("range=None", out)
            self.assertIn("in_range=None", out)
            self.assertIn("只记实际数", out)
            self.assertIn("non_crossing=6", out)
            self.assertIn("tension=", out)
            self.assertIn("payoff=", out)

    def test_observation_exception_degrades_to_pass(self) -> None:
        """AC-005：观测自身异常 → 降级跳过并记异常日志，写入照常放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            with mock.patch.object(
                guard_mod, "iter_line_blocks", side_effect=RuntimeError("obs-boom"),
            ), self.assertLogs(_GUARD_LOGGER, level="ERROR") as cm:
                self.assertEqual(
                    mw.wrap_tool_call(_request_write(text + _block("支线一", "支线", 6)), tracker),
                    "passed-through",
                )
            self.assertIn("storyline 观测异常", "\n".join(cm.output))
            self.assertEqual(tracker.calls, 1)

    def test_crossing_events_excluded_from_observation(self) -> None:
        """FR-004 口径：交汇事件不计入非交汇数与类型词分布。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            block = _block("支线三", "支线", 6, extra_crossing=2)
            with self.assertLogs(_GUARD_LOGGER, level="INFO") as cm:
                self.assertEqual(
                    mw.wrap_tool_call(_request_write(text + block), tracker), "passed-through",
                )
            out = "\n".join(cm.output)
            self.assertIn("non_crossing=6", out)  # 2 条交汇行不计入
            self.assertNotIn("交汇x", out)        # 分布不含交汇词
            self.assertIn("in_range=True", out)    # 6 ∈ [4,9]

    def test_unchanged_block_not_reobserved(self) -> None:
        """FR-004 口径：既有未变更区块不重复观测。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            self.assertEqual(
                mw.wrap_tool_call(_request_write(text), tracker), "passed-through",
            )
            self.assertNoLogs(_GUARD_LOGGER, level="INFO")

    def test_line_type_without_range_logs_actual_only(self) -> None:
        """rng None 分支：线类型无参考区间 → 只记实际数。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = self._seeded(tmpdir)
            mw = StorylineContractGuardMiddleware(workspace)
            tracker = _CallTracker()
            text = (workspace / "storyline.md").read_text(encoding="utf-8")
            with mock.patch.object(
                guard_mod, "_PACING_CONFIG",
                pacing_config_mod.PacingConfig(count_ranges={}, reference_types=frozenset()),
            ), self.assertLogs(_GUARD_LOGGER, level="INFO") as cm:
                self.assertEqual(
                    mw.wrap_tool_call(_request_write(text + _block("支线一", "支线", 6)), tracker),
                    "passed-through",
                )
            out = "\n".join(cm.output)
            self.assertIn("该线类型无参考区间", out)
            self.assertIn("non_crossing=6", out)

    def test_corrupt_config_load_returns_none(self) -> None:
        """加载器异常分支：损坏 JSON → 返回 None + 错误日志（降级只记实际数）。"""
        import tempfile as _tf

        _PACING_LOGGER = "harness_current.middleware.pacing_config"
        with _tf.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "bad_config.json"
            bad.write_text("{ not-json", encoding="utf-8")
            with mock.patch.object(pacing_config_mod, "_CONFIG_PATH", bad), \
                    self.assertLogs(_PACING_LOGGER, level="ERROR"):
                self.assertIsNone(pacing_config_mod.load_pacing_config())
            # 合法但缺 count_ranges 键 → 同样降级
            bad.write_text('{"foo": 1}', encoding="utf-8")
            with mock.patch.object(pacing_config_mod, "_CONFIG_PATH", bad), \
                    self.assertLogs(_PACING_LOGGER, level="ERROR"):
                self.assertIsNone(pacing_config_mod.load_pacing_config())

    def test_config_matches_prompt_intervals(self) -> None:
        """DEC-005 单一事实源：观测配置区间与提示词 §5.1 区间表述一致。"""
        import json

        prompt = (
            Path(__file__).resolve().parents[2]
            / "evolution" / "harnesses" / "repo" / "prompts" / "storybuilding_system.md"
        ).read_text(encoding="utf-8")
        config = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "evolution" / "harnesses" / "repo" / "middleware"
                / "storyline_observation_config.json"
            ).read_text(encoding="utf-8")
        )
        for line_type, (lo, hi) in config["count_ranges"].items():
            self.assertIn(f"{line_type} | {lo}~{hi}", prompt)


# ── path_guard 白名单收紧（FR-008）─────────────────────────


class PathGuardWhitelistTest(unittest.TestCase):
    def test_harness_whitelist_is_six_entries(self) -> None:
        _load_real_harness()
        from harness_current.middleware.path_guard import WRITING_WRITE_PATTERNS

        patterns = {p.pattern for p in WRITING_WRITE_PATTERNS}
        self.assertEqual(patterns, {
            r"^/character/[^/]+\.md$",
            r"^/storyline\.md$",
            r"^/worldview\.md$",
            r"^/object/[^/]+\.md$",
            r"^/promises\.md$",
            r"^/review/[^/]+\.md$",
        })
        for forbidden in ("/outline.md", "/novel.md", "/chapter/x.md", "/detail/x.md", "/state_log.md", "/storyline/x.md", "/promises/x.md"):
            self.assertFalse(
                any(p.match(forbidden) for p in WRITING_WRITE_PATTERNS),
                f"{forbidden} 不应再被白名单放行",
            )


# ── 装配断言（FR-006 + 护栏挂载）───────────────────────────


class AssemblyTest(unittest.TestCase):
    def test_max_revisions_one_and_guard_mounted(self) -> None:
        """M2 清单架构：单次审查修订 + 契约/台账护栏挂载与 promises 写权限声明。"""
        import json

        pkg_dir = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
        manifest = json.loads(
            (pkg_dir / "architecture.json").read_text(encoding="utf-8")
        )
        story = next(a for a in manifest["agents"] if a["name"] == "storybuilding")
        review = next(a for a in manifest["agents"] if a["name"] == "storybuilding_review")

        self.assertEqual(story.get("max_revisions", 1), 1)
        self.assertIn("storyline_contract_guard", story["middleware"])
        self.assertIn("promises_contract_guard", story["middleware"])
        self.assertIn("/promises.md", story["write_permissions"])
        # review 子代理挂载节奏体检报告注入（FR-005）
        self.assertIn("pacing_report", review["middleware"])


if __name__ == "__main__":
    unittest.main()
