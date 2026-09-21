"""评测报告下钻后端测试（REQ-20260921-114943）。

覆盖：
- 批次行明细查询 list_batch_runs / GET runs（FR-001/FR-004 / AC-001、AC-004）
- 三件套交付索引 GET deliveries（FR-002 / AC-002，DEC-002/003/009）
- 运行观测产物分组 list_trace_artifact_revisions（FR-003 / AC-003，DEC-001）
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BenchmarkDetailTestBase(unittest.TestCase):
    """临时 DB fixture（与 test_benchmark_v3 同模式）。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        os.environ["EVOLUTION_DB"] = str(Path(self._tmpdir) / "test.db")
        os.environ["EXECUTOR_WORKSPACE"] = self._tmpdir
        import importlib
        import sqlite3

        import app.core.settings as settings_mod

        importlib.reload(settings_mod)
        import app.core.db as db

        conn = sqlite3.connect(os.environ["EVOLUTION_DB"], check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db._conn = conn
        db.init_db()
        self.db = db

    def tearDown(self) -> None:
        try:
            self.db.get_conn().close()
        except Exception:
            pass
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── 造数助手 ──

    def _make_scores(self, overall: float = 3.5) -> dict:
        return {
            "rubric_version": "v3",
            "scores": {"structure": 4, "consistency": 3},
            "tags": {"consistency": ["设定前后矛盾"]},
            "reasons": {"structure": "结构完整", "consistency": "局部不一致"},
            "overall": overall,
            "rule_delivery": {"key": "delivery_complete", "passed": True, "problems": []},
        }

    def _make_batch_with_rows(self) -> tuple[str, dict[str, dict]]:
        """2 case × 2 seed 的批次，覆盖 done/failed/pending/evaluating 四态。"""
        from app.benchmark import repo

        batch_id = repo.create_batch(
            case_ids=["case-001", "case-002"], versions=[7],
            golden_revision="rev-x", seeds=2, rubric_version="v3", judge_fp="jfp",
        )
        rows = self.db.query_all(
            "SELECT * FROM benchmark_runs WHERE batch_id=? ORDER BY case_id, seed",
            (batch_id,),
        )
        # case-001 seed1 → done（有评分、有 trace）
        repo.set_trace(rows[0]["id"], "trace-done")
        repo.set_result(
            rows[0]["id"], eval_id=None,
            scores_json=json.dumps(self._make_scores(3.5), ensure_ascii=False),
        )
        # case-001 seed2 → failed（评分失败，trace 已回填，error 保留）。
        # 复放真实重试序列：每次失败前 worker 重新抢占为 running。
        repo.set_trace(rows[1]["id"], "trace-scored-failed")
        for _ in range(repo.MAX_RETRIES):
            self.db.execute(
                "UPDATE benchmark_runs SET status='running' WHERE id=?", (rows[1]["id"],))
            repo.mark_failed(rows[1]["id"], "评分重试用尽（2 次）: boom")
        # case-002 seed1 → pending（未动）
        # case-002 seed2 → evaluating（有 trace 未出分）
        repo.set_trace(rows[3]["id"], "trace-running")
        return batch_id, {f"{r['case_id']}#{r['seed']}": r for r in rows}


# ── FR-001 / FR-004：批次行明细 ───────────────────────────────


class ListBatchRunsTest(BenchmarkDetailTestBase):
    def test_mixed_statuses_and_parsed_scores(self):
        """四态行齐全返回；done 行解析出完整 scores；无分行 scores=None。"""
        from app.benchmark import repo

        batch_id, _ = self._make_batch_with_rows()
        result = repo.list_batch_runs(batch_id)

        self.assertEqual(result["total"], 4)
        items = result["items"]
        self.assertEqual([i["case_id"] for i in items],
                         ["case-001", "case-001", "case-002", "case-002"])
        by_key = {f"{i['case_id']}#{i['seed']}": i for i in items}

        done = by_key["case-001#1"]
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["trace_id"], "trace-done")
        self.assertEqual(done["scores"]["overall"], 3.5)
        self.assertEqual(done["scores"]["rule_delivery"]["passed"], True)
        self.assertIn("structure", done["scores"]["reasons"])

        failed = by_key["case-001#2"]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["trace_id"], "trace-scored-failed")
        self.assertIn("评分重试用尽", failed["error"])
        self.assertIsNone(failed["scores"])

        pending = by_key["case-002#1"]
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["trace_id"])
        self.assertIsNone(pending["scores"])

        evaluating = by_key["case-002#2"]
        self.assertEqual(evaluating["status"], "evaluating")

    def test_corrupt_scores_json_degrades_to_none(self):
        """scores_json 解析失败的行按无评分处理，不阻塞其他行（FR-001 失败语义）。"""
        from app.benchmark import repo

        batch_id, rows = self._make_batch_with_rows()
        self.db.execute(
            "UPDATE benchmark_runs SET scores_json='{broken' WHERE id=?",
            (rows["case-001#1"]["id"],),
        )
        result = repo.list_batch_runs(batch_id)
        by_key = {f"{i['case_id']}#{i['seed']}": i for i in result["items"]}
        self.assertIsNone(by_key["case-001#1"]["scores"])
        self.assertEqual(result["total"], 4)

    def test_unknown_batch_returns_empty(self):
        from app.benchmark import repo

        result = repo.list_batch_runs("no-such-batch")
        self.assertEqual(result["total"], 0)


class ListBatchRunsApiTest(BenchmarkDetailTestBase):
    def test_endpoint_shape_and_404(self):
        from app.benchmark import api as bench_api

        batch_id, _ = self._make_batch_with_rows()
        resp = bench_api.list_batch_runs(batch_id)
        self.assertEqual(resp["batch_id"], batch_id)
        self.assertEqual(resp["total"], 4)
        self.assertEqual(len(resp["items"]), 4)
        self.assertIn("scores", resp["items"][0])
        self.assertIn("error", resp["items"][0])
        self.assertIn("trace_id", resp["items"][0])

        with self.assertRaises(Exception):
            bench_api.list_batch_runs("no-such-batch")


# ── FR-002：三件套交付索引 ───────────────────────────────────


class DeliveryIndexTest(BenchmarkDetailTestBase):
    def _insert_run_row(self, trace_id: str) -> None:
        self.db.execute(
            """INSERT INTO runs (trace_id, workspace_id, status, ingested_at)
               VALUES (?, ?, 'completed', '2026-09-21T00:00:00Z')""",
            (trace_id, f"ws-{trace_id}"),
        )

    def _insert_artifact_event(
        self, trace_id: str, seq: int, key: str, content: str, revision_id: str,
    ) -> None:
        import hashlib
        import uuid

        event = {
            "trace_id": trace_id,
            "event_id": uuid.uuid4().hex,
            "sequence": seq,
            "type": "artifact_revision",
            "status": "completed",
            "timestamp": "2026-09-21T00:00:00Z",
            "source": "middleware",
            "artifact_revision_id": revision_id,
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

    def _register_revision(
        self, trace_id: str, key: str, revision_id: str, content: str,
        expires_at: str | None,
    ) -> None:
        """落 artifacts/artifact_revisions/payload_objects（模拟 importer 投影）。"""
        import hashlib

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        payload_id = f"pl-{revision_id[:12]}"
        artifact_id = f"art-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
        self.db.execute(
            """INSERT INTO payload_objects
               (payload_id, content_hash, kind, size_bytes, sensitivity, expires_at,
                storage_path, created_at)
               VALUES (?, ?, 'semantic_full', ?, 'normal', ?, '', '2026-09-21T00:00:00Z')""",
            (payload_id, content_hash, len(content), expires_at),
        )
        self.db.execute(
            "INSERT OR IGNORE INTO artifacts (artifact_id, artifact_type, workspace_id, logical_key, created_at)"
            " VALUES (?, 'workspace_file', ?, ?, '2026-09-21T00:00:00Z')",
            (artifact_id, f"ws-{trace_id}", key),
        )
        self.db.execute(
            """INSERT INTO artifact_revisions
               (artifact_revision_id, artifact_id, payload_id, content_hash,
                producer_trace_id, created_at)
               VALUES (?, ?, ?, ?, ?, '2026-09-21T00:00:00Z')""",
            (revision_id, artifact_id, payload_id, content_hash, trace_id),
        )

    def test_index_latest_per_key_grouped_and_available(self):
        """同 key 多次写入取最新修订（与评分口径一致）；元数据连接可用性。"""
        from app.benchmark.scorer import load_outline_delivery_index

        trace_id = "tr-deliv-1"
        self._insert_run_row(trace_id)
        filler = "正文" * 150
        self._insert_artifact_event(trace_id, 1, "/storyline.md", "旧版" + filler, "rev-old")
        self._insert_artifact_event(trace_id, 2, "/storyline.md", "新版" + filler, "rev-new")
        self._insert_artifact_event(trace_id, 3, "/character/main.md", "人物" + filler, "rev-char")
        # 世界观无事件 → 空组
        self._register_revision(trace_id, "/storyline.md", "rev-new", "新版" + filler,
                                "2999-01-01T00:00:00+00:00")
        self._register_revision(trace_id, "/character/main.md", "rev-char", "人物" + filler,
                                "2000-01-01T00:00:00+00:00")

        groups = load_outline_delivery_index(trace_id)
        self.assertEqual([g["display"] for g in groups],
                         ["主线 storyline", "人物 character", "世界观 worldview"])

        storyline = groups[0]
        self.assertEqual(len(storyline["files"]), 1)
        f = storyline["files"][0]
        self.assertEqual(f["artifact_revision_id"], "rev-new")  # 最新修订，非 rev-old
        self.assertTrue(f["available"])                          # 未过期

        character = groups[1]
        self.assertFalse(character["files"][0]["available"])     # 已过期

        self.assertEqual(groups[2]["files"], [])                 # 缺失组为空

    def test_index_unregistered_revision_marks_unavailable(self):
        """事件有修订但表内无投影（异常态）：available=False，不抛错。"""
        from app.benchmark.scorer import load_outline_delivery_index

        trace_id = "tr-deliv-2"
        self._insert_run_row(trace_id)
        self._insert_artifact_event(trace_id, 1, "/storyline.md", "主线" + "正文" * 150, "rev-x1")
        groups = load_outline_delivery_index(trace_id)
        self.assertEqual(groups[0]["files"][0]["artifact_revision_id"], "rev-x1")
        self.assertFalse(groups[0]["files"][0]["available"])


class DeliveryApiTest(BenchmarkDetailTestBase):
    def _fake_request(self, is_super_admin: bool):
        from types import SimpleNamespace

        return SimpleNamespace(state=SimpleNamespace(is_super_admin=is_super_admin))

    def test_endpoint_returns_groups_and_permission_flag(self):
        from app.benchmark import api as bench_api

        trace_id = "tr-deliv-api"
        self.db.execute(
            "INSERT INTO runs (trace_id, workspace_id, status, ingested_at)"
            " VALUES (?, 'ws', 'completed', '2026-09-21T00:00:00Z')", (trace_id,))
        resp = bench_api.get_trace_deliveries(trace_id, self._fake_request(True))
        self.assertEqual(resp["trace_id"], trace_id)
        self.assertTrue(resp["can_read_content"])
        self.assertEqual(len(resp["groups"]), 3)

        resp2 = bench_api.get_trace_deliveries(trace_id, self._fake_request(False))
        self.assertFalse(resp2["can_read_content"])

    def test_endpoint_unknown_trace_404(self):
        from fastapi import HTTPException

        from app.benchmark import api as bench_api

        with self.assertRaises(HTTPException) as ctx:
            bench_api.get_trace_deliveries("no-such-trace", self._fake_request(True))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
