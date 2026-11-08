"""All readers of the shared SQLite connection must use its transaction lock."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.core import db


@pytest.fixture
def connection():
    previous = db._conn
    current = db.sqlite3.connect(":memory:", check_same_thread=False)
    current.row_factory = db.sqlite3.Row
    current.executescript("""
        CREATE TABLE judge_groups (id INTEGER, name TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE judge_group_members (group_id INTEGER, config_id INTEGER, position INTEGER);
        CREATE TABLE llm_configs (id INTEGER, scope TEXT);
        CREATE TABLE benchmark_batch_meta (batch_id TEXT, stop_reason TEXT);
        CREATE TABLE runs (trace_id TEXT, evidence_status TEXT, evidence_gaps_json TEXT);
        CREATE TABLE counter (value INTEGER);
        INSERT INTO judge_groups VALUES (1, 'test', 'now', 'now');
        INSERT INTO judge_group_members VALUES (1, 1, 0);
        INSERT INTO llm_configs VALUES (1, 'evolution');
        INSERT INTO benchmark_batch_meta VALUES ('batch', 'user_stop');
        INSERT INTO runs VALUES ('trace', 'unknown', '[]');
        INSERT INTO counter VALUES (0);
    """)
    db._conn = current
    try:
        yield current
    finally:
        current.set_trace_callback(None)
        current.close()
        db._conn = previous


def _read(operation):
    if operation == "groups":
        return db.JudgeGroupsRepository.list_all()
    if operation == "group":
        return db.JudgeGroupsRepository.get(1)
    if operation == "members":
        return db._validate_members([1])
    if operation == "stop_reason":
        from app.benchmark.repo import get_stop_reason
        return get_stop_reason("batch")
    if operation == "trace_columns":
        from app.trace.evidence import CreationTraceEligibility, _persist_report
        return _persist_report(CreationTraceEligibility(
            trace_id="trace", row={"trace_id": "trace"}, eligible=False,
            evidence_status="incomplete", transport_integrity="verified", missing_fields=("write",),
        ))
    return db.query_all("SELECT id FROM judge_groups")


@pytest.mark.parametrize("operation", ["groups", "group", "members", "stop_reason", "trace_columns", "query"])
def test_runtime_sql_executes_under_the_shared_lock(connection, operation):
    unlocked = []
    seen = []

    def trace(sql):
        seen.append(sql)
        if not db._lock._is_owned():
            unlocked.append(sql)

    connection.set_trace_callback(trace)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_read, operation).result(timeout=5)
    assert seen, "The test must exercise the real SQLite connection"
    assert not unlocked, f"Shared SQLite used outside its lock: {unlocked}"


def test_get_conn_creates_only_one_connection_under_concurrent_start():
    previous = db._conn
    db._conn = None
    start = threading.Barrier(8)
    entered = threading.Event()
    release = threading.Event()
    created = []

    def connect():
        value = db.sqlite3.connect(":memory:", check_same_thread=False)
        created.append(value)
        entered.set()
        assert release.wait(5)
        return value

    def get():
        start.wait(timeout=5)
        return db.get_conn()

    try:
        with patch.object(db, "_connect", side_effect=connect), ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(get) for _ in range(8)]
            assert entered.wait(5)
            # Let concurrent callers reach the lazy initialization boundary.
            threading.Event().wait(.15)
            release.set()
            values = [future.result(timeout=5) for future in futures]
        assert len(created) == 1
        assert all(value is values[0] for value in values)
    finally:
        release.set()
        for value in created:
            value.close()
        db._conn = previous


def test_concurrent_model_group_trace_reads_and_transactions(connection):
    start = threading.Barrier(8)

    def work(worker):
        start.wait(timeout=5)
        for number in range(200):
            # Different statements also exercise the real driver's cache eviction.
            model = db.query_one(f"SELECT id, scope FROM llm_configs /* {worker}:{number} */")
            assert model == {"id": 1, "scope": "evolution"}
            assert db.JudgeGroupsRepository.list_all()[0]["members"] == [{"config_id": 1, "position": 0}]
            assert db.JudgeGroupsRepository.get(1)["name"] == "test"
            assert db.query_one("SELECT count(*) AS total FROM runs")["total"] == 1
            if number % 5 == 0:
                with db.transaction() as conn:
                    assert db.JudgeGroupsRepository.get(1)["id"] == 1
                    value = conn.execute("SELECT value FROM counter").fetchone()["value"]
                    conn.execute("UPDATE counter SET value=?", (value + 1,))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))
    assert db.query_one("SELECT value FROM counter")["value"] == 320
