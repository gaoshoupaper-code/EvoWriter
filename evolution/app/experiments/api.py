"""JSON-only experiment APIs; source text keeps the existing super-admin boundary."""
from __future__ import annotations

import copy
import hmac

from fastapi import APIRouter, HTTPException, Query, Request

from app.core import db
from app.core.settings import settings
from app.experiments import clients, packages, release, repo, rubrics, runner
from app.experiments.schemas import PackageCreate, RuleCreate, RunCreate, TASK_TYPES
from app.trace.access import require_full_content_access

router = APIRouter(prefix="/experiments", tags=["experiments"])
internal_router = APIRouter(prefix="/ingestion", tags=["experiment-internal"])


def _failure(exc: Exception):
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="记录不存在") from exc
    if isinstance(exc, clients.ServiceUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _can_read(request: Request) -> bool:
    return bool(getattr(request.state, "is_super_admin", False))


def _usage(run: dict) -> dict:
    generations = [sample.get("usage", {}).get("generation", {}) for sample in run.get("samples", []) if sample.get("request_saved")]
    judges = [attempt for sample in run.get("samples", []) for attempt in sample.get("usage", {}).get("judge_attempts", [])]
    evolutions = [item.get("usage", {}) for item in run.get("rounds", [])]
    return {"generation": generations, "evaluation": judges, "evolution": evolutions,
            "known": bool(generations) and all(value.get("known", False) for value in generations) and all(item.get("usage") for item in judges) and all(value.get("known", False) for value in evolutions)}


def public_run(run: dict, *, can_read_content: bool, summary: bool = False) -> dict:
    value = copy.deepcopy(run)
    value.pop("private", None)
    for item in value.get("rounds", []):
        item.pop("work_root", None)
        item.pop("frozen_candidate", None)
    for sample in value.get("samples", []):
        sample.pop("judge_results", None)
        sample.pop("judge_errors", None)
    if not can_read_content:
        safe_samples = []
        for sample in value.get("samples", []):
            safe = {key: sample[key] for key in (
                "id", "phase", "round", "arm", "package_id", "package_title", "task_type", "repeat", "attempt",
                "status", "task_id", "trace_ids", "evaluation_trace_ids", "completed", "input_revision", "source_commit", "artifact_digest", "usage",
            ) if key in sample}
            checks = sample.get("checks") or {}
            safe["checks"] = {key: checks[key] for key in ("complete", "passed", "missing", "volume_ranges") if key in checks}
            score = sample.get("scores")
            safe["scores"] = None if score is None else {
                "overall": score.get("overall"),
                "dimensions": {key: number for key, number in score.get("dimensions", {}).items() if isinstance(number, (int, float)) and not isinstance(number, bool)},
                "reasons": {},
            }
            safe.update(artifacts={}, error="任务异常，完整原因需正文读取权限。" if sample.get("error") else None)
            safe_samples.append(safe)
        value["samples"] = safe_samples
        value["rounds"] = [{**{key: item[key] for key in (
            "round", "status", "source_commit", "base_commit", "digest", "report", "trace_ids", "usage",
        ) if key in item}, "summary": "", "diff": "", "error": "本轮异常，完整原因需正文读取权限。" if item.get("error") else None}
            for item in value.get("rounds", [])]
    value.update(can_read_content=can_read_content,
        can_publish=run["status"] == "awaiting_review" and bool(run.get("report", {}).get("passed")),
        stale_reason=run.get("stale_reason"), usage=_usage(run))
    value["progress"] = {"total": len(run.get("samples", [])),
        "done": sum(sample.get("status") in {"completed", "failed", "stopped"} for sample in run.get("samples", [])),
        "completed": sum(bool(sample.get("completed")) for sample in run.get("samples", []))}
    if summary:
        for field in ("rounds", "samples", "events"):
            value.pop(field, None)
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


@router.get("/rules")
def list_rules(task_type: str | None = Query(None)):
    if task_type is not None and task_type not in TASK_TYPES:
        raise HTTPException(status_code=400, detail="未知任务类型")
    return {"items": rubrics.list_rules(task_type)}


@router.post("/rules", status_code=201)
def save_rule(body: RuleCreate):
    return rubrics.save_rule(body)


@router.get("/options")
def experiment_options():
    from app.benchmark.api import list_judge_groups
    try:
        versions = clients.versions()
    except (clients.ServiceUnavailable, clients.TaskNotFound) as exc:
        _failure(exc)
    groups = list_judge_groups()
    return {"task_types": list(TASK_TYPES), "versions": versions["items"], "production_version": versions["production_version"],
            "models": db.LlmConfigsRepository.list_all("executor"), "judge_groups": groups["groups"] if "groups" in groups else groups.get("items", [])}


@router.post("/runs", status_code=201)
def start_run(body: RunCreate, request: Request):
    try:
        run = runner.create_run(body, str(getattr(request.state, "user_id", "unknown")))
    except (ValueError, KeyError, clients.ServiceUnavailable) as exc:
        _failure(exc)
    runner.start_worker(run["id"])
    return public_run(run, can_read_content=_can_read(request))


@router.get("/runs")
def list_runs(request: Request):
    return {"items": [public_run(run, can_read_content=_can_read(request), summary=True) for run in repo.list_runs()]}


@router.get("/runs/{run_id}")
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
