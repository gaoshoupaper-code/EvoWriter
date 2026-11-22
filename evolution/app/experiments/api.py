"""JSON-only experiment APIs; source text keeps the existing super-admin boundary."""
from __future__ import annotations

import copy
import hmac
from itertools import islice

from contracts.trace.usage import normalize_usage
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from contracts.target_tasks import configured_task_type

from app.core import db
from app.core.settings import settings
from app.experiments import agent, clients, evaluation, packages, release, repo, rubrics, runner
from app.experiments.schemas import PackageCreate, RuleCreate, RunCreate
from app.trace.access import require_full_content_access
from app.view.display_context import trace_run_views

def require_suite() -> str:
    scope = configured_task_type()
    if not scope:
        raise HTTPException(status_code=404, detail="请进入所属创作 Agent")
    return scope


router = APIRouter(prefix="/experiments", tags=["experiments"], dependencies=[Depends(require_suite)])
internal_router = APIRouter(prefix="/ingestion", tags=["experiment-internal"])
# The archive uses these bounded read views. Production mounts them through the scoped router only.
history_router = APIRouter()


def _failure(exc: Exception):
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="记录不存在") from exc
    if isinstance(exc, clients.ServiceUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _can_read(request: Request) -> bool:
    return bool(getattr(request.state, "is_super_admin", False))


def _usage_group(records) -> dict:
    fields = ("input_tokens", "output_tokens", "total_tokens")
    totals = dict.fromkeys(fields, 0)
    counts = dict.fromkeys(fields, 0)
    calls = failed_calls = complete = 0
    for usage, status in records:
        calls += 1
        failed_calls += status == "failed"
        usage = usage if isinstance(usage, dict) else {}
        tokens = normalize_usage(usage)
        for field in fields:
            if tokens[field] is not None:
                totals[field] += tokens[field]
                counts[field] += 1
        complete += (all(tokens[field] is not None for field in fields)
                     and usage.get("known") is not False
                     and not any(usage.get(field + "_partial") for field in ("input", "output", "total")))
    known = calls > 0 and complete == calls
    return {"calls": calls, "failed_calls": failed_calls,
            **{field: totals[field] if counts[field] else None for field in fields},
            "known": known, "partial": any(counts.values()) and not known}


def _usage(run: dict) -> dict:
    def generation_records():
        for sample in run.get("samples", []):
            usage = sample.get("usage", {})
            for retry in sample.get("generation_retries", []):
                yield retry["usage"], "failed"
            if sample.get("request_saved"):
                yield usage.get("generation", {}), sample.get("generation_status") or sample.get("status")

    generation = _usage_group(generation_records())
    evaluation = _usage_group((attempt.get("usage"), attempt.get("status"))
                              for sample in run.get("samples", [])
                              for attempt in sample.get("usage", {}).get("judge_attempts", []))
    evolution = _usage_group((item.get("usage", {}), item.get("status")) for item in run.get("rounds", []))
    return {"generation": generation, "evaluation": evaluation, "evolution": evolution,
            "known": generation["calls"] > 0 and all(group["known"] for group in
                (generation, evaluation, evolution) if group["calls"])}


def _score_numbers(values: dict, fields) -> dict:
    return {key: values[key] for key in fields if type(values.get(key)) in (int, float)}


def _runtime_numbers(runtime: dict) -> dict:
    projected = _score_numbers(runtime, ("score",))
    for group, fields in (("tools", ("successes", "total", "rate", "score")),
                          ("tokens", ("value", "baseline", "saving", "score")), ("skill", ("score",))):
        if group in runtime:
            projected[group] = _score_numbers(runtime[group], fields)
    return projected


def public_run(run: dict, *, can_read_content: bool, summary: bool = False) -> dict:
    # Project before copying: discarded histories can contain tens of thousands of calls.
    omitted = {"private", "rounds", "samples", "usage"}
    if summary:
        omitted.add("events")
    value = copy.deepcopy({key: item for key, item in run.items() if key not in omitted})
    if run.get("task_type") == "chapter_text":
        rule = run.get("private", {}).get("rules", {}).get("chapter_text")
        if rule and all(key in rule for key in ("id", "name", "version")):
            value["scoring_rule"] = {key: rule[key] for key in ("id", "name", "version")}
            if run.get("runtime_scoring_version"):
                value["scoring_rule"]["dimension_weights"] = {
                    dimension["key"]: dimension["weight"] for dimension in rule["dimensions"]}
    if not summary:
        value["rounds"] = [copy.deepcopy({key: item for key, item in round_.items()
            if key not in {"work_root", "frozen_candidate"}}) for round_ in run.get("rounds", [])]
        value["samples"] = []
        for sample in run.get("samples", []):
            public = {key: item for key, item in sample.items() if key not in {
                "judge_results", "judge_errors", "judge_evidence_feedback", "usage", "generation_retries", "runtime_metrics",
            }}
            if "runtime_metrics" in sample:
                public["runtime_metrics"] = _runtime_numbers(sample["runtime_metrics"])
            if sample.get("generation_retries"):
                public["trace_ids"] = list(dict.fromkeys([
                    *sample.get("trace_ids", []),
                    *(trace_id for retry in reversed(sample["generation_retries"]) for trace_id in retry["trace_ids"]),
                ]))
            public["usage"] = {key: item for key, item in sample.get("usage", {}).items() if key != "judge_attempts"}
            has_partial_scores = sample.get("judge_results") or sample.get("judge_errors")
            if sample.get("completed") and sample.get("scores") is None and ("judge_repeats" in run or has_partial_scores):
                progress = evaluation.score_progress(run, sample)
                if not can_read_content:
                    reasons = progress.pop("reasons")
                    if run.get("runtime_scoring_version"):
                        progress["reasons"] = {dimension: [
                            _score_numbers(item, ("judge_id", "repeat", "score")) for item in entries
                            if type(item.get("score")) in (int, float)
                        ] for dimension, entries in reasons.items()}
                public["score_progress"] = progress
            value["samples"].append(copy.deepcopy(public))
        value["usage"] = _usage(run)
        trace_ids = [trace_id for sample in value["samples"]
                     for key in ("trace_ids", "evaluation_trace_ids") for trace_id in sample.get(key, [])]
        trace_ids.extend(trace_id for round_ in run.get("rounds", []) for trace_id in round_.get("trace_ids", []))
        value["trace_runs"] = trace_run_views(trace_ids)
    if not can_read_content and not summary:
        safe_samples = []
        for sample in value.get("samples", []):
            safe = {key: sample[key] for key in (
                "id", "phase", "round", "arm", "package_id", "package_title", "task_type", "repeat", "attempt",
                "status", "task_id", "trace_ids", "evaluation_trace_ids", "completed", "input_revision", "source_commit", "artifact_digest", "usage",
                "execution_phase", "target", "score_retry_at", "scoring_attempts", "score_progress", "runtime_metrics",
            ) if key in sample}
            checks = sample.get("checks") or {}
            safe["checks"] = {key: checks[key] for key in ("complete", "passed", "missing", "volume_ranges") if key in checks}
            score = sample.get("scores")
            safe["scores"] = None if score is None else {
                "overall": score.get("overall"),
                "dimensions": {key: number for key, number in score.get("dimensions", {}).items() if isinstance(number, (int, float)) and not isinstance(number, bool)},
                "reasons": {},
            }
            if score is not None and score.get("runtime_scoring_version"):
                safe["scores"].update(_score_numbers(score, ("quality", "runtime_scoring_version")),
                    rule_id=score["rule_id"], runtime=_runtime_numbers(score["runtime"]),
                    weights=_score_numbers(score["weights"], ("quality", "runtime", "tools", "tokens", "skill")))
            safe.update(artifacts={}, error="任务异常，完整原因需正文读取权限。" if sample.get("error") else None)
            safe_samples.append(safe)
        value["samples"] = safe_samples
        value["rounds"] = [{**{key: item[key] for key in (
            "round", "status", "source_commit", "base_commit", "digest", "report", "regressions", "trace_ids", "usage",
        ) if key in item}, **({"startup_check": {"status": item["startup_check"]["status"]}} if item.get("startup_check") else {}),
        "summary": "", "diff": "", "error": "本轮异常，完整原因需正文读取权限。" if item.get("error") else None}
            for item in value.get("rounds", [])]
    value.update(can_read_content=can_read_content,
        can_publish=run["status"] in {"awaiting_review", "publish_failed"} and not run.get("stale_reason")
            and run.get("rollback_status") not in {"restoring", "restored"}
            and bool(run.get("report", {}).get("passed")) and bool(run.get("best_candidate")),
        can_restore=bool(run.get("published")) and run["status"] in {"activated", "publish_failed"} and run.get("rollback_status") != "restored",
        models=repo.public_models(run),
        stale_reason=run.get("stale_reason"))
    current = next((item for item in run.get("rounds", []) if item.get("round") == run.get("current_round")), None)
    value["current_candidate"] = ({"commit": current["source_commit"], "round": current["round"], "digest": current.get("digest")}
                                  if current and current.get("source_commit") else None)
    value["progress"] = {"total": len(run.get("samples", [])),
        "done": sum(sample.get("status") in {"completed", "failed", "stopped"} for sample in run.get("samples", [])),
        "completed": sum(bool(sample.get("completed")) for sample in run.get("samples", []))}
    phase_samples = [sample for sample in run.get("samples", []) if sample.get("phase") == run.get("phase")
                     and (sample.get("round", 0) == run.get("current_round", 0) or run.get("phase") == "baseline")]
    value["progress"]["phase"] = {"name": run.get("phase"), "round": run.get("current_round", 0), "total": len(phase_samples),
        "done": sum(sample.get("status") in {"completed", "failed", "stopped"} for sample in phase_samples),
        "completed": sum(bool(sample.get("completed")) for sample in phase_samples),
        "active": sum(sample.get("status") in {"running", "scoring", "interrupted"} for sample in phase_samples)}
    return value


@router.get("/packages/templates")
def package_templates():
    return {"items": packages.templates()}


@router.get("/packages")
def list_packages(request: Request):
    return {"items": [{key: value for key, value in package.items() if key != "input"} for package in repo.list_packages()],
            "can_read_content": _can_read(request)}


@router.post("/packages", status_code=201)
def import_package(body: PackageCreate):
    try:
        return packages.import_package(body)
    except ValueError as exc:
        _failure(exc)


@router.get("/packages/{package_id}")
def package_detail(package_id: str, request: Request):
    require_full_content_access(request)
    package = repo.get_package(package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="样本不存在")
    return package


@router.put("/packages/{package_id}")
def replace_package(package_id: str, body: PackageCreate):
    try:
        return packages.import_package(body, previous_id=package_id)
    except (KeyError, ValueError) as exc:
        _failure(exc)


@router.delete("/packages/{package_id}")
def delete_package(package_id: str):
    try:
        repo.delete_package(package_id)
    except KeyError as exc:
        _failure(exc)
    return {"ok": True}


@router.get("/rules")
def list_rules(task_type: str | None = Query(None)):
    scope = require_suite()
    if task_type and task_type != scope:
        raise HTTPException(status_code=404, detail="找不到该套规则")
    return {"items": rubrics.list_rules(scope)}


@router.post("/rules", status_code=201)
def save_rule(body: RuleCreate):
    return rubrics.save_rule(body)


@router.get("/evolver-assets")
def get_evolver_assets(scope: str = Depends(require_suite)):
    return agent.evolver_assets(assessment=scope == "chapter_text")


@router.get("/options")
def experiment_options():
    from app.benchmark.api import list_judge_groups
    try:
        versions = clients.versions()
    except (clients.ServiceUnavailable, clients.TaskNotFound) as exc:
        _failure(exc)
    groups = list_judge_groups()
    scope = require_suite()
    current = rubrics.list_rules(scope)
    if scope == "chapter_text":
        current = [rule for rule in current if rubrics.is_current_text_rule(rule)]
    rule_versions = {scope: current[0]["id"]} if current else {}
    current_versions = [item for item in versions["items"] if item["version"] == versions["production_version"]]
    return {"task_types": [scope], "versions": current_versions, "production_version": versions["production_version"],
            **({"launch_rule_ids": [rule["id"] for rule in current]} if scope == "chapter_text" else {}),
            "models": [{key: item.get(key) for key in ("id", "name", "model", "is_active", "has_key")} for item in db.LlmConfigsRepository.list_all("executor")],
            "judge_groups": groups["groups"] if "groups" in groups else groups.get("items", []),
            "defaults": {"rule_versions": rule_versions, "thresholds": {"completion_rate": 1, "quality_gain": .1,
                "regression_tolerance": {scope: 0}}, "max_rounds": 3},
            "builtin_suite": {"id": "isolated-writing-v1", "total": 4}}


@router.post("/runs", status_code=201)
def start_run(body: RunCreate, request: Request):
    try:
        run = runner.create_run(body, str(getattr(request.state, "user_id", "unknown")))
    except (ValueError, KeyError, clients.ServiceUnavailable) as exc:
        _failure(exc)
    runner.start_worker(run["id"])
    return public_run(run, can_read_content=_can_read(request))


@history_router.get("/runs")
def list_runs(request: Request, active: bool = False):
    active_statuses = {"pending", "running", "recovering", "stopping", "publishing"}
    return {"items": [public_run(run, can_read_content=_can_read(request), summary=True)
                      for run in repo.list_runs() if not active or run["status"] in active_statuses]}


@history_router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request):
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="实验不存在")
    if run["status"] == "awaiting_review":
        try:
            release.check_stale(run)
        except clients.ServiceUnavailable:
            value = public_run(run, can_read_content=_can_read(request))
            value.update(can_publish=False, stale_reason="暂时无法核对生产版本")
            return value
    return public_run(run, can_read_content=_can_read(request))


@history_router.get("/runs/{run_id}/usage/evaluation")
def evaluation_usage(run_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="实验不存在")
    samples = run.get("samples", [])
    total = sum(len(sample.get("usage", {}).get("judge_attempts", [])) for sample in samples)
    attempts = ((sample["id"], attempt) for sample in samples
                for attempt in sample.get("usage", {}).get("judge_attempts", []))
    items = []
    for sample_id, attempt in islice(attempts, offset, offset + limit):
        usage = attempt.get("usage")
        counters = ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")
        safe_usage = ({key: usage[key] for key in counters if type(usage.get(key)) is int} if isinstance(usage, dict) else None)
        items.append({"sample_id": sample_id, "judge_id": attempt.get("judge_id"), "dimension": attempt.get("dimension"),
                      "status": attempt.get("status"), "usage": safe_usage})
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/runs/{run_id}/versions/{round_number}/source")
def round_source(run_id: str, round_number: int, request: Request, path: str | None = None):
    from app.experiments.snapshots import archived_source

    require_full_content_access(request)
    if set(request.query_params) - {"path"}:
        raise HTTPException(400, "只支持按本轮资产路径查询")
    if configured_task_type() != "chapter_text" or round_number < 0:
        raise HTTPException(404, "没有该评测版本")
    version = db.query_one(
        """SELECT e.version FROM assessment_entries e JOIN self_evolution_runs r ON r.id=e.assessment_id
           WHERE e.assessment_id=? AND e.category='version' AND e.round_number=? AND e.entry_id=?
             AND json_extract(r.payload,'$.task_type')='chapter_text'
             AND json_extract(r.payload,'$.flow_version')=3 AND json_extract(r.payload,'$.assessment_version')>=1""",
        (run_id, round_number, "baseline" if round_number == 0 else f"round-{round_number}"),
    )
    if not version or not version["version"]:
        raise HTTPException(404, "本轮暂无保存的 Harness 版本")
    try:
        return archived_source(run_id, version["version"], path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "本档案没有所选版本或资产") from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(415, "该资产无法按 UTF-8 文本显示") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, "本轮保存的快照无法读取") from exc


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, request: Request):
    try:
        run = runner.request_stop(run_id)
    except KeyError as exc:
        _failure(exc)
    return public_run(run, can_read_content=_can_read(request))


@router.post("/runs/{run_id}/publish")
def publish_run(run_id: str, request: Request):
    try:
        run = release.publish_run(run_id, str(getattr(request.state, "user_id", "unknown")))
    except (ValueError, KeyError, clients.ServiceUnavailable) as exc:
        _failure(exc)
    return public_run(run, can_read_content=_can_read(request))


@router.post("/runs/{run_id}/restore")
def restore_run(run_id: str, request: Request):
    try:
        run = release.restore_run(run_id, str(getattr(request.state, "user_id", "unknown")))
    except (ValueError, KeyError, clients.ServiceUnavailable) as exc:
        _failure(exc)
    return public_run(run, can_read_content=_can_read(request))


@internal_router.get("/experiment-key/{run_id}")
def frozen_key(run_id: str, request: Request):
    # Unlike the dev-mode middleware bypass, this new secret endpoint fails closed.
    expected = settings.notify_token
    provided = request.headers.get("x-notify-token", "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="missing or invalid X-Notify-Token")
    try:
        return repo.frozen_model_credentials(run_id)
    except KeyError as exc:
        _failure(exc)


router.include_router(history_router)
