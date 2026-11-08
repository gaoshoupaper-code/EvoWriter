"""REQ-20261108-191146: retire legacy starts and clean only proven legacy ownership.

Run in an exclusive maintenance container after pausing evolution;
keep executor running, then restore evolution after cleanup.
The reviewed manifest is also the retry journal; keep it until cleanup completes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from app.core import db
from app.core.settings import settings

RETIRED_MESSAGE = "旧版批次评测已退役，请使用评测工作台的三阶段实验。"
ACTIVE_ROWS = {"pending", "running", "evaluating"}
TERMINAL_TASKS = {"done", "failed", "cancelled", "evidence_capture_failed"}


class BenchmarkRetired(RuntimeError):
    pass


def reject_legacy_start() -> None:
    raise BenchmarkRetired(RETIRED_MESSAGE)


def _tables() -> dict[str, set[str]]:
    names = db.query_all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return {row["name"]: {column["name"] for column in db.query_all(f'PRAGMA table_info("{row["name"]}")')} for row in names}


def _experiment_traces(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"trace_ids", "evaluation_trace_ids"} and isinstance(item, list):
                found.update(identifier for identifier in item if isinstance(identifier, str))
            elif key in {"trace_id", "self_trace_id"} and isinstance(item, str):
                found.add(item)
            found.update(_experiment_traces(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_experiment_traces(item))
    return found


def _protected_traces(candidates: set[str]) -> dict[str, str]:
    protected: dict[str, str] = {}
    tables = _tables()
    revisions = db.query_all("SELECT artifact_revision_id,producer_trace_id,source_trace_id,parent_revision_id FROM artifact_revisions")
    owners = {("trace", identifier): {identifier} for identifier in candidates}
    for row in revisions:
        if row["producer_trace_id"] in candidates:
            owners[("artifact_revision", row["artifact_revision_id"])] = {row["producer_trace_id"]}
    if "self_evolution_runs" in tables:
        for row in db.query_all("SELECT id,payload FROM self_evolution_runs"):
            for identifier in _experiment_traces(json.loads(row["payload"])):
                protected[identifier] = "当前实验记录引用（包括旧流程实验）"
    owned_tables = {"benchmark_runs", "runs", "nodes", "event_payloads", "trace_flags", "evaluation_scores",
                    "trace_payload_links", "trace_receipts", "trace_ingestion_conflicts", "artifact_revisions"}
    for table, columns in tables.items():
        if table in owned_tables:
            continue
        reference_columns = {column for column in columns if column in {"trace_id", "baseline_trace", "candidate_trace"}
                             or column.endswith("_trace_id") or column.endswith("_revision_id")}
        if table == "release_events_v2":
            reference_columns.add("candidate_id")
        for column in reference_columns:
            for row in db.query_all(f'SELECT DISTINCT "{column}" AS id FROM "{table}" WHERE "{column}" IS NOT NULL'):
                kind = "artifact_revision" if column.endswith("_revision_id") or column == "candidate_id" else "trace"
                for identifier in owners.get((kind, row["id"]), set()):
                    protected[identifier] = f"仍被 {table}.{column} 引用"
    for row in db.query_all("SELECT trace_id,run_purpose,owner_user_id,external_refs_json FROM runs"):
        refs = json.loads(row.get("external_refs_json") or "{}")
        if refs.get("experiment_id") or refs.get("experiment_run_id"):
            protected[row["trace_id"]] = "运行明确关联当前实验"
        elif row["run_purpose"] == "user_generation" or row["owner_user_id"] not in {None, "", "unknown", "ab-evolve"}:
            protected[row["trace_id"]] = "用户运行或用户归属未获删除授权"
    for row in db.query_all("SELECT target_type,target_id FROM outcome_records UNION ALL SELECT target_type,target_id FROM score_records"):
        kind = "trace" if row["target_type"] == "run" else row["target_type"]
        for identifier in owners.get((kind, row["target_id"]), set()):
            protected[identifier] = "仍被不可变结果或评分记录引用"
    edges = db.query_all("SELECT from_type,from_id,to_type,to_id FROM lineage_edges")
    # Revision dependencies are logical references even when no lineage edge was stored.
    for row in revisions:
        if row["parent_revision_id"]:
            edges.append({"from_type": "artifact_revision", "from_id": row["artifact_revision_id"],
                          "to_type": "artifact_revision", "to_id": row["parent_revision_id"]})
        if row["source_trace_id"]:
            edges.append({"from_type": "artifact_revision", "from_id": row["artifact_revision_id"],
                          "to_type": "trace", "to_id": row["source_trace_id"]})
    # An old trace's own produces edge is internal; a surviving consumer is not.
    changed = True
    while changed:
        changed = False
        remaining = candidates - protected.keys()
        for row in edges:
            left = owners.get((row["from_type"], row["from_id"]), set())
            right = owners.get((row["to_type"], row["to_id"]), set())
            for ours, opposite in ((left, right), (right, left)):
                if not opposite or not opposite.issubset(remaining):
                    for identifier in ours.intersection(remaining):
                        protected[identifier] = "仍存在其他有效对象的血缘引用"
                        changed = True
    return protected


def _digest(document: dict) -> str:
    body = {key: value for key, value in document.items() if key != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _database_path() -> str:
    return str(Path(next(row["file"] for row in db.query_all("PRAGMA database_list") if row["name"] == "main")).resolve())


def build_manifest() -> dict:
    """Read-only inventory: no stop requests, row changes, or file deletions."""
    rows = db.query_all("SELECT id,batch_id,trace_id,status FROM benchmark_runs ORDER BY id")
    candidates = {row["trace_id"] for row in rows if row["trace_id"]}
    protected = _protected_traces(candidates)
    retained = [{"kind": "trace", "id": identifier, "reason": reason} for identifier, reason in protected.items() if identifier in candidates]
    for row in db.query_all("SELECT trace_id,run_purpose,external_refs_json FROM runs ORDER BY trace_id"):
        purpose = row["run_purpose"] or ""
        refs = json.loads(row["external_refs_json"] or "{}")
        if row["trace_id"] not in candidates and refs.get("benchmark_batch_id") in {item["batch_id"] for item in rows}:
            retained.append({"kind": "trace", "id": row["trace_id"], "reason": "旧批次仅为会话上下文引用，保留正常会话及运行证据", "run_purpose": purpose})
        elif row["trace_id"] not in candidates and row["trace_id"] not in protected and (purpose.startswith("evolution") or purpose in {"optimization", "evaluation", "benchmark"}):
            reason = "旧批次仅为会话上下文引用，不能证明属于旧批次执行" if refs.get("benchmark_batch_id") else "没有可靠旧批次关联；不能按运行标签删除"
            retained.append({"kind": "trace", "id": row["trace_id"], "reason": reason, "run_purpose": purpose})
    payloads: dict[str, list[str]] = {}
    for identifier in sorted(candidates):
        values = db.query_all("SELECT payload_id FROM trace_payload_links WHERE trace_id=? UNION SELECT payload_id FROM artifact_revisions WHERE producer_trace_id=?", (identifier, identifier))
        payloads[identifier] = sorted(row["payload_id"] for row in values)
        retained.append({"kind": "executor_workspace", "id": identifier, "reason": "执行端目录未有可验证独占路径；不按临时目录前缀删除"})
        retained.append({"kind": "executor_trace_evidence", "id": identifier, "reason": "执行端原始 Trace、WAL 与索引副本未有可验证独占路径及安全删除接口；仅删除 evolution 观测副本，不宣称两端全清"})
    document = {"version": 1, "database": _database_path(), "created_at": datetime.now(UTC).isoformat(),
                "batch_ids": sorted({row["batch_id"] for row in rows}), "trace_ids": sorted(candidates - protected.keys()),
                "rows": rows, "payloads": payloads, "retained": retained}
    document["digest"] = _digest(document)
    return document


def confirm_stopped(rows: list[dict]) -> tuple[bool, str]:
    """Confirm actual executor termination; cancelled DB rows alone are not proof."""
    from app.benchmark import runner
    batches = {row["batch_id"] for row in rows}
    deadline = time.monotonic() + 25
    while True:
        with runner._cancel_lock:
            local_workers = batches.intersection(runner._cancel_events)
        if not local_workers:
            break
        if time.monotonic() >= deadline:
            return False, "旧批次工作线程尚未退出"
        time.sleep(.2)
    candidates = {row["trace_id"] for row in rows if row["trace_id"]}
    protected = _protected_traces(candidates)
    if any(row["status"] in {"running", "evaluating"} and (not row["trace_id"] or row["trace_id"] in protected) for row in rows):
        return False, "运行中旧行缺少独占 Trace 关联；无法确认停止，保留历史"
    base = settings.executor_url.rstrip("/")
    try:
        for identifier in candidates - protected.keys():
            run = db.query_one("SELECT status,external_refs_json FROM runs WHERE trace_id=?", (identifier,))
            refs = json.loads(run["external_refs_json"] or "{}") if run else {}
            task_id = refs.get("task_id")
            if task_id:
                path = base + "/internal/ab/status/" + quote(str(task_id), safe="")
                response = httpx.get(path, timeout=5)
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                state = response.json().get("status")
                if state not in TERMINAL_TASKS:
                    stopped = httpx.post(base + "/internal/ab/stop/" + quote(str(task_id), safe=""), timeout=5)
                    if stopped.status_code not in {200, 202, 409}:
                        stopped.raise_for_status()
                    while time.monotonic() < deadline:
                        response = httpx.get(path, timeout=5)
                        if response.status_code == 404:
                            break
                        response.raise_for_status()
                        if response.json().get("status") in TERMINAL_TASKS:
                            break
                        time.sleep(.5)
                    else:
                        return False, f"执行任务 {task_id} 停止尚未确认"
            elif run and run["status"] in {"running", "pending", "recovering", "stopping"}:
                return False, f"Trace {identifier} 没有可验证停止的执行任务标识"
        response = httpx.get(base + "/internal/active-runs", timeout=5)
        response.raise_for_status()
        active = response.json()
        if not isinstance(active, list) or any(not isinstance(item, dict) for item in active):
            return False, "执行端活跃运行列表无效"
        if candidates.intersection(item.get("trace_id") for item in active):
            return False, "执行端仍报告旧 Trace 活跃"
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return False, f"执行端停止核对失败：{type(exc).__name__}"
    return True, "旧工作线程已退出，执行任务终态及活跃列表已核对"


def _delete_trace(identifier: str, candidates: set[str]) -> str | None:
    # Lock before checking references so another SQLite writer cannot add one mid-delete.
    with db.transaction() as connection:
        connection.execute("UPDATE runs SET trace_id=trace_id WHERE trace_id=?", (identifier,))
        reason = _protected_traces(candidates).get(identifier)
        if reason:
            return reason
        revisions = list(connection.execute("SELECT artifact_revision_id,artifact_id FROM artifact_revisions WHERE producer_trace_id=?", (identifier,)))
        connection.execute("DELETE FROM artifact_revisions WHERE producer_trace_id=?", (identifier,))
        connection.execute("DELETE FROM trace_payload_links WHERE trace_id=?", (identifier,))
        for revision_id, artifact_id in revisions:
            connection.execute("DELETE FROM lineage_edges WHERE (from_type='artifact_revision' AND from_id=?) OR (to_type='artifact_revision' AND to_id=?)", (revision_id, revision_id))
            connection.execute("DELETE FROM artifacts WHERE artifact_id=? AND NOT EXISTS (SELECT 1 FROM artifact_revisions WHERE artifact_id=?)", (artifact_id, artifact_id))
        connection.execute("DELETE FROM lineage_edges WHERE (from_type='trace' AND from_id=?) OR (to_type='trace' AND to_id=?)", (identifier, identifier))
        connection.execute("DELETE FROM runs WHERE trace_id=?", (identifier,))
    return None


def _delete_payload(identifier: str) -> str | None:
    if len(identifier) != 64 or any(char not in "0123456789abcdef" for char in identifier):
        raise ValueError("invalid payload content address")
    with db.transaction() as connection:
        connection.execute("UPDATE payload_objects SET payload_id=payload_id WHERE payload_id=?", (identifier,))
        metadata = connection.execute("SELECT sealed FROM payload_objects WHERE payload_id=?", (identifier,)).fetchone()
        count = connection.execute("SELECT (SELECT COUNT(*) FROM trace_payload_links WHERE payload_id=?) + (SELECT COUNT(*) FROM artifact_revisions WHERE payload_id=?) + (SELECT COUNT(*) FROM outcome_records WHERE payload_id=?)", (identifier, identifier, identifier)).fetchone()[0]
        if count or (metadata and metadata["sealed"]):
            return "仍有有效正文引用或封存要求"
        root = settings.trace_payload_path.resolve()
        path = root / (identifier + ".json")
        if path.is_symlink() or path.resolve().parent != root:
            raise ValueError("payload path escapes configured root")
        path.unlink(missing_ok=True)
        connection.execute("UPDATE payload_objects SET deleted_at=?,storage_path='' WHERE payload_id=?", (datetime.now(UTC).isoformat(), identifier))
    return None


def apply_manifest(document: dict, *, confirm_stopped: Callable[[list[dict]], tuple[bool, str]] = confirm_stopped) -> dict:
    """Resume the same reviewed inventory; never expand to newly discovered objects."""
    if document.get("version") != 1 or document.get("digest") != _digest(document) or document.get("database") != _database_path():
        raise ValueError("manifest invalid, changed, or bound to another database")
    from app.benchmark import runner
    result = {"status": "complete", "manifest_digest": document["digest"], "deleted": [], "retained": list(document["retained"]), "failed": [], "stops": []}
    approved: set[str] = set()
    for batch_id in document["batch_ids"]:
        rows = [row for row in document["rows"] if row["batch_id"] == batch_id]
        try:
            current = db.query_all("SELECT id,batch_id,trace_id,status FROM benchmark_runs WHERE batch_id=? ORDER BY id", (batch_id,))
            expected = {row["id"]: row["trace_id"] for row in rows}
            if any(row["id"] not in expected or row["trace_id"] != expected[row["id"]] for row in current):
                raise ValueError("旧批次归属已变化；重新生成清单后重试")
            if current:
                runner.request_stop(batch_id)
            confirmed, detail = confirm_stopped(rows)
            result["stops"].append({"id": batch_id, "confirmed": confirmed, "detail": detail})
            if not confirmed:
                raise RuntimeError(detail)
            approved.add(batch_id)
        except Exception as exc:
            result["failed"].append({"kind": "batch_stop", "id": batch_id, "reason": str(exc)})
    if approved:
        # Legacy stores can lack payload lookup indexes; otherwise each body
        # scans all reference tables again during a large retirement.
        for table in ("trace_payload_links", "artifact_revisions", "outcome_records"):
            db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_payload ON {table}(payload_id)")
    candidates = {row["trace_id"] for row in document["rows"] if row["trace_id"] in document["trace_ids"] and row["batch_id"] in approved}
    blocked = {row["trace_id"] for row in document["rows"] if row["trace_id"] and row["batch_id"] not in approved}
    failed_traces: set[str] = set()
    for identifier in sorted(candidates - blocked):
        try:
            reason = _delete_trace(identifier, candidates)
            result["retained" if reason else "deleted"].append({"kind": "trace" if reason else "observer_trace", "id": identifier, **({"reason": reason} if reason else {})})
        except Exception as exc:
            failed_traces.add(identifier)
            result["failed"].append({"kind": "trace", "id": identifier, "reason": str(exc)})
    for identifier in sorted({value for trace_id in candidates - blocked - failed_traces for value in document["payloads"].get(trace_id, [])}):
        try:
            reason = _delete_payload(identifier)
            result["retained" if reason else "deleted"].append({"kind": "payload", "id": identifier, **({"reason": reason} if reason else {})})
        except Exception as exc:
            result["failed"].append({"kind": "payload", "id": identifier, "reason": str(exc)})
    for batch_id in sorted(approved):
        if any(row["trace_id"] in failed_traces for row in document["rows"] if row["batch_id"] == batch_id):
            continue
        try:
            with db.transaction() as connection:
                connection.execute("UPDATE evolve_sessions SET benchmark_batch_id=NULL WHERE benchmark_batch_id=?", (batch_id,))
                for table in ("benchmark_batch_meta", "benchmark_batch_judge", "benchmark_runs"):
                    connection.execute(f"DELETE FROM {table} WHERE batch_id=?", (batch_id,))
            result["deleted"].append({"kind": "batch", "id": batch_id})
        except Exception as exc:
            result["failed"].append({"kind": "batch", "id": batch_id, "reason": str(exc)})
    result["status"] = "partial" if result["failed"] else "complete"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="仅清理冻结清单内可确认旧版归属的历史；在已部署退役版本的 evolution 容器内运行。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", type=Path, metavar="MANIFEST")
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.apply and arguments.output.resolve() == arguments.apply.resolve():
        parser.error("output must not overwrite the reviewed manifest")
    # No init_db: a dry-run must not seed, migrate, or otherwise write application data.
    try:
        if not settings.db_path.is_file():
            raise ValueError("configured evolution database does not exist")
        result = build_manifest() if arguments.dry_run else apply_manifest(json.loads(arguments.apply.read_text(encoding="utf-8")))
    except Exception as exc:
        result = {"status": "failed", "failed": [{"kind": "cleanup", "reason": str(exc)}]}
    arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result.get("status", "dry_run"), "output": str(arguments.output), "failures": len(result.get("failed", []))}, ensure_ascii=False))
    return 1 if result.get("status") in {"partial", "failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
