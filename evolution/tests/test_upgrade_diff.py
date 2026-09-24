"""upgrade_diff 测试（升级总览实时 diff，REQ-20260923-145931 FR-003）。

覆盖：
- resolve_base：same_code / root / ancestor / error 四态基线解析
- compute_agent_diffs：prompt 行级 hunks、skills 增删、middleware 增删改、无变化排除
- build_upgrade_diff：编排（账本条目 → 基线 → diff）、缓存命中、changes.agents 形状

要素快照构建（git 读取）打桩——diff 比较逻辑纯函数验证；
真实 git 链路由部署后 AC-004 与 git diff 抽查比对覆盖。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.versioning import upgrade_diff


def _ledger_entry(**overrides):
    entry = {
        "version": 14, "status": "production", "change_summary": "",
        "created_at": "2026-09-22", "commit": "c14",
        "same_code_as": None, "based_on": 10, "based_on_status": "resolved",
    }
    entry.update(overrides)
    return entry


class ResolveBaseTest(unittest.TestCase):
    def test_ancestor(self):
        kind, base_ver, same = upgrade_diff.resolve_base(_ledger_entry())
        self.assertEqual((kind, base_ver, same), ("ancestor", 10, None))

    def test_same_code(self):
        entry = _ledger_entry(version=9, commit="c8",
                              same_code_as=8, based_on=None, based_on_status="root")
        kind, base_ver, same = upgrade_diff.resolve_base(entry)
        self.assertEqual((kind, base_ver, same), ("same_code", None, 8))

    def test_root(self):
        entry = _ledger_entry(version=6, commit="c6",
                              same_code_as=None, based_on=None, based_on_status="root")
        kind, base_ver, same = upgrade_diff.resolve_base(entry)
        self.assertEqual((kind, base_ver, same), ("root", None, None))

    def test_error(self):
        entry = _ledger_entry(same_code_as=None, based_on=None, based_on_status="error")
        kind, base_ver, same = upgrade_diff.resolve_base(entry)
        self.assertEqual((kind, base_ver, same), ("error", None, None))


class ComputeAgentDiffsTest(unittest.TestCase):
    """打桩要素构建：两个 commit 的快照 → agent diff 聚合。"""

    def _run(self, prompts, skills, stacks):
        with patch.object(upgrade_diff, "_read_prompt", side_effect=lambda c, f: prompts[c][f]), \
             patch.object(upgrade_diff, "_build_skill_infos",
                          side_effect=lambda c: skills[c]), \
             patch.object(upgrade_diff, "_build_middleware_stacks",
                          side_effect=lambda c: stacks[c]):
            return upgrade_diff.compute_agent_diffs("base", "target")

    def test_prompt_line_diff(self):
        prompts = {
            "base": {"storybuilding_system.md": "l1\nl2\nl3", "storybuilding_review.md": "r"},
            "target": {"storybuilding_system.md": "l1\nl2\nl3\nl4", "storybuilding_review.md": "r"},
        }
        skills = {"base": [], "target": []}
        stacks = {"base": {}, "target": {}}
        diffs = self._run(prompts, skills, stacks)
        self.assertEqual(list(diffs), ["storybuilding"])
        prompt = diffs["storybuilding"]["prompt"]
        self.assertEqual(prompt["summary"], {"added": 1, "removed": 0})
        self.assertEqual(prompt["hunks"][-1], {"type": "insert", "lines": ["l4"]})

    def test_skills_added_removed(self):
        prompts = {
            "base": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
            "target": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
        }
        skills = {
            "base": [{"path": "skills/storybuilding/old"}, {"path": "skills/storybuilding/keep"}],
            "target": [{"path": "skills/storybuilding/keep"}, {"path": "skills/storybuilding/new"}],
        }
        stacks = {"base": {}, "target": {}}
        diffs = self._run(prompts, skills, stacks)
        skill_diff = diffs["storybuilding"]["skills"]
        self.assertEqual(skill_diff["added"], ["skills/storybuilding/new"])
        self.assertEqual(skill_diff["removed"], ["skills/storybuilding/old"])
        self.assertEqual(skill_diff["unchanged_count"], 1)

    def test_middleware_first_instance_pairing(self):
        """同 class 多次挂载：首实例（栈序在前）参与配对，末实例被忽略。"""
        def _mount(cls, params, group="agent", hooks=("before_model",)):
            return {"class_name": cls, "params": params, "group": group,
                    "hooks": list(hooks), "optional": False}

        prompts = {
            "base": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
            "target": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
        }
        skills = {"base": [], "target": []}
        stacks = {
            "base": {"storybuilding": [_mount("PacingMiddleware", {"max": 5})]},
            "target": {"storybuilding": [
                _mount("PacingMiddleware", {"max": 8}),   # 首实例（参与配对）
                _mount("PacingMiddleware", {"max": 99}),  # 末实例（忽略）
            ]},
        }
        diffs = self._run(prompts, skills, stacks)
        modified = next(c for c in diffs["storybuilding"]["processors"]
                        if c["change_type"] == "modified")
        self.assertEqual(modified["params_change"], {"old": {"max": 5}, "new": {"max": 8}})

    def test_middleware_added_removed_modified(self):
        def _mount(cls, params, group="agent", hooks=("before_model",)):
            return {"class_name": cls, "params": params, "group": group,
                    "hooks": list(hooks), "optional": False}

        prompts = {
            "base": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
            "target": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
        }
        skills = {"base": [], "target": []}
        stacks = {
            "base": {"storybuilding": [
                _mount("PacingMiddleware", {"max": 5}),
                _mount("GoneMiddleware", {}),
            ]},
            "target": {"storybuilding": [
                _mount("PacingMiddleware", {"max": 10}),
                _mount("FreshMiddleware", {}),
            ]},
        }
        diffs = self._run(prompts, skills, stacks)
        changes = {(c["change_type"], c["class_change"]["new"] or c["class_change"]["old"])
                   for c in diffs["storybuilding"]["processors"]}
        self.assertEqual(changes, {("modified", "PacingMiddleware"),
                                   ("removed", "GoneMiddleware"),
                                   ("added", "FreshMiddleware")})
        modified = next(c for c in diffs["storybuilding"]["processors"]
                        if c["change_type"] == "modified")
        self.assertEqual(modified["params_change"], {"old": {"max": 5}, "new": {"max": 10}})

    def test_no_change_excludes_agent(self):
        prompts = {
            "base": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
            "target": {"storybuilding_system.md": "p", "storybuilding_review.md": "r"},
        }
        skills = {"base": [], "target": []}
        stacks = {"base": {}, "target": {}}
        diffs = self._run(prompts, skills, stacks)
        self.assertEqual(diffs, {})


class BuildUpgradeDiffTest(unittest.TestCase):
    def setUp(self):
        upgrade_diff.clear_cache()

    def test_root_returns_empty_changes(self):
        entry = _ledger_entry(version=6, commit="c6",
                              same_code_as=None, based_on=None, based_on_status="root")
        with patch.object(upgrade_diff.platform_ledger, "get_version", return_value=entry):
            result = upgrade_diff.build_upgrade_diff(6)
        self.assertEqual(result["base_kind"], "root")
        self.assertEqual(result["changes"], {"agents": [], "intent": None})

    def test_same_code_returns_empty_changes(self):
        entry = _ledger_entry(version=9, commit="c8",
                              same_code_as=8, based_on=None, based_on_status="root")
        with patch.object(upgrade_diff.platform_ledger, "get_version", return_value=entry):
            result = upgrade_diff.build_upgrade_diff(9)
        self.assertEqual(result["base_kind"], "same_code")
        self.assertEqual(result["same_code_as"], 8)
        self.assertEqual(result["changes"]["agents"], [])

    def test_error_kind_no_diff(self):
        entry = _ledger_entry(same_code_as=None, based_on=None, based_on_status="error")
        with patch.object(upgrade_diff.platform_ledger, "get_version", return_value=entry):
            result = upgrade_diff.build_upgrade_diff(14)
        self.assertEqual(result["base_kind"], "error")
        self.assertEqual(result["changes"]["agents"], [])

    def test_ancestor_computes_and_caches(self):
        entry = _ledger_entry()
        calls = {"n": 0}

        def _fake_compute(base_commit, target_commit):
            calls["n"] += 1
            return {"storybuilding": {"prompt": None, "skills": None, "processors": []}}

        with patch.object(upgrade_diff.platform_ledger, "get_version", return_value=entry), \
             patch.object(upgrade_diff.platform_ledger, "resolve_commit",
                          side_effect=lambda v: {"10": "c10", "14": "c14"}.get(str(v))), \
             patch.object(upgrade_diff, "_require_readable_commit"), \
             patch.object(upgrade_diff, "compute_agent_diffs", side_effect=_fake_compute):
            first = upgrade_diff.build_upgrade_diff(14)
            second = upgrade_diff.build_upgrade_diff(14)
        self.assertEqual(first["base_kind"], "ancestor")
        self.assertEqual(first["base_version"], 10)
        self.assertEqual(first["base_commit"], "c10")
        self.assertEqual(
            first["changes"]["agents"],
            [{"agent": "storybuilding",
              "diff": {"prompt": None, "skills": None, "processors": []}}],
        )
        # commit 对不可变 → 同 (base, target) 命中缓存不重算
        self.assertEqual(calls["n"], 1)
        self.assertEqual(second, first)

    def test_unreadable_commit_degrades_to_error_and_not_cached(self):
        """git 侧 commit 不可读 → base_kind=error + 不写缓存（FR-003 失败语义）。"""
        entry = _ledger_entry()
        with patch.object(upgrade_diff.platform_ledger, "get_version", return_value=entry), \
             patch.object(upgrade_diff.platform_ledger, "resolve_commit",
                          side_effect=lambda v: {"10": "c10", "14": "c14"}.get(str(v))), \
             patch.object(upgrade_diff, "_require_readable_commit",
                          side_effect=RuntimeError("git cat-file 失败")), \
             patch.object(upgrade_diff, "compute_agent_diffs") as mock_compute:
            result = upgrade_diff.build_upgrade_diff(14)
        self.assertEqual(result["base_kind"], "error")
        self.assertEqual(result["changes"]["agents"], [])
        mock_compute.assert_not_called()
        self.assertEqual(upgrade_diff._diff_cache, {})

    def test_compute_failure_degrades_to_error_and_not_cached(self):
        """compute 过程异常（含 git 读取失败）→ error 占位 + 不写缓存。"""
        entry = _ledger_entry()
        with patch.object(upgrade_diff.platform_ledger, "get_version", return_value=entry), \
             patch.object(upgrade_diff.platform_ledger, "resolve_commit",
                          side_effect=lambda v: {"10": "c10", "14": "c14"}.get(str(v))), \
             patch.object(upgrade_diff, "_require_readable_commit"), \
             patch.object(upgrade_diff, "compute_agent_diffs",
                          side_effect=RuntimeError("git show 失败")):
            result = upgrade_diff.build_upgrade_diff(14)
        self.assertEqual(result["base_kind"], "error")
        self.assertEqual(result["changes"]["agents"], [])
        self.assertEqual(upgrade_diff._diff_cache, {})


if __name__ == "__main__":
    unittest.main()
