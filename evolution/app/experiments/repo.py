"""Additive SQLite records; one atomic document owns each experiment state."""
from __future__ import annotations

import copy
import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core import db
from app.experiments import assessment
from contracts.target_tasks import TaskInput, configured_task_type

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
        assessment.init_schema(connection)
    from app.experiments.rubrics import seed_rules
    seed_rules()
    from app.experiments.seed_data import seed_packages
    seed_packages()


def insert(table: str, payload: dict[str, Any]) -> None:
    db.execute(f"INSERT INTO {table}(id,payload,created_at) VALUES (?,?,?)",
               (payload["id"], json.dumps(payload, ensure_ascii=False), payload["created_at"]))


def _get(table: str, identifier_: str) -> dict | None:
    row = db.query_one(f"SELECT payload FROM {table} WHERE id=?", (identifier_,))
    value = json.loads(row["payload"]) if row else None
    return value if value and _visible(value) else None


def _visible(value: dict) -> bool:
    scope = configured_task_type()
    return not scope or (value.get("task_type") == scope and value.get("flow_version") == 3)


def _list(table: str) -> list[dict]:
    rows = db.query_all(f"SELECT payload FROM {table} ORDER BY created_at DESC,id")
    return [value for row in rows if _visible(value := json.loads(row["payload"]))]


def get_package(package_id: str) -> dict | None:
    return _get("self_evolution_packages", package_id)


def chapter_case(package: dict, assessment_version: int = 2) -> dict:
    inputs = package["input"]
    if assessment_version == 1:
        if not inputs.get("skills"):
            return package
        updates = {"skills": []}
    else:
        outline = f"章纲/第{package['target']['chapter']:03d}章.md"
        updates = {"files": {outline: inputs["files"][outline]}, "demand_md": ""}
    source = TaskInput.model_validate({**inputs, **updates, "input_revision": ""})
    return {**package, "input": source.model_dump(mode="json"), "input_revision": source.input_revision}


def run_package(run: dict, package_id: str) -> dict:
    if not run.get("assessment_version"):
        return run["private"]["packages"][package_id]
    source_revision = run["private"]["package_revisions"][package_id]
    package = get_package(package_id)
    if package is None or package["input_revision"] != source_revision:
        raise ValueError("找不到本次评测绑定的题库版本")
    return chapter_case(package, run["assessment_version"])


def list_packages() -> list[dict]:
    return [p for p in _list("self_evolution_packages") if not p.get("superseded_by") and not p.get("deleted_at")]


def replace_package(previous_id: str, new: dict) -> dict:
    table = "self_evolution_packages"
    with db.transaction() as connection:
        previous = get_package(previous_id)
        if not previous or previous.get("deleted_at"):
            raise KeyError(previous_id)
        if previous.get("superseded_by"):
            raise ValueError("该题已被替换，请刷新后修订当前版本")
        if previous["purpose"] != new["purpose"] or previous["task_type"] != new["task_type"]:
            raise ValueError("替换题须保留所属 Agent 和用途")
        previous["superseded_by"] = new["id"]
        new["supersedes"] = previous_id
        if previous.get("suite"):
            new["suite"] = previous["suite"]
        connection.execute(f"UPDATE {table} SET payload=? WHERE id=?",
                           (json.dumps(previous, ensure_ascii=False), previous_id))
        connection.execute(f"INSERT INTO {table}(id,payload,created_at) VALUES (?,?,?)",
                           (new["id"], json.dumps(new, ensure_ascii=False), new["created_at"]))
    return new


def delete_package(package_id: str) -> None:
    """Remove a question from the active dataset while retaining its experiment input."""
    with db.transaction() as connection:
        package = get_package(package_id)
        if not package or package.get("superseded_by"):
            raise KeyError(package_id)
        if package.get("deleted_at"):
            return
        package["deleted_at"] = now()
        connection.execute("UPDATE self_evolution_packages SET payload=? WHERE id=?",
                           (json.dumps(package, ensure_ascii=False), package_id))


def get_rule(rule_id: str) -> dict | None:
    return _get("self_evolution_rules", rule_id)


def list_rules() -> list[dict]:
    return _list("self_evolution_rules")


def get_run(run_id: str) -> dict | None:
    with run_lock(run_id):
        value = _get("self_evolution_runs", run_id)
        return assessment.hydrate(value) if value and value.get("assessment_version") else value


def list_runs() -> list[dict]:
    """Summaries retain scheduler state; callers that mutate must fetch the full run."""
    return _list("self_evolution_runs")


def put_run(run: dict) -> None:
    # Worker and stop/release operations share this lock; the JSON write is atomic.
    with run_lock(run["id"]):
        current = _get("self_evolution_runs", run["id"])
        if current and current["status"] in {"stopping", "stopped"} and run["status"] not in {"stopping", "stopped"}:
            for key in ("status", "phase", "end_reason"):
                run[key] = current.get(key)
            existing_ids = {item["id"] for item in run.setdefault("events", [])}
            run["events"].extend(item for item in current.get("events", []) if item["id"] not in existing_ids)
        run["updated_at"] = now()
        header, entries = assessment.split(run) if run.get("assessment_version") else (run, [])
        with db.transaction() as connection:
            connection.execute("INSERT INTO self_evolution_runs(id,payload,created_at) VALUES (?,?,?) "
                               "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                               (run["id"], json.dumps(header, ensure_ascii=False), run["created_at"]))
            if entries:
                assessment.save_entries(connection, entries)


def event(run: dict, kind: str, message: str) -> None:
    run.setdefault("events", []).append({"id": identifier("event"), "kind": kind, "message": message, "created_at": now()})


def freeze_credentials(config_id: int) -> dict:
    row = db.query_one("SELECT id,name,model,base_url,api_key_enc,scope FROM llm_configs WHERE id=?", (config_id,))
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


def public_model(snapshot: dict | None) -> dict | None:
    if not snapshot or not snapshot.get("model"):
        return None
    return {"id": snapshot.get("id"), "name": snapshot.get("name") or snapshot["model"], "model": snapshot["model"]}


def public_models(run: dict) -> dict:
    private = run.get("private", {})
    frozen = run.get("models", {})
    return {
        "executor": public_model(frozen.get("executor") or private.get("model")),
        "judges": [value for item in frozen.get("judges", private.get("judges", [])) if (value := public_model(item))],
        "evolver": public_model(frozen.get("evolver") or private.get("evolver")),
    }


def next_run_name(task_type: str) -> str:
    labels = {"master_outline": "总纲", "volume_outlines": "卷纲" if configured_task_type() else "整本卷纲",
              "chapter_outlines": "单章章纲", "chapter_text": "单章正文"}
    date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    prefix = f"{labels[task_type]} · {date} · "
    numbers = [int(value["name"][len(prefix):]) for value in list_runs()
               if value.get("name", "").startswith(prefix) and value["name"][len(prefix):].isdigit()]
    return prefix + f"{max(numbers, default=0) + 1:03d}"
