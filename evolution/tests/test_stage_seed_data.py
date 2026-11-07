"""Built-in trial inputs and scoring revisions remain complete and immutable."""
from __future__ import annotations

from collections import Counter
import json
import sqlite3
import sys
import unittest
from pathlib import Path

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
        self.assertEqual(len({tuple(dimension["anchors"].values()) for dimension in dimensions}), 13)
        self.assertTrue(all(set(dimension["anchors"]) == {"1", "2", "3", "4", "5"} for dimension in dimensions))
        chapter = next(rule for rule in current if rule["task_type"] == "chapter_outlines")
        self.assertEqual([dimension["key"] for dimension in chapter["dimensions"]], ["卷纲兑现", "状态连续", "章内节奏", "可直接执笔"])
        self.assertTrue(all("四卷" not in json.dumps(rule, ensure_ascii=False) for rule in current))
        edited = rubrics.save_rule(RuleCreate.model_validate({key: chapter[key] for key in ("task_type", "name", "dimensions", "discipline_rules")}))
        self.assertEqual(edited["profile"], "stage-eval-v2")
        self.assertGreater(edited["version"], chapter["version"])
        self.assertEqual(repo.get_rule(chapter["id"]), chapter)


if __name__ == "__main__":
    unittest.main()
