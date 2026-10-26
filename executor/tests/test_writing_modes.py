"""创作双模式契约与路由测试（REQ-20261026-191719 FR-001/003/008/011）。

跑法（在 executor 目录）：python -m pytest tests/test_writing_modes.py -v

覆盖：
  - 段级模式解析：缺省=interactive（FR-011 存量迁移）；题材/总纲段锁死
    interactive（DEC-002）；volume/chapter/text 可 auto（FR-001）
  - 模式写入校验：非法段/非法值拒绝（FR-008）
  - 正文批次默认 N=5（FR-003）
  - 路由 directive 在 auto 段携带批次大小事实（FR-003/FR-002 纪律由装配层注入）
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from contracts.writing_artifacts import (
    STAGE_CHAPTER,
    STAGE_OUTLINE,
    STAGE_TEXT,
    STAGE_THEME,
    STAGE_VOLUME,
    load_writing_state,
    save_writing_state,
    set_stage_mode,
    stage_mode,
    text_batch_size,
)

AUTO = "auto"
INTERACTIVE = "interactive"


class TestStageModes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_mode_is_interactive(self):
        """FR-011：缺省与存量书一律逐步（无字段=interactive）。"""
        self.assertEqual(stage_mode(self.ws), INTERACTIVE)

    def test_theme_outline_locked_interactive(self):
        """DEC-002：题材/总纲段锁死逐步，写 auto 报错且不落盘。"""
        for stage in (STAGE_THEME, STAGE_OUTLINE):
            with self.assertRaises(ValueError):
                set_stage_mode(self.ws, stage, AUTO)
            self.assertEqual(stage_mode(self.ws, stage), INTERACTIVE)

    def test_auto_allowed_on_three_stages(self):
        """FR-001：卷纲/章纲/正文可开自动，各段独立记忆（FR-008）。"""
        for stage in (STAGE_VOLUME, STAGE_CHAPTER, STAGE_TEXT):
            set_stage_mode(self.ws, stage, AUTO)
        self.assertEqual(stage_mode(self.ws, STAGE_VOLUME), AUTO)
        self.assertEqual(stage_mode(self.ws, STAGE_CHAPTER), AUTO)
        self.assertEqual(stage_mode(self.ws, STAGE_TEXT), AUTO)
        # 未设置的段不受影响（每段可调）
        self.assertEqual(stage_mode(self.ws, STAGE_OUTLINE), INTERACTIVE)

    def test_invalid_mode_value_rejected(self):
        with self.assertRaises(ValueError):
            set_stage_mode(self.ws, STAGE_VOLUME, "yolo")

    def test_invalid_stage_rejected(self):
        with self.assertRaises(ValueError):
            set_stage_mode(self.ws, "unknown", AUTO)

    def test_mode_persists_in_state(self):
        set_stage_mode(self.ws, STAGE_VOLUME, AUTO)
        state = load_writing_state(self.ws)
        self.assertEqual(state.stage_modes.get(STAGE_VOLUME), AUTO)
        self.assertEqual(stage_mode(self.ws, STAGE_VOLUME), AUTO)


class TestTextBatchSize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_batch_size_is_five(self):
        """FR-003：首批默认 5 章。"""
        self.assertEqual(text_batch_size(self.ws), 5)

    def test_batch_size_set_and_clamp(self):
        state = load_writing_state(self.ws)
        state.text_batch_size = 8
        save_writing_state(self.ws, state)
        self.assertEqual(text_batch_size(self.ws), 8)


class TestRouteDirectiveFacts(unittest.TestCase):
    """auto 段的 directive 携带运行时事实（批次大小/模式），不携带行为纪律。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_text_stage(self):
        """最小产物树推到正文段：主题/总纲/卷纲定稿 + 章纲已出。"""
        from contracts.writing_artifacts import (
            save_writing_state,
            scan_writing_progress,
        )
        # 直接钉人工指针到正文段（不依赖产物树形态，路由读指针）
        state = load_writing_state(self.ws)
        state.confirmed_stage = STAGE_TEXT
        state.stage = STAGE_TEXT
        save_writing_state(self.ws, state)
        return scan_writing_progress(self.ws)

    def test_auto_text_directive_carries_mode_and_batch(self):
        from app.domains.writing.pipeline import route

        self._seed_text_stage()
        set_stage_mode(self.ws, STAGE_TEXT, AUTO)
        decision = route(self.ws, "t1", user_text="继续")
        self.assertEqual(decision.phase, STAGE_TEXT)
        self.assertIn("自动模式", decision.directive)
        self.assertIn("5", decision.directive)  # 批次大小事实

    def test_interactive_directive_has_no_auto_marker(self):
        from app.domains.writing.pipeline import route

        self._seed_text_stage()
        decision = route(self.ws, "t1", user_text="继续")
        self.assertNotIn("自动模式", decision.directive)


class TestAutoAnswerDecision(unittest.TestCase):
    """FR-007/DEC-010：违纪 choice 代答，review 停等点永不代答。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_choice_in_auto_stage_is_answered(self):
        from app.domains.writing.pipeline import should_auto_answer

        set_stage_mode(self.ws, STAGE_VOLUME, AUTO)
        iv = {"kind": "choice", "question": "选 A 还是 B?", "options": []}
        self.assertTrue(should_auto_answer(iv, self.ws, STAGE_VOLUME))

    def test_review_never_answered_even_in_auto(self):
        from app.domains.writing.pipeline import should_auto_answer

        set_stage_mode(self.ws, STAGE_TEXT, AUTO)
        for rk in ("batch", "style", "stage"):
            iv = {"kind": "review", "review_kind": rk, "question": "交审"}
            self.assertFalse(should_auto_answer(iv, self.ws, STAGE_TEXT))

    def test_choice_in_interactive_not_answered(self):
        from app.domains.writing.pipeline import should_auto_answer

        iv = {"kind": "choice", "question": "选 A 还是 B?"}
        self.assertFalse(should_auto_answer(iv, self.ws, STAGE_VOLUME))

    def test_mode_flip_to_interactive_stops_auto_answer(self):
        """DEC-016：运行中切回逐步，代答判定实时翻转（下一停等边界弹卡）。"""
        from app.domains.writing.pipeline import should_auto_answer

        set_stage_mode(self.ws, STAGE_TEXT, AUTO)
        iv = {"kind": "choice", "question": "?"}
        self.assertTrue(should_auto_answer(iv, self.ws, STAGE_TEXT))
        set_stage_mode(self.ws, STAGE_TEXT, INTERACTIVE)
        self.assertFalse(should_auto_answer(iv, self.ws, STAGE_TEXT))


class TestAutoModeAssets(unittest.TestCase):
    """harness 包自动模式资产（DEC-004 软纪律 + TD-003 request_review）。"""

    def _load_pkg(self):
        from app.platform.agent.loader import load_package

        return load_package(_HARNESS)

    def test_auto_mode_prompt_exists_and_overrides_discipline(self):
        pkg = self._load_pkg()
        text = pkg.auto_mode_prompt()
        self.assertTrue(text)
        self.assertIn("严禁调用 `confirm_with_user`", text)
        self.assertIn("request_review", text)

    def test_request_review_tool_payload(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "request_review_under_test", _HARNESS / "tools" / "request_review.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        payload = mod.build_review_payload("batch", "第 1-5 章已写完", [])
        self.assertEqual(payload["kind"], "review")
        self.assertEqual(payload["review_kind"], "batch")

    def test_manifest_mounts_request_review(self):
        manifest = json.loads(
            (_HARNESS / "architecture.json").read_text(encoding="utf-8")
        )
        author = next(a for a in manifest["agents"] if a["name"] == "author")
        self.assertIn("request_review", author["tools"])

    def test_stage_prompt_chainable_with_auto(self):
        pkg = self._load_pkg()
        stage_text = pkg.stage_prompt(STAGE_TEXT)
        self.assertTrue(stage_text)
        # 装配层拼接形态：段规程在前，auto 纪律追加在后
        combined = f"{stage_text}\n\n{pkg.auto_mode_prompt()}"
        self.assertLess(combined.index(stage_text), combined.index("自动模式"))


_HARNESS = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"


if __name__ == "__main__":
    unittest.main()
