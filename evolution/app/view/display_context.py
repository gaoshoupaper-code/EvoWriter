"""Readable run metadata, kept separate from governed request and artifact bodies."""
from __future__ import annotations

import json
from typing import Any

from contracts.trace.payload import sanitize_structural_text

from app.core import db


def _object(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def clean_display_context(value: Any) -> dict[str, Any]:
    """Do not let new snapshot fields silently expand the metadata boundary."""
    value = _object(value)
    result: dict[str, Any] = {}
    for key in ("workspace_title", "task_phase", "task_type", "experiment_name", "sample_name", "phase", "arm"):
        if isinstance(value.get(key), str) and value[key].strip():
            result[key] = sanitize_structural_text(value[key].strip(), max_length=500)
    if value.get("operation") in ("generation", "evaluation", "improvement"):
        result["operation"] = value["operation"]
    for key in ("round", "repeat"):
        if type(value.get(key)) is int and value[key] >= (1 if key == "repeat" else 0):
            result[key] = value[key]
    target = _object(value.get("target"))
    safe_target = {}
    if target.get("task_type") in ("volume_outlines", "chapter_outlines", "chapter_text"):
        safe_target["task_type"] = target["task_type"]
    for key in ("chapter", "chapters_per_volume"):
        if type(target.get(key)) is int and target[key] > 0:
            safe_target[key] = target[key]
    if isinstance(target.get("volume_ids"), list):
        safe_target["volume_ids"] = [number for number in target["volume_ids"] if type(number) is int and number > 0]
    if safe_target:
        result["target"] = safe_target
    if value.get("legacy") is True:
        result["legacy"] = True
    return result


def _has_table(name: str) -> bool:
    return db._conn is not None and db.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


def _operation(run: dict) -> str | None:
    purpose = run.get("run_purpose") or run.get("purpose")
    workload = run.get("workload")
    if purpose == "evolution_eval" or workload == "evaluation":
        return "evaluation"
    if purpose == "evolution_evolve" or workload == "evolution":
        return "improvement"
    if purpose in ("user_generation", "optimization", "evolution") or workload == "creation":
        return "generation"
    return None


def _experiment_contexts(runs: list[dict]) -> dict[str, dict]:
    if not runs or not _has_table("self_evolution_runs"):
        return {}
    refs = {run["trace_id"]: _object(run.get("external_refs") or run.get("external_refs_json")) for run in runs}
    experiment_ids = list({ref["experiment_id"] for ref in refs.values() if ref.get("experiment_id")})
    trace_ids = [run["trace_id"] for run in runs if not refs[run["trace_id"]].get("experiment_id")]
    trace_marks = ",".join("?" * len(trace_ids))
    experiment_filter = "e.id IN (" + ",".join("?" * len(experiment_ids)) + ")" if experiment_ids else "0"
    sample_filter = round_filter = experiment_filter
    if trace_ids:
        sample_filter += f""" OR EXISTS (SELECT 1 FROM json_each(s.value,'$.trace_ids') t WHERE t.value IN ({trace_marks}))
            OR EXISTS (SELECT 1 FROM json_each(s.value,'$.evaluation_trace_ids') t WHERE t.value IN ({trace_marks}))"""
        round_filter += f" OR EXISTS (SELECT 1 FROM json_each(r.value,'$.trace_ids') t WHERE t.value IN ({trace_marks}))"
    # Project only public business fields. In particular, never load frozen inputs,
    # credentials, scores, artifacts or the sample's mutable attempt counter.
    samples = db.query_all(
        f"""SELECT e.id AS experiment_id, json_extract(e.payload,'$.name') AS experiment_name,
                   json_extract(s.value,'$.id') AS sample_id,
                   json_extract(s.value,'$.package_title') AS sample_name,
                   json_extract(s.value,'$.task_type') AS task_type,
                   json_extract(s.value,'$.target') AS target,
                   json_extract(s.value,'$.phase') AS phase,
                   json_extract(s.value,'$.arm') AS arm,
                   json_extract(s.value,'$.round') AS round,
                   json_extract(s.value,'$.repeat') AS repeat,
                   json_extract(s.value,'$.trace_ids') AS trace_ids,
                   json_extract(s.value,'$.evaluation_trace_ids') AS evaluation_trace_ids
            FROM self_evolution_runs e LEFT JOIN json_each(e.payload,'$.samples') s
            WHERE {sample_filter}""",
        tuple(experiment_ids + trace_ids + trace_ids),
    )
    rounds = db.query_all(
        f"""SELECT e.id AS experiment_id, json_extract(e.payload,'$.name') AS experiment_name,
                   json_extract(r.value,'$.round') AS round, json_extract(r.value,'$.trace_ids') AS trace_ids
            FROM self_evolution_runs e LEFT JOIN json_each(e.payload,'$.rounds') r
            WHERE {round_filter}""",
        tuple(experiment_ids + trace_ids),
    )
    result = {}
    for run in runs:
        tid = run["trace_id"]
        ref = refs[tid]
        for sample in samples:
            generated = tid in json.loads(sample.get("trace_ids") or "[]")
            evaluated = tid in json.loads(sample.get("evaluation_trace_ids") or "[]")
            same_sample = (ref.get("experiment_id") == sample["experiment_id"]
                           and ref.get("sample_id") == sample.get("sample_id") and bool(sample.get("sample_id")))
            if generated or evaluated or same_sample:
                context = {key: sample.get(key) for key in (
                    "experiment_name", "sample_name", "task_type", "phase", "arm", "round", "repeat")}
                context["target"] = _object(sample.get("target"))
                context["operation"] = "evaluation" if evaluated else "generation" if generated else _operation(run)
                result[tid] = clean_display_context(context)
                break
        if tid in result:
            continue
        linked_round = next((item for item in rounds if tid in json.loads(item.get("trace_ids") or "[]")), None)
        candidates = [linked_round] if linked_round is not None else rounds
        for round_ in candidates:
            linked = tid in json.loads(round_.get("trace_ids") or "[]")
            same_experiment = ref.get("experiment_id") == round_["experiment_id"]
            if linked or same_experiment:
                context = {"experiment_name": round_["experiment_name"]}
                if linked or ref.get("phase") == "improvement" or _operation(run) == "improvement":
                    context.update(operation="improvement")
                    number = round_.get("round") if linked else ref.get("round")
                    if number is not None and str(number).isdigit():
                        context["round"] = int(number)
                result[tid] = clean_display_context(context)
                break
    return result


def display_contexts(runs: list[dict], *, legacy: bool = True) -> dict[str, dict[str, Any]]:
    """Resolve a batch once; saved run names always win over current entity names."""
    result = {}
    saved_contexts = {}
    pending = []
    for run in runs:
        snapshot = _object(run.get("run_snapshot") or run.get("run_snapshot_json"))
        saved = clean_display_context(snapshot.get("display_context") or run.get("display_context")
                                      or run.get("display_context_json"))
        if saved:
            saved_contexts[run["trace_id"]] = saved
            refs = _object(run.get("external_refs") or run.get("external_refs_json"))
            complete_experiment = saved.get("experiment_name") and (
                saved.get("sample_name") or saved.get("operation") == "improvement")
            if complete_experiment or (saved.get("workspace_title") and not refs.get("experiment_id")):
                result[run["trace_id"]] = saved
                continue
        pending.append(run)
    if not pending:
        return result
    workspace_ids = list({run["workspace_id"] for run in pending if run.get("workspace_id")})
    workspaces = {}
    if workspace_ids and _has_table("workspace_cache"):
        marks = ",".join("?" * len(workspace_ids))
        workspaces = {row["workspace_id"]: row["title"] for row in db.query_all(
            f"SELECT workspace_id,title FROM workspace_cache WHERE workspace_id IN ({marks})", tuple(workspace_ids))}
    experiments = _experiment_contexts(pending)
    for run in pending:
        tid = run["trace_id"]
        context = experiments.get(tid, {}).copy()
        if title := workspaces.get(run.get("workspace_id")):
            context["workspace_title"] = title
        if operation := _operation(run):
            context.setdefault("operation", operation)
        saved = saved_contexts.get(tid, {})
        if legacy and (not saved or (context.get("workspace_title") and not saved.get("workspace_title"))):
            context["legacy"] = True
        result[tid] = clean_display_context({**context, **saved})
    return result


def trace_run_views(trace_ids: list[str]) -> dict[str, dict]:
    """Lightweight references for embedded experiment and analysis run links."""
    if not trace_ids or not _has_table("runs"):
        return {}
    ids = list(dict.fromkeys(trace_ids))
    marks = ",".join("?" * len(ids))
    rows = db.query_all(
        f"""SELECT trace_id,workspace_id,session_name,started_at,run_purpose,workload,external_refs_json,
                   json_extract(run_snapshot_json,'$.display_context') AS display_context_json
            FROM runs WHERE trace_id IN ({marks})""", tuple(ids))
    contexts = display_contexts(rows)
    keys = ("trace_id", "session_name", "started_at", "run_purpose", "workload")
    values = {row["trace_id"]: {**{key: row.get(key) for key in keys}, "display_context": contexts[row["trace_id"]]}
              for row in rows}
    return {tid: values[tid] for tid in ids if tid in values}
