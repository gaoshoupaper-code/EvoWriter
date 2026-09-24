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
    """打桩要素构建：两侧 commit 快照 → agent diff 聚合（布局探测 + 并集对比）。"""

    V7_SPECS, _V7_LAYOUT = (
        [
            ("storybuilding", "story_expert", ("prompts/storybuilding_system.md",)),
            ("storybuilding_review", "reviewer", ("prompts/storybuilding_review.md",)),
        ],
        "v7",
    )
    V14_SPECS, _V14_LAYOUT = (
        [
            ("orchestrator", "orchestrator", ("prompts/v14/orchestrator_system.md",)),
            ("worldview", "domain",
             ("prompts/v14/common_rules.md", "prompts/v14/domain_worldview.md")),
            ("character", "domain",
             ("prompts/v14/common_rules.md", "prompts/v14/domain_character.md")),
            ("storyline", "domain",
             ("prompts/v14/common_rules.md", "prompts/v14/domain_storyline.md")),
            ("storybuilding_review", "reviewer", ("prompts/storybuilding_review.md",)),
        ],
        "v14",
    )

    def _run(self, prompts, skills, stacks,
             base_specs=None, target_specs=None):
        specs = {
            "base": base_specs or (self.V7_SPECS, self._V7_LAYOUT),
            "target": target_specs or (self.V7_SPECS, self._V7_LAYOUT),
        }
        with patch.object(upgrade_diff, "_agent_specs_for_commit",
                          side_effect=lambda c: specs[c]), \
             patch.object(upgrade_diff, "read_prompt_body",
                          side_effect=lambda c, files: "\n\n---\n\n".join(
                              prompts[c][f] for f in files)), \
             patch.object(upgrade_diff, "_build_skill_infos",
                          side_effect=lambda c: skills[c]), \
             patch.object(upgrade_diff, "_build_middleware_stacks",
                          side_effect=lambda c: stacks[c]):
            return upgrade_diff.compute_agent_diffs("base", "target")

    def test_prompt_line_diff(self):
        prompts = {
            "base": {"prompts/storybuilding_system.md": "l1\nl2\nl3",
                     "prompts/storybuilding_review.md": "r"},
            "target": {"prompts/storybuilding_system.md": "l1\nl2\nl3\nl4",
                       "prompts/storybuilding_review.md": "r"},
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
            "base": {"prompts/storybuilding_system.md": "p",
                     "prompts/storybuilding_review.md": "r"},
            "target": {"prompts/storybuilding_system.md": "p",
                       "prompts/storybuilding_review.md": "r"},
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
            "base": {"prompts/storybuilding_system.md": "p",
                     "prompts/storybuilding_review.md": "r"},
            "target": {"prompts/storybuilding_system.md": "p",
                       "prompts/storybuilding_review.md": "r"},
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
            "base": {"prompts/storybuilding_system.md": "p",
                     "prompts/storybuilding_review.md": "r"},
            "target": {"prompts/storybuilding_system.md": "p",
                       "prompts/storybuilding_review.md": "r"},
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
            "base": {"prompts/storybuilding_system.md": "p",
                     "prompts/storybuilding_review.md": "r"},
            "target": {"prompts/storybuilding_system.md": "p",
                       "prompts/storybuilding_review.md": "r"},
        }
        skills = {"base": [], "target": []}
        stacks = {"base": {}, "target": {}}
        diffs = self._run(prompts, skills, stacks)
        self.assertEqual(diffs, {})

    def test_cross_layout_whole_agent_added_removed(self):
        """v7 → v14 跨架构：旧专家整体退役、新 orchestrator/领域整体登场。"""
        prompts = {
            "base": {"prompts/storybuilding_system.md": "S\nS2",
                     "prompts/storybuilding_review.md": "r",
                     "prompts/v14/orchestrator_system.md": "ORCH",
                     "prompts/v14/common_rules.md": "C",
                     "prompts/v14/domain_worldview.md": "W",
                     "prompts/v14/domain_character.md": "CH",
                     "prompts/v14/domain_storyline.md": "SL"},
            "target": {"prompts/storybuilding_system.md": "S\nS2",
                       "prompts/storybuilding_review.md": "r",
                       "prompts/v14/orchestrator_system.md": "ORCH",
                       "prompts/v14/common_rules.md": "C",
                       "prompts/v14/domain_worldview.md": "W",
                       "prompts/v14/domain_character.md": "CH",
                       "prompts/v14/domain_storyline.md": "SL"},
        }
        skills = {"base": [{"path": "skills/storybuilding/draft"}],
                  "target": [{"path": "skills/v14/worldview-build"}]}
        stacks = {"base": {"storybuilding": []}, "target": {"orchestrator": []}}

        diffs = self._run(prompts, skills, stacks, target_specs=(self.V14_SPECS, self._V14_LAYOUT))

        # 旧专家整体退役（whole_agent + prompt 全删）
        self.assertEqual(diffs["storybuilding"]["whole_agent"], "removed")
        self.assertEqual(diffs["storybuilding"]["prompt"]["summary"], {"added": 0, "removed": 2})
        # 新架构 agent 整体登场（prompt 全插入）
        for name in ("orchestrator", "worldview", "character", "storyline"):
            self.assertEqual(diffs[name]["whole_agent"], "added", name)
        self.assertEqual(diffs["orchestrator"]["prompt"]["summary"], {"added": 1, "removed": 0})
        # 领域 prompt 是拼接体（"C\n\n---\n\nW" → 5 行全插入）
        self.assertEqual(diffs["worldview"]["prompt"]["summary"], {"added": 5, "removed": 0})
        # reviewer 两代都在 → 无 whole_agent，无变化则不出现
        self.assertNotIn("storybuilding_review", diffs)


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
