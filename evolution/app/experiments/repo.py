"""Additive SQLite records; one atomic document owns each experiment state."""
from __future__ import annotations

import copy
import json
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core import db

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def run_lock(run_id: str):
    with _locks_guard:
        return _locks.setdefault(run_id, threading.RLock())


def init_schema() -> None:
    with db.transaction() as connection:
        for table in ("self_evolution_packages", "self_evolution_rules", "self_evolution_runs"):
            connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
    from app.experiments.rubrics import seed_rules
    seed_rules()


def insert(table: str, payload: dict[str, Any]) -> None:
    db.execute(f"INSERT INTO {table}(id,payload,created_at) VALUES (?,?,?)",
               (payload["id"], json.dumps(payload, ensure_ascii=False), payload["created_at"]))


def _get(table: str, identifier_: str) -> dict | None:
    row = db.query_one(f"SELECT payload FROM {table} WHERE id=?", (identifier_,))
    return json.loads(row["payload"]) if row else None


def _list(table: str) -> list[dict]:
    return [json.loads(row["payload"]) for row in db.query_all(f"SELECT payload FROM {table} ORDER BY created_at DESC,id")]


def get_package(package_id: str) -> dict | None:
    return _get("self_evolution_packages", package_id)


def list_packages() -> list[dict]:
    return _list("self_evolution_packages")


def get_rule(rule_id: str) -> dict | None:
    return _get("self_evolution_rules", rule_id)


def list_rules() -> list[dict]:
    return _list("self_evolution_rules")


def get_run(run_id: str) -> dict | None:
    return _get("self_evolution_runs", run_id)


def list_runs() -> list[dict]:
    return _list("self_evolution_runs")


def put_run(run: dict) -> None:
    # Worker and stop/release operations share this lock; the JSON write is atomic.
    with run_lock(run["id"]):
        current = get_run(run["id"])
        if current and current["status"] in {"stopping", "stopped"} and run["status"] not in {"stopping", "stopped"}:
            for key in ("status", "phase", "end_reason"):
                run[key] = current.get(key)
            existing_ids = {item["id"] for item in run.setdefault("events", [])}
            run["events"].extend(item for item in current.get("events", []) if item["id"] not in existing_ids)
        run["updated_at"] = now()
        db.execute("INSERT INTO self_evolution_runs(id,payload,created_at) VALUES (?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                   (run["id"], json.dumps(run, ensure_ascii=False), run["created_at"]))


def event(run: dict, kind: str, message: str) -> None:
    run.setdefault("events", []).append({"id": identifier("event"), "kind": kind, "message": message, "created_at": now()})


def freeze_credentials(config_id: int) -> dict:
    row = db.query_one("SELECT id,model,base_url,api_key_enc,scope FROM llm_configs WHERE id=?", (config_id,))
    if not row or not row["api_key_enc"] or not row["model"] or not row["base_url"]:
        raise ValueError(f"模型配置 #{config_id} 缺少模型、地址或凭据")
    return dict(row)


def decrypt_credentials(snapshot: dict) -> dict:
    from app.core.security import decrypt_secret
    return {"model": snapshot["model"], "base_url": snapshot["base_url"],
            "api_key": decrypt_secret(snapshot["api_key_enc"], db.get_master_key())}


def frozen_model_credentials(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    return decrypt_credentials(run["private"]["model"])


def public_settings(settings: dict) -> dict:
    return copy.deepcopy(settings)
