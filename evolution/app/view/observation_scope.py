"""One explicit source boundary for lists, events, and aggregate queries."""
from __future__ import annotations

import json
from typing import Any, Literal

ObservationScope = Literal["user", "experiment"]
EXPERIMENT_PURPOSES = ("optimization", "evolution", "evolution_eval", "evolution_evolve")


def source_scope(run: dict[str, Any]) -> str | None:
    if run.get("experiment_id") or (run.get("external_refs") or {}).get("experiment_id"):
        return "experiment"
    purpose = run.get("run_purpose") or run.get("purpose")
    if purpose == "user_generation":
        return "user"
    if purpose in EXPERIMENT_PURPOSES:
        return "experiment"
    return None


def scope_sql(scope: ObservationScope, alias: str = "r") -> str:
    evidence = f"COALESCE(json_extract({alias}.external_refs_json,'$.experiment_id'),'')<>''"
    if _has_experiment_records():
        evidence += f""" OR {alias}.trace_id IN (
            SELECT trace.value FROM self_evolution_runs experiment,
                json_each(experiment.payload,'$.samples') sample,
                json_each(sample.value,'$.trace_ids') trace
            UNION SELECT trace.value FROM self_evolution_runs experiment,
                json_each(experiment.payload,'$.samples') sample,
                json_each(sample.value,'$.evaluation_trace_ids') trace
            UNION SELECT trace.value FROM self_evolution_runs experiment,
                json_each(experiment.payload,'$.rounds') round_,
                json_each(round_.value,'$.trace_ids') trace)"""
    if scope == "user":
        return f"({alias}.run_purpose='user_generation' AND NOT ({evidence}))"
    values = ",".join(f"'{value}'" for value in EXPERIMENT_PURPOSES)
    return f"({alias}.run_purpose IN ({values}) OR {evidence})"


def _has_experiment_records() -> bool:
    from app.core import db
    return db._conn is not None and db.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='self_evolution_runs'") is not None


def experiment_metadata() -> dict[str, dict[str, str]]:
    """Use saved sample identities for historical runs without source references."""
    from app.core import db

    if not _has_experiment_records():
        return {}
    result = {}
    for row in db.query_all("SELECT id,payload FROM self_evolution_runs"):
        run = json.loads(row["payload"])
        for sample in run.get("samples", []):
            refs = {"experiment_id": row["id"], "sample_id": sample["id"],
                    "phase": sample.get("phase", ""), "round": str(sample.get("round", 0)),
                    "arm": sample.get("arm", ""), "attempt": str(sample.get("attempt", 1))}
            for key in ("trace_ids", "evaluation_trace_ids"):
                for trace_id in sample.get(key, []):
                    result[trace_id] = refs
        for round_ in run.get("rounds", []):
            for trace_id in round_.get("trace_ids", []):
                result[trace_id] = {"experiment_id": row["id"], "round": str(round_["round"]), "phase": "improvement"}
    return result
