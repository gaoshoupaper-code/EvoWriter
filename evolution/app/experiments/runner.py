"""Durable experiment state machine; retries reconcile existing tasks before dispatch."""
from __future__ import annotations

import copy
import asyncio
import json
import threading
import time

from app.core import db
from app.experiments import clients, evaluation, repo
from app.experiments.schemas import PackageCreate, RunCreate, TASK_TYPES

ACTIVE = {"pending", "running", "recovering", "stopping"}
_SCORING_FAILURE_LIMIT = 3
_workers: dict[str, threading.Thread] = {}
_stops: dict[str, threading.Event] = {}
_guard = threading.Lock()
_shutdown = threading.Event()
_recorder = None


def recorder():
    return _recorder


def is_stopped(run_id: str) -> bool:
    with _guard:
        event = _stops.get(run_id)
    return _shutdown.is_set() or bool(event and event.is_set())


def wait_for_stop(run_id: str, seconds: float) -> bool:
    with _guard:
        stop = _stops.setdefault(run_id, threading.Event())
    return stop.wait(seconds) or _shutdown.is_set()


def create_run(request: RunCreate, actor: str) -> dict:
    from app.experiments import packages, rubrics
    all_versions = clients.versions()
    selected = next((item for item in all_versions["items"] if item["version"] == request.baseline_version), None)
    if selected is None:
        raise ValueError("所选基线版本不存在")
    production = clients.production()
    model = repo.freeze_credentials(request.model_config_id)
    if model["scope"] != "executor":
        raise ValueError("运行模型必须选择执行端模型配置")
    group = db.JudgeGroupsRepository.get(request.judge_group_id)
    if group is None or not group["members"]:
        raise ValueError("评委组不存在或没有成员")
    judges = [repo.freeze_credentials(member["config_id"]) for member in group["members"]]
    if any(judge["scope"] not in ("eval", "evolution") for judge in judges):
        raise ValueError("评委组包含不可用于评测的模型配置")
    evaluation.require_heterogeneous_judges(model["model"], judges)
    evolver_id = db.query_one("SELECT id FROM llm_configs WHERE scope='evolution' AND is_active=1")
    if not evolver_id:
        raise ValueError("进化Agent模型未配置")
    evolver = repo.freeze_credentials(evolver_id["id"])
    frozen_rules = {}
    for kind, identifier in request.rule_versions.items():
        rule = rubrics.get_rule(identifier)
        if rule is None or rule["task_type"] != kind:
            raise ValueError(f"{kind}评分规则版本不存在或类型不匹配")
        frozen_rules[kind] = rule
    frozen_packages = {}
    input_purposes = {}
    selections = [(identifier, request.task_type, "optimization") for identifier in request.optimization_ids]
    selections += [(identifier, request.task_type, "validation") for identifier in request.validation_ids]
    selections += [(identifier, kind, "regression") for kind, identifiers in request.regression_ids.items() for identifier in identifiers]
    for identifier, kind, purpose in selections:
        package = repo.get_package(identifier)
        if package is None or package["task_type"] != kind or package["purpose"] != purpose:
            raise ValueError(f"样本 {identifier} 不存在或类型、用途不匹配")
        packages.validate_package(PackageCreate.model_validate({key: package[key] for key in ("title", "purpose", "target", "input")}))
        fingerprint = (kind, package["input_revision"], json.dumps(package["target"], sort_keys=True))
        previous_purpose = input_purposes.get(fingerprint)
        if previous_purpose is not None and previous_purpose != purpose:
            raise ValueError("优化与终验必须使用独立输入；不能通过重复导入同一资料包规避隔离")
        input_purposes[fingerprint] = purpose
        frozen_packages[identifier] = package
    prepared = clients.prepare_artifact(selected["commit"])
    now = repo.now()
    run = {"id": repo.identifier("experiment"), "name": request.name, "task_type": request.task_type, "flow_version": 2,
           "status": "pending", "phase": "baseline", "current_round": 0, "max_rounds": request.max_rounds,
           "settings": request.model_dump(mode="json"),
           "baseline": {"version": selected["version"], "commit": selected["commit"], "artifact_digest": prepared["digest"]},
           "production_at_start": {"commit": production["commit"], "version": production["version"]},
           "best_candidate": None, "rounds": [], "samples": [], "report": {}, "events": [], "usage": {},
           "end_reason": None, "publish_status": "not_started", "loaded_revision": None, "load_error": None,
           "rollback_status": None, "created_at": now, "updated_at": now,
           "private": {"model": model, "judges": judges, "evolver": evolver, "rules": frozen_rules,
                       "packages": frozen_packages, "actor": actor}}
    run["models"] = repo.public_models(run)
    repo.event(run, "created", "实验条件已固定；准备运行原始对照。")
    _add_samples(run, request.optimization_ids, arm="baseline", phase="baseline", round_=0, repeats=1,
                 commit=selected["commit"], digest=prepared["digest"])
    with _guard:
        run["name"] = request.name or repo.next_run_name(request.task_type)
        run["settings"]["name"] = run["name"]
        repo.put_run(run)
    return run


def _add_samples(run: dict, package_ids: list[str], *, arm: str, phase: str, round_: int, repeats: int, commit: str, digest: str) -> None:
    for package_id in package_ids:
        package = run["private"]["packages"][package_id]
        for repeat in range(1, repeats + 1):
            if any(sample["package_id"] == package_id and sample["arm"] == arm and sample["phase"] == phase
                   and sample["round"] == round_ and sample["repeat"] == repeat for sample in run["samples"]):
                continue
            identifier = repo.identifier("sample")
            run["samples"].append({"id": identifier, "phase": phase, "round": round_, "arm": arm,
                "package_id": package_id, "package_title": package["title"], "task_type": package["task_type"],
                "repeat": repeat, "attempt": 1, "status": "pending", "task_id": identifier,
                "trace_ids": [], "completed": False, "checks": {}, "scores": None, "error": None,
                "artifacts": {}, "usage": {}, "input_revision": package["input_revision"],
                "source_commit": commit, "artifact_digest": digest, "target": copy.deepcopy(package["target"]),
                "execution_phase": None})


def _stop_scoring_retries(run: dict, sample: dict, cause: Exception | None = None) -> None:
    if sample.get("status") == "failed" and "score_retry_at" not in sample:
        return
    message = f"评分失败已达{_SCORING_FAILURE_LIMIT}轮上限，停止自动重试；原产物和已保存的部分评分保留。"
    detail = f"评测异常：{type(cause).__name__}：{str(cause)[:240]}；" if cause else ""
    sample.update(status="failed", error=detail + message)
    sample.pop("score_retry_at", None)
    repo.event(run, "evaluation_error", f"样本 {sample['id']}：{message}")
    repo.put_run(run)


def process_sample(run: dict, sample: dict) -> None:
    if sample.get("generation_status") != "completed":
        package = run["private"]["packages"][sample["package_id"]]
        task = None
        if sample.get("request_saved"):
            try:
                task = clients.get_task(sample["task_id"])
            except clients.TaskNotFound:
                repo.event(run, "interrupted", f"样本 {sample['package_title']} 的执行记录暂未找到；以原任务键核对重试。")
        if task is None:
            model = run["private"]["model"]
            payload = {"task_id": sample["task_id"], "attempt_id": sample["id"] + "-a" + str(sample["attempt"]),
                "source_commit": sample["source_commit"], "artifact_digest": sample["artifact_digest"],
                "model_snapshot": {"model": model["model"], "base_url": model["base_url"],
                    "source": "evolution", "api_key_ref": f"experiment:{run['id']}:model"},
                "target": package["target"], "input": package["input"]}
            sample.update(status="running", request_saved=True)
            repo.put_run(run)
            if is_stopped(run["id"]) or run["status"] == "stopping":
                sample.update(status="pending", request_saved=False)
                repo.put_run(run)
                return
            try:
                task = clients.start_task(payload)
            except ValueError as exc:
                sample.update(status="failed", completed=False, generation_status="failed", error=str(exc))
                repo.put_run(run)
                return
        if not task.get("status"):
            return
        execution_phase = task.get("phase")
        if execution_phase and sample.get("execution_phase") != execution_phase:
            repo.event(run, "execution_progress", f"{sample['package_title']}：{execution_phase}")
        sample.update(trace_ids=task.get("trace_ids", []), checks=task.get("checks", {}), execution_phase=execution_phase,
                      artifacts=task.get("artifacts", {}), completed=bool(task.get("completed")),
                      usage={**sample.get("usage", {}), "generation": task.get("usage", {})})
        status = task["status"]
        if status in {"pending", "running", "recovering", "stopping"}:
            sample["status"] = "running"
            repo.put_run(run)
            return
        if status == "interrupted":
            sample.update(status="interrupted", error=task.get("error"))
            repo.put_run(run)
            return
        if status != "completed" or not task.get("completed"):
            sample.update(status="failed" if status != "stopped" else "stopped", completed=False,
                          generation_status="failed", error=task.get("error") or "任务未达到完整目标")
            repo.put_run(run)
            return
        if task.get("input_revision") != sample["input_revision"] or task.get("source_commit") != sample["source_commit"]:
            sample.update(status="failed", completed=False, generation_status="failed", error="实际运行版本与固定输入或候选不一致")
            repo.put_run(run)
            return
        if sample.get("artifact_digest") and task.get("artifact_digest") != sample["artifact_digest"]:
            sample.update(status="failed", completed=False, generation_status="failed", error="实际候选制品摘要不一致")
            repo.put_run(run)
            return
        sample["generation_status"] = "completed"
        repo.event(run, "generated", f"{sample['package_title']}：生成完成，开始评分。")
        repo.put_run(run)
    if sample.get("scores") is not None:
        sample["status"] = "completed"
        return
    if sample.get("scoring_attempts", 0) >= _SCORING_FAILURE_LIMIT:
        _stop_scoring_retries(run, sample)
        return
    sample["status"] = "scoring"
    repo.put_run(run)
    try:
        sample["scores"] = evaluation.score_sample(run, sample)
        sample.update(status="completed", error=None)
        sample.pop("score_retry_at", None)
    except Exception as exc:
        attempts = sample.get("scoring_attempts", 0) + 1
        sample["scoring_attempts"] = attempts
        if attempts >= _SCORING_FAILURE_LIMIT:
            _stop_scoring_retries(run, sample, exc)
            return
        delay = min(60, 5 * attempts)
        sample.update(status="evaluation_error", score_retry_at=time.time() + delay,
                      error=f"评测异常：{type(exc).__name__}：{str(exc)[:240]}；保留原产物，{delay} 秒后重试评分。")
        repo.event(run, "evaluation_error", f"样本 {sample['id']} 评测异常：{type(exc).__name__}；保留原产物，{delay} 秒后重试评分。")
    repo.put_run(run)


def optimization_evidence(run: dict) -> list[dict]:
    allowed = set(run["settings"]["optimization_ids"])
    phases = {"baseline", "exploration"}
    if run.get("flow_version") == 2:
        allowed.update(identifier for identifiers in run["settings"].get("regression_ids", {}).values() for identifier in identifiers)
        phases.add("regression")
    return [{key: copy.deepcopy(sample.get(key)) for key in
             ("package_id", "phase", "round", "arm", "completed", "checks", "scores", "error", "artifacts", "trace_ids")}
            for sample in run.get("samples", [])
            if sample.get("package_id") in allowed and sample.get("phase") in phases]


def _finish(run: dict, status: str, reason: str) -> None:
    run.update(status=status, end_reason=reason)
    repo.event(run, status, reason)
    repo.put_run(run)


def _run_pending(run: dict, samples: list[dict]) -> bool:
    pending = [sample for sample in samples if sample["status"] not in {"completed", "failed", "stopped"}]
    if not pending:
        return False
    # A judging outage must not keep other samples from generating.
    pending.sort(key=lambda sample: sample["status"] == "evaluation_error")
    ready = next((sample for sample in pending if sample.get("score_retry_at", 0) <= time.time()
                  or (sample.get("scores") is None and sample.get("scoring_attempts", 0) >= _SCORING_FAILURE_LIMIT)), None)
    if ready:
        process_sample(run, ready)
    return True


def _final_samples(run: dict, phase: str) -> list[dict]:
    return [sample for sample in run["samples"] if sample["phase"] == phase and sample["round"] == run["current_round"]]


def _start_checks(run: dict, phase: str, candidate: dict) -> None:
    selections = run["settings"]["regression_ids"].values() if phase == "regression" else [run["settings"]["validation_ids"]]
    for identifiers in selections:
        for arm, version in (("baseline", run["baseline"]), ("candidate", candidate)):
            _add_samples(run, identifiers, arm=arm, phase=phase, round_=run["current_round"], repeats=3,
                         commit=version["commit"], digest=version.get("digest") or version["artifact_digest"])
    run["phase"] = phase
    repo.event(run, phase, "优化达标，进入另外两类回归。" if phase == "regression" else "优化与回归已通过，固定候选进入独立终验。")


def _current_candidate(run: dict) -> dict:
    record = next(item for item in run["rounds"] if item["round"] == run["current_round"])
    report = record["report"]
    return {"commit": record["source_commit"], "digest": record["digest"], "round": record["round"],
            "quality": report["candidate"]["quality"], "completion_rate": report["candidate"]["completion_rate"]}


def _compare_final(run: dict, samples: list[dict], kind: str, gain: float) -> dict:
    left = [sample for sample in samples if sample["task_type"] == kind and sample["arm"] == "baseline"]
    right = [sample for sample in samples if sample["task_type"] == kind and sample["arm"] == "candidate"]
    report = evaluation.compare_results(left, right, completion_rate=run["settings"]["thresholds"]["completion_rate"], quality_gain=gain)
    identifiers = run["settings"]["validation_ids"] if kind == run["task_type"] else run["settings"]["regression_ids"][kind]
    expected = {(identifier, repeat) for identifier in identifiers for repeat in (1, 2, 3)}
    if any({(sample["package_id"], sample["repeat"]) for sample in arm} != expected or len(arm) != len(expected) for arm in (left, right)):
        report["passed"] = False
        report["problems"].append("缺少指定样本的新旧版各三次实际运行记录")
    return report


def advance_one(run_id: str) -> None:
    from app.experiments import agent, release
    run = repo.get_run(run_id)
    if run and release.needs_recovery(run):
        try:
            release.recover_run(run_id)
        except clients.ServiceUnavailable as exc:
            run["recovery_error"] = str(exc)
            repo.event(run, "recovering", str(exc))
            repo.put_run(run)
        return
    if not run or run["status"] not in ACTIVE:
        return
    if run["status"] == "stopping" or is_stopped(run_id):
        waiting = False
        for sample in run["samples"]:
            if sample["status"] in {"running", "interrupted"} and sample.get("request_saved"):
                try:
                    stopped = clients.stop_task(sample["task_id"])
                except clients.TaskNotFound:
                    sample.update(status="stopped", error="执行端未找到任务；任务未派发或未被接收，已停止。")
                    continue
                except clients.ServiceUnavailable:
                    waiting = True
                    continue
                if "usage" in stopped:
                    sample["usage"] = {**sample.get("usage", {}), "generation": stopped["usage"]}
                if stopped.get("status") not in {"stopped", "completed", "failed"}:
                    waiting = True
                    continue
                for field in ("trace_ids", "artifacts", "checks"):
                    if field in stopped:
                        sample[field] = stopped[field]
            if sample["status"] not in {"completed", "failed"}:
                sample["status"] = "stopped"
        if waiting:
            run["status"] = "stopping"
            repo.put_run(run)
            return
        _finish(run, "stopped", "用户已停止实验；保留已有内容。")
        return
    run["status"] = "running"
    run.pop("recovery_error", None)
    try:
        if run["phase"] == "baseline":
            if _run_pending(run, [sample for sample in run["samples"] if sample["phase"] == "baseline"]):
                return
            run["phase"] = "evolving"
            repo.event(run, "evolving", "原版评测完成，开始自动改进 Harness。")
        elif run["phase"] == "evolving":
            round_record = next((item for item in run["rounds"] if item["round"] == run["current_round"] and item["status"] == "evolving"), None)
            if round_record is None:
                if run["current_round"] >= run["max_rounds"]:
                    _finish(run, "exhausted", "最大改进轮数已用尽；未通过终验的候选不能发布。")
                    return
                run["current_round"] += 1
                base = run["best_candidate"]["commit"] if run["best_candidate"] else run["baseline"]["commit"]
                round_record = {"round": run["current_round"], "status": "evolving", "base_commit": base,
                                "source_commit": None, "summary": "", "error": None, "report": {}, "diff": ""}
                run["rounds"].append(round_record)
                repo.put_run(run)
            try:
                result = agent.improve_candidate(run, round_record, optimization_evidence(run))
                if is_stopped(run_id):
                    return
                round_record.update(status="exploration", source_commit=result["commit"], digest=result["digest"],
                                    summary=result["summary"], diff=result["diff"], usage=result.get("usage", {}))
                run["phase"] = "exploration"
                repo.event(run, "exploration", f"第 {run['current_round']} 轮候选已固定，开始优化复测。")
                _add_samples(run, run["settings"]["optimization_ids"], arm="candidate", phase="exploration",
                             round_=run["current_round"], repeats=1, commit=result["commit"], digest=result["digest"])
            except clients.ServiceUnavailable:
                raise
            except Exception as exc:
                round_record.update(status="failed", error=f"候选改进失败：{type(exc).__name__}：{str(exc)[:240]}")
                repo.event(run, "round_failed", f"第 {run['current_round']} 轮候选改进失败：{type(exc).__name__}；完整原因见轮次报告。")
        elif run["phase"] == "exploration":
            candidate = [sample for sample in run["samples"] if sample["phase"] == "exploration" and sample["round"] == run["current_round"]]
            if _run_pending(run, candidate):
                return
            baseline = [sample for sample in run["samples"] if sample["phase"] == "baseline"]
            thresholds = run["settings"]["thresholds"]
            report = evaluation.compare_results(baseline, candidate, completion_rate=thresholds["completion_rate"], quality_gain=thresholds["quality_gain"])
            round_record = run["rounds"][-1]
            round_record.update(report=report, status="passed" if report["passed"] else "not_improved")
            run["report"]["exploration"] = report
            run["report"]["exploration_round"] = run["current_round"]
            if report["passed"]:
                if run.get("flow_version") == 2:
                    _start_checks(run, "regression", _current_candidate(run))
                    round_record["status"] = "regression"
                    repo.put_run(run)
                    return
                run["best_candidate"] = {"commit": round_record["source_commit"], "digest": round_record["digest"],
                    "round": run["current_round"], "quality": report["candidate"]["quality"], "completion_rate": report["candidate"]["completion_rate"]}
                run["phase"] = "validation"
                selections = [(run["settings"]["validation_ids"], "validation")]
                selections += [(identifiers, "regression") for identifiers in run["settings"]["regression_ids"].values()]
                for identifiers, phase in selections:
                    for arm, version in (("baseline", run["baseline"]), ("candidate", run["best_candidate"])):
                        _add_samples(run, identifiers, arm=arm, phase=phase, round_=run["current_round"], repeats=3,
                                     commit=version["commit"], digest=version.get("digest") or version["artifact_digest"])
                repo.event(run, "validation", "探索达标；固定候选进入独立终验与另外两类回归。")
            else:
                run["phase"] = "evolving"
                repo.event(run, "not_improved", "优化复测未达门槛，按剩余轮数继续改版。")
        elif run["phase"] == "regression" and run.get("flow_version") == 2:
            selected = _final_samples(run, "regression")
            if _run_pending(run, selected):
                return
            thresholds = run["settings"]["thresholds"]
            reports = {}
            for kind in TASK_TYPES:
                if kind == run["task_type"]:
                    continue
                reports[kind] = _compare_final(run, selected, kind, -thresholds["regression_tolerance"][kind])
            run["report"].update(regressions=reports, regression_round=run["current_round"])
            round_record = run["rounds"][-1]
            round_record["regressions"] = reports
            if all(report["passed"] for report in reports.values()):
                run["best_candidate"] = _current_candidate(run)
                round_record["status"] = "validation"
                _start_checks(run, "validation", run["best_candidate"])
            else:
                round_record["status"] = "regression_failed"
                run["phase"] = "evolving"
                repo.event(run, "regression_failed", "回归未达门槛，保留失败证据，按剩余轮数继续改版。")
        elif run["phase"] == "validation":
            if run.get("flow_version") == 2:
                selected = _final_samples(run, "validation")
                if _run_pending(run, selected):
                    return
                thresholds = run["settings"]["thresholds"]
                report = _compare_final(run, selected, run["task_type"], thresholds["quality_gain"])
                regressions = run["report"].get("regressions", {})
                passed = report["passed"] and run["report"].get("regression_round") == run["current_round"] and all(
                    regressions.get(kind, {}).get("passed", False) for kind in TASK_TYPES if kind != run["task_type"])
                run["report"].update(passed=passed, validation=report, validation_round=run["current_round"])
                run["rounds"][-1]["status"] = "passed" if passed else "validation_failed"
                run["phase"] = "review"
                _finish(run, "awaiting_review" if passed else "validation_failed",
                        "优化、回归与独立终验已通过，等待人工决定发布。" if passed else "独立终验未达门槛；本次实验结束，不继续使用终验反馈改版。")
                return
            selected = [sample for sample in run["samples"] if sample["phase"] in {"validation", "regression"}]
            if _run_pending(run, selected):
                return
            reports = {}
            thresholds = run["settings"]["thresholds"]
            for kind in TASK_TYPES:
                left = [sample for sample in selected if sample["task_type"] == kind and sample["arm"] == "baseline"]
                right = [sample for sample in selected if sample["task_type"] == kind and sample["arm"] == "candidate"]
                gain = thresholds["quality_gain"] if kind == run["task_type"] else -thresholds["regression_tolerance"][kind]
                reports[kind] = evaluation.compare_results(left, right, completion_rate=thresholds["completion_rate"], quality_gain=gain)
            passed = all(report["passed"] for report in reports.values())
            run["report"].update(passed=passed, validation=reports[run["task_type"]],
                                 regressions={kind: report for kind, report in reports.items() if kind != run["task_type"]})
            run["phase"] = "review"
            _finish(run, "awaiting_review" if passed else "validation_failed",
                    "终验与回归通过，等待人工决定发布。" if passed else "终验或回归未达门槛；本次实验结束。")
            return
        repo.put_run(run)
    except clients.ServiceUnavailable as exc:
        run.update(status="recovering", recovery_error=str(exc))
        repo.event(run, "recovering", str(exc))
        repo.put_run(run)


def request_stop(run_id: str) -> dict:
    with _guard:
        _stops.setdefault(run_id, threading.Event()).set()
    with repo.run_lock(run_id):
        run = repo.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] in ACTIVE:
            run["status"] = "stopping"
            repo.event(run, "stop_requested", "已请求停止，不再派发新工作。")
            repo.put_run(run)
    start_worker(run_id)
    return run


def _worker(run_id: str) -> None:
    from app.experiments import release
    try:
        while not _shutdown.is_set():
            run = repo.get_run(run_id)
            if not run or (run["status"] not in ACTIVE and not release.needs_recovery(run)):
                break
            try:
                advance_one(run_id)
            except asyncio.CancelledError:
                # Source-tool cancellation is acknowledged by the next stop pass.
                continue
            except Exception as exc:
                current = repo.get_run(run_id)
                if current:
                    _finish(current, "failed", f"无法继续核对实验状态：{type(exc).__name__}；已有现场保留。")
            _shutdown.wait(2)
    finally:
        with _guard:
            _workers.pop(run_id, None)


def start_worker(run_id: str) -> None:
    with _guard:
        if run_id in _workers:
            return
        thread = threading.Thread(target=_worker, args=(run_id,), daemon=True, name="experiment-" + run_id)
        _workers[run_id] = thread
        thread.start()


def recover_runs() -> None:
    from app.experiments import release
    for run in repo.list_runs():
        if release.needs_recovery(run):
            repo.event(run, "recovering", "服务已恢复，先核对本次发布或恢复是否已实际生效。")
            repo.put_run(run)
            start_worker(run["id"])
        elif run["status"] in ACTIVE:
            if run["status"] != "stopping":
                run["status"] = "recovering"
                repo.event(run, "recovering", "服务已恢复，先核对已保存的任务和评分。")
                repo.put_run(run)
            start_worker(run["id"])


def startup(trace_recorder=None) -> None:
    global _recorder
    _recorder = trace_recorder
    _shutdown.clear()
    recover_runs()


def shutdown() -> None:
    _shutdown.set()
