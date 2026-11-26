"""Built-in trial inputs and scoring revisions remain complete and immutable."""
from __future__ import annotations

from collections import Counter
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BuiltinPackageTest(unittest.TestCase):
    def test_download_templates_are_complete_editable_new_targets(self):
        from app.experiments.packages import templates, validate_package
        from app.experiments.schemas import PackageCreate
        examples = templates()
        self.assertEqual(len(examples), 3)
        for example in examples:
            self.assertNotIn("input_revision", example["input"])
            example["input"]["demand_md"] += "\n保持固定目标，细化具体动作。"
            validate_package(PackageCreate.model_validate(example))
            if example["target"]["task_type"] == "volume_outlines":
                self.assertEqual(example["target"]["chapters_per_volume"], 20)
            else:
                self.assertIsInstance(example["target"]["chapter"], int)

    def test_twelve_independent_inputs_have_the_confirmed_distribution(self):
        from app.experiments.seed_data import SUITE, get_builtin_packages
        from app.experiments.packages import validate_package
        from app.experiments.schemas import PackageCreate

        records = get_builtin_packages()
        self.assertEqual(len(records), 12)
        self.assertEqual(len({record["id"] for record in records}), 12)
        self.assertEqual(len({record["input_revision"] for record in records}), 12)
        counts = Counter((record["task_type"], record["purpose"]) for record in records)
        for kind in ("volume_outlines", "chapter_outlines", "chapter_text"):
            self.assertEqual([counts[kind, purpose] for purpose in ("optimization", "validation", "regression")], [2, 1, 1])
        for record in records:
            self.assertEqual(record["suite"], SUITE)
            payload = {key: record[key] for key in ("title", "purpose", "target", "input")}
            validate_package(PackageCreate.model_validate(payload))

    def test_targets_and_nonfirst_chapter_context_are_frozen(self):
        from app.experiments.seed_data import get_builtin_packages

        records = get_builtin_packages()
        for record in records:
            target, files = record["target"], record["input"]["files"]
            if record["task_type"] == "volume_outlines":
                self.assertEqual(target["volume_ids"], [1, 2, 3])
                self.assertEqual(target["chapters_per_volume"], 20)
                continue
            chapter = target["chapter"]
            self.assertEqual(len(target["volume_ids"]), 1)
            if chapter > 1:
                self.assertIn(f"章纲/第{chapter - 1:03d}章.md", files)
                self.assertIn(f"正文/第{chapter - 1:03d}章.md", files)
                self.assertIn("章末钩子", files[f"章纲/第{chapter - 1:03d}章.md"])
            self.assertEqual(f"章纲/第{chapter:03d}章.md" in files, record["task_type"] == "chapter_text")
            self.assertTrue(all(skill["content"].strip() for skill in record["input"]["skills"]))
        again = get_builtin_packages()
        self.assertEqual([item["input_revision"] for item in records], [item["input_revision"] for item in again])
        records[4]["input"]["files"]["主题.md"] = "caller mutation"
        self.assertNotEqual(get_builtin_packages()[4]["input"]["files"]["主题.md"], "caller mutation")


class SeedDatabaseTest(unittest.TestCase):
    def setUp(self):
        from app.core import db
        self.db = db
        self.previous = db._conn
        db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        db._conn.row_factory = sqlite3.Row
        for table in ("self_evolution_packages", "self_evolution_rules"):
            db._conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)")

    def tearDown(self):
        self.db._conn.close()
        self.db._conn = self.previous

    def test_repeated_seeding_never_overwrites_an_existing_package(self):
        from app.experiments import repo
        from app.experiments.seed_data import seed_packages

        first = seed_packages()
        preserved = repo.get_package(first[0]["id"])
        preserved["title"] = "已有记录必须保留"
        self.db.execute("UPDATE self_evolution_packages SET payload=? WHERE id=?", (json.dumps(preserved), preserved["id"]))
        second = seed_packages()
        self.assertEqual(len(repo.list_packages()), 12)
        self.assertEqual(next(item for item in second if item["id"] == preserved["id"])["title"], preserved["title"])

    def test_new_rules_preserve_legacy_rows_and_use_dimension_specific_anchors(self):
        from app.experiments import repo, rubrics
        from app.experiments.schemas import RuleCreate

        legacy = {"id": "legacy-rule", "task_type": "chapter_outlines", "version": 1, "created_at": repo.now(),
                  "name": "旧四卷规则", "dimensions": [], "discipline_rules": []}
        repo.insert("self_evolution_rules", legacy)
        rubrics.seed_rules()
        rubrics.seed_rules()
        self.assertEqual(repo.get_rule(legacy["id"]), legacy)
        current = [item for item in rubrics.list_rules() if item.get("profile") == "stage-eval-v2"]
        self.assertEqual(len(current), 3)
        dimensions = [dimension for rule in current for dimension in rule["dimensions"]]
        self.assertEqual(len(dimensions), 13)
        self.assertTrue(all("focus_points" not in dimension for dimension in dimensions))
        self.assertEqual(len({tuple(dimension["anchors"].values()) for dimension in dimensions}), 13)
        self.assertTrue(all(set(dimension["anchors"]) == {"1", "2", "3", "4", "5"} for dimension in dimensions))
        chapter = next(rule for rule in current if rule["task_type"] == "chapter_outlines")
        self.assertEqual([dimension["key"] for dimension in chapter["dimensions"]], ["卷纲兑现", "状态连续", "章内节奏", "可直接执笔"])
        self.assertTrue(all("四卷" not in json.dumps(rule, ensure_ascii=False) for rule in current))
        edited = rubrics.save_rule(RuleCreate.model_validate({key: chapter[key] for key in ("task_type", "name", "dimensions", "discipline_rules")}))
        self.assertEqual(edited["profile"], "stage-eval-v2")
        self.assertGreater(edited["version"], chapter["version"])
        self.assertEqual(repo.get_rule(chapter["id"]), chapter)

    @patch.dict("os.environ", {"WRITING_AGENT_ID": "text"})
    def test_chapter_rule_seeds_current_revision_without_changing_old_rule(self):
        from pydantic import ValidationError

        from app.experiments import chapter_rubric, repo, rubrics
        from app.experiments.schemas import RuleCreate

        old = {"id": "old-text-rule", "task_type": "chapter_text", "version": 3, "created_at": repo.now(),
               "profile": "chapter-text-outline-v2", "flow_version": 3, "name": "旧单章正文规则",
               "dimensions": [], "discipline_rules": []}
        repo.insert("self_evolution_rules", old)

        rubrics.seed_rules()
        rubrics.seed_rules()

        current, previous = rubrics.list_rules("chapter_text")
        self.assertEqual(previous, old)
        self.assertEqual(current["profile"], "chapter-text-outline-v3")
        self.assertEqual(current["version"], 4)
        dimensions = current["dimensions"]
        self.assertEqual([d["key"] for d in dimensions], [
            "事件因果逻辑", "人物行为逻辑", "人物对话", "叙事节奏与张力", "场景空间",
            "叙事视角与沉浸感", "行文清晰度", "语言质感与去 AI 味", "章纲兑现",
        ])
        self.assertEqual({dimension["key"]: dimension["focus_points"] for dimension in dimensions},
                         chapter_rubric.FOCUS_POINTS)
        self.assertEqual({d["weight"] for d in dimensions}, {1})
        self.assertTrue(all(set(d["anchors"]) == {"1", "2", "3", "4", "5"} for d in dimensions))
        self.assertEqual(len({tuple(d["anchors"].values()) for d in dimensions}), 9)

        payload = {key: current[key] for key in ("task_type", "name", "dimensions", "discipline_rules")}
        edited = rubrics.save_rule(RuleCreate.model_validate(payload))
        self.assertEqual(edited["profile"], current["profile"])
        self.assertEqual(repo.get_rule(current["id"]), current)
        with self.assertRaises(ValidationError):
            RuleCreate.model_validate({**payload, "dimensions": dimensions[:-1]})
        with self.assertRaisesRegex(ValidationError, "等权"):
            RuleCreate.model_validate({**payload, "dimensions": [{**dimensions[0], "weight": 2}, *dimensions[1:]]})

    @patch.dict("os.environ", {"WRITING_AGENT_ID": "text"})
    def test_chapter_rule_seeds_nine_defaults_alongside_custom_historical_revisions(self):
        from app.experiments import chapter_rubric, repo, rubrics

        shared_keys = ["事件因果逻辑", "人物行为逻辑", "章纲兑现"]
        historical_keys = [
            [*shared_keys, "语言清晰度"],
            [*shared_keys, "语言清晰度", "人物语言", "场景空间"],
            [*shared_keys, "行文清晰度", "场景空间"],
        ]
        historical = []
        for version, keys in enumerate(historical_keys, 9):
            rule = {"id": f"custom-{len(keys)}-dimensions", "task_type": "chapter_text", "version": version,
                    "profile": rubrics.TEXT_PROFILE, "flow_version": 3, "created_at": repo.now(),
                    "name": "自定义正文规则", "discipline_rules": ["定制纪律。"],
                    "dimensions": [{"key": key, "question": f"定制判定问题：{key}", "weight": weight,
                                    "focus_points": f"定制关注点：{key}",
                                    "anchors": {str(level): f"定制 {level} 分档位。" for level in range(1, 6)}}
                                   for weight, key in enumerate(keys, 1)]}
            repo.insert("self_evolution_rules", rule)
            historical.append(rule)

        rubrics.seed_rules()
        rubrics.seed_rules()

        current, *previous = rubrics.list_rules("chapter_text")
        self.assertEqual(previous, list(reversed(historical)))
        self.assertTrue(all(not rubrics.is_current_text_rule(rule) for rule in previous))
        self.assertTrue(rubrics.is_current_text_rule(current))
        self.assertEqual(current["version"], 12)
        self.assertEqual(current["discipline_rules"], chapter_rubric.DISCIPLINE_RULES)
        self.assertEqual(current["dimensions"], [
            {"key": key, "question": question, "weight": 1, "focus_points": chapter_rubric.FOCUS_POINTS[key],
             "anchors": {str(level): anchor for level, anchor in enumerate(anchors, 1)}}
            for key, question, anchors in chapter_rubric.DIMENSIONS
        ])

    @patch.dict("os.environ", {"WRITING_AGENT_ID": "text"})
    def test_focus_upgrade_copies_latest_revision_once_and_preserves_explicit_edits(self):
        from app.experiments import chapter_rubric, repo, rubrics
        from app.experiments.schemas import RuleCreate

        dimensions = [{"key": key, "question": question, "weight": 1,
                       "anchors": {str(level): anchor for level, anchor in enumerate(anchors, 1)}}
                      for key, question, anchors in chapter_rubric.DIMENSIONS]
        first = rubrics.save_rule(RuleCreate.model_validate({
            "task_type": "chapter_text", "name": "旧规则", "discipline_rules": ["遵守旧纪律。"],
            "dimensions": dimensions,
        }))
        dimensions[0].update(question="定制判定问题", focus_points="先查条件。\n再查结果。")
        dimensions[0]["anchors"]["4"] = "定制 4 分档位。"
        dimensions[1]["focus_points"] = ""
        before = rubrics.save_rule(RuleCreate.model_validate({
            "task_type": "chapter_text", "name": "自定义评分规则", "discipline_rules": ["定制纪律。"],
            "dimensions": dimensions,
        }))

        rubrics.seed_rules()
        rubrics.seed_rules()
        current, previous, oldest = rubrics.list_rules("chapter_text")
        self.assertEqual((previous, oldest), (before, first))
        self.assertNotEqual(current["id"], before["id"])
        self.assertEqual(current["version"], before["version"] + 1)
        for key in ("name", "profile", "discipline_rules"):
            self.assertEqual(current[key], before[key])
        self.assertEqual(before["dimensions"][0], current["dimensions"][0])
        self.assertEqual(before["dimensions"][1], current["dimensions"][1])
        for previous_dimension, current_dimension in zip(before["dimensions"][2:], current["dimensions"][2:]):
            self.assertEqual(current_dimension, {**previous_dimension,
                "focus_points": chapter_rubric.FOCUS_POINTS[previous_dimension["key"]]})

        payload = {key: current[key] for key in ("task_type", "profile", "name", "dimensions", "discipline_rules")}
        payload["dimensions"] = [{**dimension, "focus_points": "  \n "} for dimension in current["dimensions"]]
        edited = rubrics.save_rule(RuleCreate.model_validate(payload))
        rubrics.seed_rules()
        self.assertEqual([item["id"] for item in rubrics.list_rules("chapter_text")],
                         [edited["id"], current["id"], before["id"], first["id"]])
        self.assertTrue(all(dimension["focus_points"] == "" for dimension in edited["dimensions"]))
        self.assertEqual(repo.get_rule(current["id"]), current)


if __name__ == "__main__":
    unittest.main()
