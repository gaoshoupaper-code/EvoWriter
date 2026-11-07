"""Frozen external judging and comparisons with an explicit completion denominator."""
from __future__ import annotations

import json
import asyncio
import math
import time
from uuid import uuid4

import httpx
from contracts.trace.usage import normalize_usage

from app.common.model_factory import _model_family
from app.experiments import repo


def require_heterogeneous_judges(writer_model: str, judges: list[dict]) -> None:
    if not judges:
        raise ValueError("请配置异源评委组")
    family = _model_family(writer_model)
    for judge in judges:
        if _model_family(judge["model"]) == family:
            raise ValueError(f"评委模型 {judge['model']} 与运行模型同家族；必须配置异源评委")


def _stats(samples: list[dict]) -> dict:
    completed = sum(bool(sample.get("completed")) for sample in samples)
    values = [sample["scores"]["overall"] for sample in samples
              if sample.get("completed") and sample.get("scores") is not None]
    return {"total": len(samples), "completed": completed,
            "completion_rate": completed / len(samples) if samples else 0,
            "quality": sum(values) / len(values) if values else None,
            "evaluation_errors": sum(bool(sample.get("completed")) and sample.get("scores") is None for sample in samples)}


def compare_results(baseline: list[dict], candidate: list[dict], *, completion_rate: float, quality_gain: float) -> dict:
    left, right = _stats(baseline), _stats(candidate)
    old = {(sample["package_id"], sample.get("repeat", 1)): sample for sample in baseline}
    new = {(sample["package_id"], sample.get("repeat", 1)): sample for sample in candidate}
    changes = []
    new_failures = []
    paired = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        a = before.get("scores", {}).get("overall") if before and before.get("completed") and before.get("scores") else None
        b = after.get("scores", {}).get("overall") if after and after.get("completed") and after.get("scores") else None
        delta = b - a if a is not None and b is not None else None
        if delta is not None:
            paired.append(delta)
        if before and before.get("completed") and (not after or not after.get("completed")):
            new_failures.append(key[0])
        changes.append({"package_id": key[0], "repeat": key[1], "baseline": a, "candidate": b, "delta": delta})
    delta = sum(paired) / len(paired) if paired else None
    problems = []
    if not candidate or right["completion_rate"] < completion_rate:
        problems.append("完成率未达到门槛")
    if right["evaluation_errors"] or left["evaluation_errors"]:
        problems.append("存在缺少有效评分的已完成样本")
    if old.keys() != new.keys():
        problems.append("新旧版本样本或重复次数不完整")
    if delta is None or delta + 1e-9 < quality_gain:
        problems.append("可比较样本质量涨幅未达到门槛")
    return {"passed": not problems, "baseline": left, "candidate": right, "paired_count": len(paired),
            "quality_delta": delta, "new_failures": sorted(set(new_failures)), "changes": changes, "problems": problems}


def _judgement(raw: str, dimension: str) -> dict:
    from app.benchmark.scorer import _parse_response, _validate_dim_judgement
    value = _parse_response(raw)
    _validate_dim_judgement(value, dimension)
    if value["score"] == 0:
        raise ValueError("评委证据不足，不能作为有效质量评分")
    return value


def score_sample(run: dict, sample: dict) -> dict:
    package = run["private"]["packages"][sample["package_id"]]
    rule = run["private"]["rules"][sample["task_type"]]
    judges = run["private"]["judges"]
    progress = sample.setdefault("judge_results", {})
    errors = sample.setdefault("judge_errors", {})
    from app.experiments import runner
    recorder = runner.recorder()
    trace_id = None
    if recorder is not None:
        handle = recorder.create_run(sample["id"], "evolution_eval", endpoint="experiments/score", external_refs={"experiment_id": run["id"], "sample_id": sample["id"], "purpose": sample["phase"]})
        trace_id = handle.trace_id
        sample.setdefault("evaluation_trace_ids", []).append(trace_id)
        repo.put_run(run)
    try:
        for judge in judges:
            for dimension in rule["dimensions"]:
                key = str(judge["id"]) + ":" + dimension["key"]
                if key in progress:
                    continue
                if runner.is_stopped(run["id"]):
                    raise asyncio.CancelledError()
                credentials = repo.decrypt_credentials(judge)
                messages = [
                    {"role": "system", "content": "你是独立质量评委。只评当前维度，只依据资料与产物中的具体证据。\n" + "\n".join(rule["discipline_rules"]) + "\n" + json.dumps(dimension, ensure_ascii=False) + '\n只输出JSON：{"score":1到5的整数,"达标":["引用原文的证据"],"不足":["引用原文的缺陷"]}。'},
                    {"role": "user", "content": json.dumps({"target": package["target"], "input": package["input"], "artifacts": sample["artifacts"], "checks": sample.get("checks", {})}, ensure_ascii=False)},
                ]
                last_error = None
                for attempt in range(2):
                    if attempt and runner.wait_for_stop(run["id"], 2):
                        raise asyncio.CancelledError()
                    if runner.is_stopped(run["id"]):
                        raise asyncio.CancelledError()
                    call_id = "judge-" + uuid4().hex
                    started = time.monotonic()
                    sample.setdefault("usage", {}).setdefault("judge_calls", 0)
                    sample["usage"]["judge_calls"] += 1
                    call_record = {"judge_id": judge["id"], "dimension": dimension["key"], "usage": None, "status": "failed"}
                    sample["usage"].setdefault("judge_attempts", []).append(call_record)
                    received = False
                    if trace_id:
                        recorder.append_event(trace_id, {"type": "llm_start", "status": "running", "source": "runtime",
                            "run_id": call_id, "agent_name": "experiment-judge", "node_name": dimension["key"], "model_name": judge["model"], "input": messages})
                    try:
                        with httpx.Client(timeout=60) as client:
                            response = client.post(credentials["base_url"].rstrip("/") + "/chat/completions",
                                headers={"Authorization": "Bearer " + credentials["api_key"]},
                                json={"model": credentials["model"].split(":", 1)[-1], "temperature": 0, "messages": messages})
                        if not response.is_success:
                            raise RuntimeError(f"评委调用失败：HTTP {response.status_code}")
                        data = response.json()
                        raw = data["choices"][0]["message"]["content"]
                        tokens = data.get("usage")
                        call_record["usage"] = tokens
                        received = True
                        if trace_id:
                            recorder.append_event(trace_id, {"type": "llm_end", "status": "completed", "source": "runtime",
                                "run_id": call_id, "agent_name": "experiment-judge", "node_name": dimension["key"], "model_name": judge["model"],
                                "output": raw, "usage": normalize_usage(tokens), "duration_ms": int((time.monotonic() - started) * 1000)})
                        progress[key] = _judgement(raw, dimension["key"])
                        call_record["status"] = "completed"
                        errors.pop(key, None)
                        repo.put_run(run)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = f"{dimension['key']}评测异常：{type(exc).__name__}：{str(exc)[:200]}"
                        if trace_id and not received:
                            recorder.append_event(trace_id, {"type": "llm_error", "status": "failed", "source": "runtime",
                                "run_id": call_id, "agent_name": "experiment-judge", "model_name": judge["model"], "error": last_error})
                        repo.put_run(run)
                if last_error:
                    errors[key] = last_error
        dimensions = {}
        reasons = {}
        quorum = 2 if len(judges) >= 2 else 1
        for dimension in rule["dimensions"]:
            available = [(judge, progress[str(judge["id"]) + ":" + dimension["key"]]) for judge in judges if str(judge["id"]) + ":" + dimension["key"] in progress]
            if len(available) < quorum:
                details = "；".join(errors.get(str(judge["id"]) + ":" + dimension["key"], "") for judge in judges)
                raise RuntimeError(f"{dimension['key']}有效评委数不足：{details}；保留产物等待恢复评分")
            values = [value for judge, value in available]
            dimensions[dimension["key"]] = sum(value["score"] for value in values) / len(values)
            reasons[dimension["key"]] = [{"judge_id": judge["id"], **value} for judge, value in available]
            reasons[dimension["key"]].extend({"judge_id": judge["id"], "score": None,
                "error": errors.get(str(judge["id"]) + ":" + dimension["key"])} for judge in judges if str(judge["id"]) + ":" + dimension["key"] not in progress)
        total_weight = sum(dimension["weight"] for dimension in rule["dimensions"])
        overall = sum(dimensions[dimension["key"]] * dimension["weight"] for dimension in rule["dimensions"]) / total_weight
        if not math.isfinite(overall):
            raise ValueError("无效评分")
        if trace_id:
            recorder.complete_run(trace_id)
        return {"overall": overall, "dimensions": dimensions, "reasons": reasons, "rule_id": rule["id"]}
    except asyncio.CancelledError:
        if trace_id:
            recorder.cancel_run(trace_id, reason="user_stop")
        raise
    except Exception:
        if trace_id:
            recorder.fail_run(trace_id, "评委调用或评分校验失败")
        raise
