"""Frozen external judging and comparisons with an explicit completion denominator."""
from __future__ import annotations

import json
import asyncio
import math
import re
import time
from itertools import product
from uuid import uuid4

import httpx
from contracts.trace.usage import normalize_usage
from contracts.target_tasks import configured_task_type
from contracts.writing_artifacts import KIND_CHAPTER, KIND_TEXT, artifact_relpath, parse_relpath

from app.common.model_factory import _model_family
from app.experiments import live_output, repo


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
    if (not candidate or right["completion_rate"] < completion_rate
            or (configured_task_type() and left["completion_rate"] < completion_rate)):
        problems.append("完成率未达到门槛")
    if right["evaluation_errors"] or left["evaluation_errors"]:
        problems.append("存在缺少有效评分的已完成样本")
    if old.keys() != new.keys():
        problems.append("新旧版本样本或重复次数不完整")
    if delta is None or delta + 1e-9 < quality_gain:
        problems.append("可比较样本质量涨幅未达到门槛")
    return {"passed": not problems, "baseline": left, "candidate": right, "paired_count": len(paired),
            "quality_delta": delta, "new_failures": sorted(set(new_failures)), "changes": changes, "problems": problems}


_EVIDENCE_QUOTES = str.maketrans("“”「」", '""""')


class _InvalidEvidence(ValueError):
    def __init__(self, details: list[dict]) -> None:
        super().__init__("评分依据须对应正文原句或有效位置")
        self.details = details


def _evidence_issue(citation: str, sources: dict[str, str]) -> str | None:
    citation = citation.strip().strip('“”「」"').strip()
    path, colon, line = citation.rpartition(":")
    if colon and (path in sources or path.endswith(".md")):
        if path not in sources:
            return "文件路径不存在"
        if not re.fullmatch(r"[1-9][0-9]*", line):
            return "行号须为正整数"
        line_count = len(sources[path].splitlines())
        if len(line) > len(str(line_count)) or int(line) > line_count:
            return "行号超出文件范围"
        return None
    if citation and any(citation in text for text in sources.values()):
        return None
    normalized = citation.translate(_EVIDENCE_QUOTES)
    if normalized and any(normalized in text.translate(_EVIDENCE_QUOTES) for text in sources.values()):
        return None
    return "找不到连续原文"


def _judgement(raw: str, dimension: str, *, material: dict | None = None) -> dict:
    from app.benchmark.scorer import _parse_response, _validate_dim_judgement
    try:
        value = _parse_response(raw)
    except (AttributeError, ValueError) as exc:
        raise ValueError("评委回复不是评分 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("评委回复必须是评分对象")
    if material is not None:
        score, reason, evidence = (value.get(field) for field in ("score", "reason", "evidence"))
        if type(score) is not int or not 1 <= score <= 5:
            raise ValueError("分数须为 1～5 的整数")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("缺少定档理由")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(line, str) or not line.strip() for line in evidence):
            raise ValueError("缺少可核对的评分依据")
        manuscript = material["artifacts"]
        sources = {**material["input"]["files"], **manuscript}
        issues = [
            {"index": index, "citation": item[:160], "reason": issue}
            for index, item in enumerate(evidence, 1)
            if (issue := _evidence_issue(item, sources)) is not None
        ]
        if not any(_evidence_issue(item, manuscript) is None for item in evidence):
            issues.append({"reason": "缺少本章最终正文依据"})
        if issues:
            raise _InvalidEvidence(issues)
        return {"score": score, "reason": " ".join(reason.splitlines()).strip(),
                "evidence": [line.strip() for line in evidence]}
    _validate_dim_judgement(value, dimension)
    if value["score"] == 0:
        raise ValueError("评委证据不足，不能作为有效质量评分")
    return value


def _score_key(judge: dict, dimension: str, repeat: int | None) -> str:
    return f"{judge['id']}:{dimension}" if repeat is None else f"{judge['id']}:{repeat}:{dimension}"


def score_progress(run: dict, sample: dict) -> dict:
    repeats = run.get("judge_repeats")
    repeat_numbers = range(1, repeats + 1) if repeats is not None else (None,)
    judges = run["private"]["judges"]
    keys = [dimension["key"] for dimension in run["private"]["rules"][sample["task_type"]]["dimensions"]]
    progress = sample.get("judge_results", {})
    errors = sample.get("judge_errors", {})
    reasons: dict[str, list[dict]] = {key: [] for key in keys}
    completed = 0
    for judge, repeat in product(judges, repeat_numbers):
        identity = {"judge_id": judge["id"]}
        if repeat is not None:
            identity["repeat"] = repeat
        complete = True
        for dimension in keys:
            key = _score_key(judge, dimension, repeat)
            if key in progress:
                reasons[dimension].append({**identity, **progress[key]})
            else:
                complete = False
                if key in errors:
                    reasons[dimension].append({**identity, "score": None, "error": errors[key]})
        completed += complete
    return {"completed": completed, "total": len(judges) * len(repeat_numbers), "reasons": reasons}


def _chapter_score_material(package: dict, sample: dict) -> dict:
    chapter = package["target"]["chapter"]
    manuscript = artifact_relpath(KIND_TEXT, chapter=chapter)
    if not sample["artifacts"].get(manuscript, "").strip():
        raise ValueError("缺少本次生成的最终正文")
    files = {}
    for path, content in package["input"]["files"].items():
        ref = parse_relpath(path)
        if ref and (ref.chapter is None or ref.chapter < chapter or (ref.kind == KIND_CHAPTER and ref.chapter == chapter)):
            files[path] = content
    return {"target": package["target"], "input": {"files": files},
            "artifacts": {manuscript: sample["artifacts"][manuscript]}}


def _judge_json(response: httpx.Response) -> tuple[str, dict | None]:
    if not response.is_success:
        raise RuntimeError(f"评委调用失败：HTTP {response.status_code}")
    data = response.json()
    return data["choices"][0]["message"]["content"], data.get("usage")


def _stream_rejected(response: httpx.Response) -> bool:
    if response.status_code == 501:
        return True
    if response.status_code not in (400, 422):
        return False
    response.read()
    detail = response.text.lower()
    return "stream" in detail and any(word in detail for word in (
        "unsupported", "not support", "unknown", "unrecognized", "extra_forbidden", "不支持", "未知参数",
    ))


def _judge_chat(client, url, headers, request, trace_id, call_id, stopped):
    if not trace_id:
        return _judge_json(client.post(url, headers=headers, json=request))
    try:
        with client.stream("POST", url, headers=headers, json={
            **request, "stream": True, "stream_options": {"include_usage": True},
        }) as response:
            if not _stream_rejected(response):
                if not response.is_success:
                    raise RuntimeError(f"评委调用失败：HTTP {response.status_code}")
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    response.read()
                    return _judge_json(response)
                return _read_judge_stream(response, trace_id, call_id, stopped)
        if stopped():
            raise asyncio.CancelledError()
        # Older compatible gateways can reject streaming before generating any output.
        return _judge_json(client.post(url, headers=headers, json=request))
    finally:
        live_output.finish_call(trace_id, call_id)


def _read_judge_stream(response, trace_id, call_id, stopped):
    parts: list[str] = []
    tokens = None
    complete = False
    for line in response.iter_lines():
        if stopped():
            raise asyncio.CancelledError()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            complete = True
            break
        if not data:
            continue
        chunk = json.loads(data)
        if chunk.get("error"):
            raise RuntimeError("评委流式响应失败")
        if chunk.get("usage") is not None:
            tokens = chunk["usage"]
        for choice in chunk.get("choices") or []:
            complete = complete or bool(choice.get("finish_reason"))
            delta = choice.get("delta") or {}
            thought = delta.get("reasoning_content")
            if isinstance(thought, str) and thought:
                live_output.publish_delta(trace_id, call_id, "reasoning", thought)
            text = delta.get("content")
            if isinstance(text, str) and text:
                parts.append(text)
                live_output.publish_delta(trace_id, call_id, "text", text)
    if not complete:
        raise RuntimeError("评委流式响应提前中断")
    return "".join(parts), tokens


def score_sample(run: dict, sample: dict) -> dict:
    repeats = run.get("judge_repeats")
    package = (repo.run_package(run, sample["package_id"], for_scoring=True) if repeats is not None
               else repo.run_package(run, sample["package_id"]))
    rule = run["private"]["rules"][sample["task_type"]]
    judges = run["private"]["judges"]
    material = (_chapter_score_material(package, sample) if repeats is not None else
                {"target": package["target"], "input": package["input"], "artifacts": sample["artifacts"], "checks": sample.get("checks", {})})
    judge_material = material
    if repeats is not None:
        judge_material = {**material, "artifacts": {
            path: "\n".join(f"{number}: {line}" for number, line in enumerate(text.splitlines(), 1))
            for path, text in material["artifacts"].items()
        }}
    user_message = {"role": "user", "content": json.dumps(judge_material, ensure_ascii=False)}
    output_format = ('只输出 JSON：{"score": 1到5的整数, "reason": "一段定档理由，说明为何符合本档和相邻档差异", '
                     '"evidence": ["逐字引用正文短句，或填写正文文件路径:行号"]}。'
                     '正文每行开头的编号是行号，逐字引用时复制编号后的原文。'
                     '每项至少给一条正文依据。判定漏写时可另逐字引用章纲要求。'
                     if repeats is not None else
                     '只输出JSON：{"score":1到5的整数,"达标":["引用原文的证据"],"不足":["引用原文的缺陷"]}。')
    progress = sample.setdefault("judge_results", {})
    errors = sample.setdefault("judge_errors", {})
    feedback = sample.get("judge_evidence_feedback", {})
    from app.experiments import runner
    recorder = runner.recorder()
    trace_id = None
    if recorder is not None:
        trace_ids = sample.setdefault("evaluation_trace_ids", [])
        if trace_ids and not recorder.is_terminal(trace_ids[-1]):
            trace_id = trace_ids[-1]
        else:
            handle = recorder.create_run(sample["id"], "evolution_eval", endpoint="experiments/score", external_refs={"experiment_id": run["id"], "sample_id": sample["id"], "purpose": sample["phase"]})
            trace_id = handle.trace_id
            trace_ids.append(trace_id)
            repo.put_run(run)
    try:
        repeat_numbers = range(1, repeats + 1) if repeats is not None else (None,)
        for judge, repeat in product(judges, repeat_numbers):
            for dimension in rule["dimensions"]:
                key = _score_key(judge, dimension["key"], repeat)
                if key in progress:
                    continue
                if runner.is_stopped(run["id"]):
                    raise asyncio.CancelledError()
                try:
                    credentials = repo.decrypt_credentials(judge)
                except Exception as exc:
                    errors[key] = f"{dimension['key']}评测异常：{type(exc).__name__}：无法读取评委配置"
                    repo.put_run(run)
                    continue
                messages = [
                    {"role": "system", "content": "你是独立质量评委。只评当前维度，只依据资料与产物中的具体证据。\n" +
                     "\n".join(rule["discipline_rules"]) + "\n" + json.dumps(dimension, ensure_ascii=False) + "\n" + output_format},
                    user_message,
                ]
                if key in feedback:
                    messages.append({"role": "user", "content": json.dumps({
                        "previous_evidence_errors": feedback[key],
                        "instruction": "以下引用片段用于定位此前未通过的依据。请核对原材料，修正依据后重新提交完整评分 JSON。",
                    }, ensure_ascii=False)})
                node_name = dimension["key"] if repeat is None else f"第 {repeat} 次 · {dimension['key']}"
                call = {"source": "runtime", "run_id": "judge-" + uuid4().hex, "agent_name": "experiment-judge",
                        "node_name": node_name, "model_name": judge["model"]}
                started = time.monotonic()
                call_record = {"judge_id": judge["id"], "dimension": dimension["key"], "usage": None, "status": "failed"}
                if repeat is not None:
                    call_record["repeat"] = repeat
                sample.setdefault("usage", {}).setdefault("judge_calls", 0)
                sample["usage"]["judge_calls"] += 1
                sample["usage"].setdefault("judge_attempts", []).append(call_record)
                if trace_id:
                    recorder.append_event(trace_id, {**call, "type": "llm_start", "status": "running", "input": messages})
                raw = None
                try:
                    # Use the same generation window as the benchmark scorer for full manuscripts.
                    with httpx.Client(timeout=httpx.Timeout(300, connect=15)) as client:
                        raw, tokens = _judge_chat(client, credentials["base_url"].rstrip("/") + "/chat/completions",
                            {"Authorization": "Bearer " + credentials["api_key"]},
                            {"model": credentials["model"].split(":", 1)[-1], "temperature": 0, "messages": messages},
                            trace_id, call["run_id"], lambda: runner.is_stopped(run["id"]))
                    call_record["usage"] = normalize_usage(tokens) if repeat is not None and tokens is not None else tokens
                    progress[key] = _judgement(raw, dimension["key"], material=material if repeat is not None else None)
                except Exception as exc:
                    error_type = type(exc).__name__
                    if isinstance(exc, _InvalidEvidence):
                        feedback[key] = exc.details
                        sample["judge_evidence_feedback"] = feedback
                        error_type = "ValueError"
                    errors[key] = f"{dimension['key']}评测异常：{error_type}：{str(exc)[:200]}"
                    event = {"type": "llm_error", "status": "failed", "error": errors[key]}
                else:
                    call_record["status"] = "completed"
                    errors.pop(key, None)
                    feedback.pop(key, None)
                    event = {"type": "llm_end", "status": "completed"}
                repo.put_run(run)
                if trace_id:
                    recorder.append_event(trace_id, {**call, **event, "output": raw, "usage": normalize_usage(call_record["usage"]),
                        "duration_ms": int((time.monotonic() - started) * 1000)})
        if repeats is not None:
            grading = score_progress(run, sample)
            if grading["completed"] != grading["total"]:
                detail = next(iter(errors.values()), "缺少有效评分")
                raise RuntimeError(f"有效评分 {grading['completed']}/{grading['total']} 份。{detail}")
            reasons = grading["reasons"]
            dimensions = {key: sum(value["score"] for value in values) / len(values) for key, values in reasons.items()}
        else:
            if errors and sample.get("scoring_attempts", 0) + 1 < runner._SCORING_FAILURE_LIMIT:
                raise RuntimeError(next(iter(errors.values())))
            dimensions = {}
            reasons = {}
            quorum = 2 if len(judges) >= 2 else 1
            for dimension in rule["dimensions"]:
                key = dimension["key"]
                available = [(judge, progress[_score_key(judge, key, None)]) for judge in judges if _score_key(judge, key, None) in progress]
                if len(available) < quorum:
                    details = "；".join(errors.get(_score_key(judge, key, None), "") for judge in judges)
                    raise RuntimeError(f"{key}有效评委数不足：{details}；保留产物等待恢复评分")
                dimensions[key] = sum(value["score"] for _, value in available) / len(available)
                reasons[key] = [{"judge_id": judge["id"], **value} for judge, value in available]
                reasons[key].extend({"judge_id": judge["id"], "score": None,
                    "error": errors.get(_score_key(judge, key, None))} for judge in judges if _score_key(judge, key, None) not in progress)
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
