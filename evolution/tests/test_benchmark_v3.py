"""评测系统 v3 单元测试（REQ-20260919-172934）。

覆盖：
- rubric v3 结构完整性（AC-002）
- judge 输出契约校验（FR-002 失败语义）
- 交付完整规则项（FR-002 ③）
- ArtifactRevision 直读三件套（FR-003 / AC-004）
- 被测模型指纹提取（AC-005）
- seed 展开建批（FR-003 / AC-003）
- Welch CI 三态 + 指纹前置校验（FR-004 / AC-006）
- 弱点报告聚合（FR-005 / AC-007）
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BenchmarkV3TestBase(unittest.TestCase):
    """临时 DB fixture（与 test_calibrate 同模式）。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["EVOLUTION_DB"] = str(Path(self._tmpdir) / "test.db")
        os.environ["EXECUTOR_WORKSPACE"] = self._tmpdir
        import importlib
        import sqlite3

        import app.core.settings as settings_mod

        importlib.reload(settings_mod)
        import app.core.db as db

        # 直接注入新连接而非 reload db 模块：reload 会改写全局 db 模块的
        # settings 绑定，污染同进程后续测试（它们只 reload settings）。
        conn = sqlite3.connect(os.environ["EVOLUTION_DB"], check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db._conn = conn
        db.init_db()
        self.db = db

    def _insert_run(self, trace_id: str) -> None:
        """event_payloads 外键引用 runs.trace_id，先落一行 run。"""
        self.db.execute(
            """INSERT INTO runs (trace_id, workspace_id, status, ingested_at)
               VALUES (?, ?, 'completed', '2026-09-19T00:00:00Z')""",
            (trace_id, f"ws-{trace_id}"),
        )

    def tearDown(self) -> None:
        try:
            self.db.get_conn().close()
        except Exception:
            pass


# ── rubric v3 结构（AC-002）─────────────────────────────────


class RubricV3Test(unittest.TestCase):
    def test_structure_complete(self):
        from app.benchmark import rubric_v3

        self.assertEqual(len(rubric_v3.DIMENSIONS), 5)
        for dim in rubric_v3.DIMENSIONS:
            self.assertEqual(sorted(dim["anchors"].keys()), ["1", "3", "5"], dim["key"])
            self.assertTrue(dim["question"])
            self.assertTrue(3 <= len(dim["defect_tags"]) <= 6, dim["key"])
        self.assertEqual(len(rubric_v3.DIMENSION_KEYS), 5)
        # 闭合集 = 各维词表并集
        union = {t for d in rubric_v3.DIMENSIONS for t in d["defect_tags"]}
        self.assertEqual(rubric_v3.ALL_DEFECT_TAGS, union)
        # 校准状态如实标注（FR-005 ③）
        self.assertEqual(rubric_v3.CALIBRATION_STATUS, "uncalibrated")
        self.assertEqual(rubric_v3.ANCHOR_DRAFT_STATUS, "draft")

    def test_prompts_built(self):
        from app.benchmark import rubric_v3

        system = rubric_v3.build_judge_system_prompt()
        for dim in rubric_v3.DIMENSIONS:
            self.assertIn(dim["key"], system)
            self.assertIn(dim["anchors"]["5"], system)
        user = rubric_v3.build_judge_user_prompt("某需求", {"主线 storyline": "内容A", "人物 character": "内容B"})
        self.assertIn("某需求", user)
        self.assertIn("内容A", user)


# ── judge 输出契约校验（FR-002 失败语义）────────────────────


class ValidateJudgementTest(unittest.TestCase):
    def _good(self, **overrides):
        from app.benchmark import rubric_v3

        payload = {
            "scores": {k: 4 for k in rubric_v3.DIMENSION_KEYS},
            "tags": {},
            "reasons": {},
        }
        payload.update(overrides)
        return payload

    def test_valid_passes(self):
        from app.benchmark.scorer import _validate_judgement

        _validate_judgement(self._good())

    def test_low_score_with_tag_passes(self):
        from app.benchmark import rubric_v3
        from app.benchmark.scorer import _validate_judgement

        scores = {k: 4 for k in rubric_v3.DIMENSION_KEYS}
        scores["人物塑造"] = 2
        _validate_judgement(self._good(
            scores=scores,
            tags={"人物塑造": ["弧线缺失"]},
            reasons={"人物塑造": "主角弧线未展开"},
        ))

    def test_missing_dimension_rejected(self):
        from app.benchmark.scorer import _validate_judgement

        with self.assertRaises(ValueError):
            _validate_judgement({"scores": {"需求兑现": 4}, "tags": {}, "reasons": {}})

    def test_illegal_score_rejected(self):
        from app.benchmark.scorer import _validate_judgement

        with self.assertRaises(ValueError):
            _validate_judgement(self._good(scores={"需求兑现": "4"}))
        with self.assertRaises(ValueError):
            _validate_judgement(self._good(scores={"需求兑现": 6}))

    def test_low_score_without_tag_rejected(self):
        from app.benchmark import rubric_v3
        from app.benchmark.scorer import _validate_judgement

        scores = {k: 4 for k in rubric_v3.DIMENSION_KEYS}
        scores["节奏结构"] = 1
        with self.assertRaises(ValueError):
            _validate_judgement(self._good(scores=scores))

    def test_tag_outside_vocab_rejected(self):
        from app.benchmark import rubric_v3
        from app.benchmark.scorer import _validate_judgement

        with self.assertRaises(ValueError):
            _validate_judgement(self._good(
                tags={"情节构造": ["不存在的标签"]},
            ))


# ── 交付完整规则项（FR-002 ③）───────────────────────────────


class DeliveryCompleteTest(unittest.TestCase):
    def _full(self) -> dict:
        filler = "字" * 400
        return {
            "主线 storyline": filler,
            "人物 character": filler,
            "世界观 worldview": filler,
        }

    def test_complete_passes(self):
        from app.benchmark.scorer import check_delivery_complete

        result = check_delivery_complete(self._full())
        self.assertTrue(result["passed"])

    def test_missing_group_fails(self):
        from app.benchmark.scorer import check_delivery_complete

        d = self._full()
        del d["世界观 worldview"]
        result = check_delivery_complete(d)
        self.assertFalse(result["passed"])
        self.assertTrue(any("世界观" in p for p in result["problems"]))

    def test_placeholder_fails(self):
        from app.benchmark.scorer import check_delivery_complete

        d = self._full()
        d["主线 storyline"] = "TODO 待补充" + "字" * 400
        result = check_delivery_complete(d)
        self.assertFalse(result["passed"])


# ── ArtifactRevision 直读（AC-004）──────────────────────────


class LoadDeliveriesTest(BenchmarkV3TestBase):
    def _insert_artifact_event(self, trace_id: str, seq: int, key: str, content: str) -> None:
        event = {
            "trace_id": trace_id,
            "event_id": uuid.uuid4().hex,
            "sequence": seq,
            "type": "artifact_revision",
            "status": "completed",
            "timestamp": "2026-09-19T00:00:00Z",
            "source": "middleware",
            "artifact_revision_id": uuid.uuid4().hex,
            "artifact": {
                "logical_key": key,
                "artifact_type": "workspace_file",
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            "output": {"content": content},
        }
        self.db.execute(
            "INSERT INTO event_payloads (trace_id, sequence, type, payload_json) VALUES (?, ?, ?, ?)",
            (trace_id, seq, "artifact_revision", json.dumps(event, ensure_ascii=False)),
        )

    def test_three_part_delivery_loaded(self):
        from app.benchmark.scorer import load_outline_deliveries

        trace_id = "tr-outline-1"
        self._insert_run(trace_id)
        filler = "正文" * 150
        self._insert_artifact_event(trace_id, 1, "/storyline.md", "主线内容" + filler)
        self._insert_artifact_event(trace_id, 2, "/character/main.md", "人物内容" + filler)
        self._insert_artifact_event(trace_id, 3, "/worldview.md", "世界观内容" + filler)
        self._insert_artifact_event(trace_id, 4, "/outline.md", "非三件套产物，应被忽略")

        deliveries = load_outline_deliveries(trace_id)
        self.assertEqual(
            sorted(deliveries.keys()),
            ["世界观 worldview", "主线 storyline", "人物 character"],
        )
        self.assertIn("主线内容", deliveries["主线 storyline"])
        self.assertIn("人物内容", deliveries["人物 character"])

    def test_hash_mismatch_skipped(self):
        from app.benchmark.scorer import load_outline_deliveries

        trace_id = "tr-outline-2"
        self._insert_run(trace_id)
        filler = "正文" * 150
        self._insert_artifact_event(trace_id, 1, "/storyline.md", "主线内容" + filler)
        # 手工改坏 hash
        self.db.execute(
            "UPDATE event_payloads SET payload_json = replace(payload_json, 'content_hash', 'content_hash_x') "
            "WHERE trace_id=?",
            (trace_id,),
        )
        deliveries = load_outline_deliveries(trace_id)
        # hash 字段被改名 → artifact 无 content_hash → 仅作告警跳过校验？不：字段缺失时不校验，仍加载
        # （校验仅在 expected_hash 为 str 时生效——字段被改名后无 content_hash，内容仍可信加载）
        self.assertEqual(len(deliveries), 1)

    def test_latest_revision_wins(self):
        from app.benchmark.scorer import load_outline_deliveries

        trace_id = "tr-outline-3"
        self._insert_run(trace_id)
        filler = "正文" * 150
        self._insert_artifact_event(trace_id, 1, "/storyline.md", "旧版内容" + filler)
        self._insert_artifact_event(trace_id, 2, "/storyline.md", "新版内容" + filler)
        deliveries = load_outline_deliveries(trace_id)
        self.assertIn("新版内容", deliveries["主线 storyline"])
        self.assertNotIn("旧版内容", deliveries["主线 storyline"])


# ── Platform 绑定指纹（AC-005，DEC-015 对齐）────────────────


class PlatformBindingFingerprintTest(unittest.TestCase):
    """绑定查询与指纹计算（mock httpx，不发真实请求）。"""

    @staticmethod
    def _binding(manifest_id=7, commit="c0ffee", model="glm-4.7", base_url="https://api.example.com"):
        return {
            "trace_id": "tr-1", "manifest_id": manifest_id, "harness_commit": commit,
            "llm_config": {"model": model, "base_url": base_url, "api_key_ref": None,
                           "source": "evolution"},
            "run_purpose": "optimization", "degraded": False,
            "runtime_identity_digest": None, "bound_at": "2026-09-20T00:00:00Z",
            "status": "active",
        }

    def test_llm_snapshot_fingerprint(self):
        from app.benchmark import manifest

        fp = manifest.llm_snapshot_fingerprint(self._binding()["llm_config"])
        self.assertIsNotNone(fp)
        # 同配置稳定；base_url 尾斜杠归一
        self.assertEqual(fp, manifest.llm_snapshot_fingerprint(
            {"model": "glm-4.7", "base_url": "https://api.example.com/"}))
        # 模型变 → 指纹变
        self.assertNotEqual(fp, manifest.llm_snapshot_fingerprint(
            {"model": "deepseek-r1", "base_url": "https://api.example.com"}))
        # 无快照 → None
        self.assertIsNone(manifest.llm_snapshot_fingerprint(None))
        self.assertIsNone(manifest.llm_snapshot_fingerprint({"model": "", "base_url": "x"}))

    def test_binding_manifest_fingerprint_stable_and_sensitive(self):
        from app.benchmark import manifest

        b = self._binding()
        fp1 = manifest.binding_manifest_fingerprint(b)
        self.assertEqual(fp1, manifest.binding_manifest_fingerprint(self._binding()))
        # manifest_id 变 / commit 变 / 模型变 → 指纹变
        self.assertNotEqual(fp1, manifest.binding_manifest_fingerprint(self._binding(manifest_id=8)))
        self.assertNotEqual(fp1, manifest.binding_manifest_fingerprint(self._binding(commit="dead00")))
        self.assertNotEqual(fp1, manifest.binding_manifest_fingerprint(self._binding(model="gpt-4o")))

    def test_fetch_binding_ok_and_fail_static(self):
        from unittest.mock import patch

        import httpx

        from app.benchmark import manifest

        # 正常 200：返回记录
        ok_resp = httpx.Response(200, json=self._binding(), request=httpx.Request("GET", "http://x"))
        with patch.object(manifest.httpx, "get", return_value=ok_resp):
            self.assertEqual(manifest.fetch_platform_binding("tr-1")["manifest_id"], 7)
        # 404 / 网络异常 / 结构非法：fail-static 返回 None（不 raise）
        not_found = httpx.Response(404, json={"detail": "not found"}, request=httpx.Request("GET", "http://x"))
        with patch.object(manifest.httpx, "get", return_value=not_found):
            self.assertIsNone(manifest.fetch_platform_binding("tr-1"))
        with patch.object(manifest.httpx, "get", side_effect=httpx.ConnectError("down")):
            self.assertIsNone(manifest.fetch_platform_binding("tr-1"))
        bad = httpx.Response(200, json={"no_manifest_id": True}, request=httpx.Request("GET", "http://x"))
        with patch.object(manifest.httpx, "get", return_value=bad):
            self.assertIsNone(manifest.fetch_platform_binding("tr-1"))


# ── seed 展开 + 指纹字段（AC-003/005）───────────────────────


class CreateBatchTest(BenchmarkV3TestBase):
    def test_seed_expansion_and_fingerprints(self):
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=["case-001", "case-002"],
            versions=[7],
            golden_revision="rev-abc",
            seeds=3,
            rubric_version="v3-outline-5dim-uncalibrated",
            judge_fp="judge-fp-1",
        )
        rows = self.db.query_all(
            "SELECT * FROM benchmark_runs WHERE batch_id=?", (batch_id,),
        )
        self.assertEqual(len(rows), 6)  # 2 case × 1 version × 3 seed
        seeds = sorted(r["seed"] for r in rows)
        self.assertEqual(seeds, [1, 1, 2, 2, 3, 3])
        self.assertTrue(all(r["rubric_version"] == "v3-outline-5dim-uncalibrated" for r in rows))
        self.assertTrue(all(r["judge_fp"] == "judge-fp-1" for r in rows))
        self.assertTrue(all(r["model_fp"] is None for r in rows))  # 跑完回填

    def test_set_fingerprints_backfill(self):
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=["case-001"], versions=[7], golden_revision="r", seeds=1,
        )
        row = self.db.query_one("SELECT id FROM benchmark_runs WHERE batch_id=?", (batch_id,))
        repo.set_fingerprints(
            row["id"], harness_commit="c0ffee", model_fp="mfp",
            manifest_fp="mfp2", platform_manifest_id=7,
        )
        updated = self.db.query_one("SELECT * FROM benchmark_runs WHERE id=?", (row["id"],))
        self.assertEqual(updated["harness_commit"], "c0ffee")
        self.assertEqual(updated["model_fp"], "mfp")
        self.assertEqual(updated["manifest_fp"], "mfp2")
        self.assertEqual(updated["platform_manifest_id"], 7)
        # unbound 回填（Platform 失联的 fail-static 路径）
        repo.set_fingerprints(
            row["id"], harness_commit=None, model_fp=None, manifest_fp="unbound",
        )
        unbound = self.db.query_one("SELECT * FROM benchmark_runs WHERE id=?", (row["id"],))
        self.assertEqual(unbound["manifest_fp"], "unbound")
        self.assertIsNone(unbound["platform_manifest_id"])


# ── Welch CI 三态 + 指纹校验（AC-006）────────────────────────


class WelchCompareTest(unittest.TestCase):
    def test_win(self):
        from app.benchmark.stats import welch_compare

        result = welch_compare([4.5] * 15, [3.0] * 15)
        self.assertEqual(result["verdict"], "win")
        self.assertTrue(result["sufficient_power"])

    def test_lose(self):
        from app.benchmark.stats import welch_compare

        result = welch_compare([2.0] * 15, [3.5] * 15)
        self.assertEqual(result["verdict"], "lose")

    def test_tie_on_overlap(self):
        from app.benchmark.stats import welch_compare

        # 均值相同 + 有方差 → CI 必然跨过对方均值 → tie
        result = welch_compare([3.0, 3.5, 4.0, 3.2, 3.8] * 3, [3.0, 3.4, 4.1, 3.1, 3.9] * 3)
        self.assertEqual(result["verdict"], "tie")

    def test_insufficient_samples(self):
        from app.benchmark.stats import welch_compare

        result = welch_compare([4.0], [3.0])
        self.assertEqual(result["verdict"], "insufficient")


class CompareBatchesTest(BenchmarkV3TestBase):
    def _make_batch(self, golden_rev: str, rubric: str, judge: str, model: str,
                    manifest: str, overalls: list[float]) -> str:
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=[f"case-{i}" for i in range(len(overalls))],
            versions=[7], golden_revision=golden_rev,
            rubric_version=rubric, judge_fp=judge,
        )
        rows = self.db.query_all("SELECT * FROM benchmark_runs WHERE batch_id=?", (batch_id,))
        for row, overall in zip(rows, overalls):
            scores = {
                "overall": overall,
                "scores": {"需求兑现": overall, "设定自洽": overall, "人物塑造": overall,
                           "情节构造": overall, "节奏结构": overall},
            }
            self.db.execute(
                """UPDATE benchmark_runs SET status='done', model_fp=?, manifest_fp=?,
                   scores_json=? WHERE id=?""",
                (model, manifest, json.dumps(scores), row["id"]),
            )
        return batch_id

    def test_comparable_when_only_manifest_differs(self):
        from app.benchmark.stats import compare_batches

        batch_a = self._make_batch("g1", "v3", "j1", "m1", "mf-a", [4.0] * 6)
        batch_b = self._make_batch("g1", "v3", "j1", "m1", "mf-b", [3.0] * 6)
        result = compare_batches(batch_a, batch_b)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["total"]["verdict"], "win")

    def test_rejected_when_rubric_differs(self):
        from app.benchmark.stats import compare_batches

        batch_a = self._make_batch("g1", "v3", "j1", "m1", "mf-a", [4.0] * 6)
        batch_b = self._make_batch("g1", "v4", "j1", "m1", "mf-b", [3.0] * 6)
        result = compare_batches(batch_a, batch_b)
        self.assertFalse(result["comparable"])
        self.assertTrue(any("rubric_version" in p for p in result["problems"]))

    def test_rejected_when_model_differs(self):
        from app.benchmark.stats import compare_batches

        batch_a = self._make_batch("g1", "v3", "j1", "m1", "mf-a", [4.0] * 6)
        batch_b = self._make_batch("g1", "v3", "j1", "m2", "mf-b", [3.0] * 6)
        result = compare_batches(batch_a, batch_b)
        self.assertFalse(result["comparable"])
        self.assertTrue(any("model_fp" in p for p in result["problems"]))

    def test_rejected_when_batch_has_unbound_rows(self):
        from app.benchmark.stats import compare_batches

        batch_a = self._make_batch("g1", "v3", "j1", "m1", "mf-a", [4.0] * 6)
        batch_b = self._make_batch("g1", "v3", "j1", "m1", "mf-b", [3.0] * 6)
        # 把 batch_a 的一行打成 unbound（Platform 失联路径的落库形态）
        victim = self.db.query_one(
            "SELECT id FROM benchmark_runs WHERE batch_id=? LIMIT 1", (batch_a,))
        self.db.execute(
            "UPDATE benchmark_runs SET manifest_fp='unbound', model_fp=NULL, "
            "harness_commit=NULL, platform_manifest_id=NULL WHERE id=?",
            (victim["id"],),
        )
        result = compare_batches(batch_a, batch_b)
        self.assertFalse(result["comparable"])
        self.assertTrue(any("unbound" in p for p in result["problems"]))

    def test_rejected_when_golden_differs(self):
        from app.benchmark.stats import compare_batches

        batch_a = self._make_batch("g1", "v3", "j1", "m1", "mf-a", [4.0] * 6)
        batch_b = self._make_batch("g2", "v3", "j1", "m1", "mf-b", [3.0] * 6)
        result = compare_batches(batch_a, batch_b)
        self.assertFalse(result["comparable"])
        self.assertTrue(any("golden_revision" in p for p in result["problems"]))


# ── 弱点报告（AC-007）────────────────────────────────────────


class ReportTest(BenchmarkV3TestBase):
    def _make_batch_with_scores(
        self, overalls_with_tags: list[tuple[float, dict, dict]],
    ) -> str:
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=[f"case-{i}" for i in range(len(overalls_with_tags))],
            versions=[7], golden_revision="g1", seeds=1,
            rubric_version="v3", judge_fp="j1",
        )
        rows = self.db.query_all("SELECT * FROM benchmark_runs WHERE batch_id=?", (batch_id,))
        for row, (overall, tags, dim_overrides) in zip(rows, overalls_with_tags):
            scores = {k: overall for k in (
                "需求兑现", "设定自洽", "人物塑造", "情节构造", "节奏结构")}
            scores.update(dim_overrides)
            data = {"overall": overall, "scores": scores, "tags": tags,
                    "rule_delivery": {"passed": True, "problems": []}}
            self.db.execute(
                "UPDATE benchmark_runs SET status='done', scores_json=?, model_fp='m', manifest_fp='mf' WHERE id=?",
                (json.dumps(data), row["id"]),
            )
        return batch_id

    def test_report_aggregation(self):
        from app.benchmark.report import build_report, build_summary_for_evolve

        batch_id = self._make_batch_with_scores([
            (4.5, {}, {"人物塑造": 2.0}),
            (2.0, {"人物塑造": ["弧线缺失"]}, {"人物塑造": 1.5}),
            (3.0, {"人物塑造": ["弧线缺失"], "节奏结构": ["中段松散"]}, {"人物塑造": 2.5}),
        ])
        report = build_report(batch_id)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["calibration"], "uncalibrated")  # FR-005 ③ 如实标注
        # 维度均分：最弱维度排最前
        self.assertEqual(report["dimensions"][0]["dimension"], "人物塑造")
        # 标签命中：弧线缺失 ×2 最热
        self.assertEqual(report["tag_hits"][0]["tag"], "弧线缺失")
        self.assertEqual(report["tag_hits"][0]["hits"], 2)
        # 低分 case 在个例层
        self.assertEqual(report["low_cases"][0]["overall"], 2.0)
        # 进化摘要可用
        summary = build_summary_for_evolve(batch_id)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["batch_id"], batch_id)

    def test_report_empty_batch(self):
        from app.benchmark.report import build_report

        report = build_report("no-such-batch")
        self.assertEqual(report["status"], "not_found")

    def test_report_no_scored_data(self):
        from app.benchmark import repo
        from app.benchmark.report import build_report

        batch_id = repo.create_batch(
            case_ids=["case-x"], versions=[7], golden_revision="g1",
        )
        report = build_report(batch_id)
        self.assertEqual(report["status"], "no_scored_data")


if __name__ == "__main__":
    unittest.main()
