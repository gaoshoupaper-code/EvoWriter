"""Repeated chapter judging preserves completed evidence and gates comparisons."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from threading import Event
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.core.notify_auth import NotifyTokenMiddleware
from app.experiments import api, evaluation, live_output, repo, rubrics, runner
from app.ingestion import ingestion
from app.trace import recorder as trace_recorder
from app.view import traces
from test_experiments import run_record


@pytest.fixture
def chapter_run(monkeypatch, tmp_path):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setattr(db.settings, "evolution_db", str(tmp_path / "evolution.db"))
    monkeypatch.setattr(db.settings, "trace_payload_dir", str(tmp_path / "payloads"))
    monkeypatch.setattr(trace_recorder, "_WAL_ROOT", tmp_path / "wal")
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", connection)
    monkeypatch.setattr(runner, "_recorder", None)
    db.init_db()
    repo.init_schema()
    rule = rubrics.list_rules("chapter_text")[0]
    case = next(item for item in repo.list_packages() if item["purpose"] == "optimization")
    chapter = case["target"]["chapter"]
    manuscript = f"正文/第{chapter:03d}章.md"
    sample = {"id": "original", "package_id": case["id"], "package_title": case["title"],
              "phase": "baseline", "arm": "baseline", "round": 0, "repeat": 1,
              "status": "scoring", "task_type": "chapter_text", "source_commit": "a" * 40,
              "completed": True, "generation_status": "completed", "scores": None,
              "artifacts": {manuscript: "她在门边停下，听清脚步后才出门。", "人物/主角.md": "本次生成的人物卡"},
              "target": case["target"]}
    run = run_record()
    run.update(task_type="chapter_text", status="running", flow_version=3, assessment_version=2,
               phase="baseline", current_round=0, judge_repeats=3, samples=[sample], report={}, best_candidate=None,
               private={"rules": {"chapter_text": rule}, "package_revisions": {case["id"]: case["input_revision"]},
                        "judges": [{"id": 1, "name": "评委甲", "model": "judge-a"},
                                   {"id": 2, "name": "评委乙", "model": "judge-b"}]})
    run["settings"].update(optimization_ids=[case["id"]], rule_versions={"chapter_text": rule["id"]})
    repo.put_run(run)
    try:
        yield run, sample
    finally:
        connection.close()


def stub_judges(monkeypatch, *, invalid_at=(), reject_after=None, http_error_at=(), evidence_at=None, runtime_ref=None):
    calls = []

    class JudgeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, _url, *, headers, json):
            calls.append(json)
            index = len(calls)
            if reject_after is not None and index > reject_after:
                raise httpx.ReadTimeout("评委连接超时")
            if index in http_error_at:
                return httpx.Response(503)
            judgement = {"score": 4, "reason": "专用评语，说明原文何以进入该档。",
                         "evidence": ["她在门边停下"], "达标": ["她在门边停下"], "不足": ["局部交代偏少"]}
            if index in invalid_at:
                judgement.pop("reason")
            if evidence_at and index in evidence_at:
                judgement["evidence"] = evidence_at[index]
            if runtime_ref and "runtime_trace" in json["messages"][1]["content"]:
                judgement["evidence"] = [runtime_ref]
            return httpx.Response(200, json={**make_completion(judgement), "usage": {
                "prompt_tokens": 3, "completion_tokens": 4, "provider_detail": "专用评语 与 她在门边停下",
            }})

        @contextmanager
        def stream(self, _method, url, **kwargs):
            yield self.post(url, **kwargs)

    monkeypatch.setattr(evaluation.httpx, "Client", JudgeClient)
    monkeypatch.setattr(repo, "decrypt_credentials", lambda judge: {
        "base_url": "https://judge.invalid", "api_key": "test-secret", "model": judge["model"],
    })
    return calls


def _runtime_input():
    return {"tools": {"successes": 4, "total": 5}, "tokens": 100_000,
            "summary": {"skill_status": "read", "omitted": {}, "evidence": [
                {"ref": "trace-body:8", "kind": "review", "text": "已按 Skill 检查叙事视角"}]}}


def _quality_dimension_count(run):
    return len(run["private"]["rules"]["chapter_text"]["dimensions"])


def test_runtime_is_independent_of_six_quality_dimensions(chapter_run, monkeypatch):
    run, sample = chapter_run
    run["runtime_scoring_version"] = 1
    calls = stub_judges(monkeypatch, runtime_ref="trace-body:8")
    monkeypatch.setattr(evaluation, "_collect_runtime", lambda _: _runtime_input(), raising=False)

    result = evaluation.score_sample(run, sample)

    assert len(calls) == 42
    runtime_calls = 0
    for call in calls:
        instructions = call["messages"][0]["content"]
        material = json.loads(call["messages"][1]["content"])
        if "runtime_trace" in material:
            runtime_calls += 1
            assert "运行时" in instructions
            assert "focus_points" not in instructions
            assert material["runtime_trace"]["evidence"][0]["ref"] == "trace-body:8"
        else:
            assert '"key": "运行时"' not in instructions
            assert "先按 focus_points（关注点）逐项核对" in instructions
            assert instructions.index('"focus_points"') < instructions.index('"anchors"')
    assert runtime_calls == 6
    assert set(result["dimensions"]) == {
        "事件因果逻辑", "人物行为逻辑", "章纲兑现", "语言清晰度", "人物语言", "场景空间", "运行时",
    }
    assert result["quality"] == 4
    assert result["runtime"]["tools"]["score"] == pytest.approx(4.2)
    assert result["runtime"]["tokens"]["score"] == 3
    assert result["runtime"]["skill"]["score"] == 4
    assert result["dimensions"]["运行时"] == pytest.approx((4.2 + 3 + 4) / 3)
    assert result["overall"] == pytest.approx(3.92)
    assert result["reasons"]["运行时"][0]["evidence_details"] == [
        {"ref": "trace-body:8", "text": "已按 Skill 检查叙事视角"}]


def test_earlier_rule_without_focus_points_still_uses_its_frozen_anchors(chapter_run, monkeypatch):
    run, sample = chapter_run
    run["judge_repeats"] = 1
    run["private"]["judges"] = run["private"]["judges"][:1]
    frozen = run["private"]["rules"]["chapter_text"]
    frozen["dimensions"] = frozen["dimensions"][:4]
    dimension = frozen["dimensions"][0]
    dimension.pop("focus_points")
    calls = stub_judges(monkeypatch)

    evaluation.score_sample(run, sample)

    instructions = calls[0]["messages"][0]["content"]
    assert "focus_points" not in instructions
    assert json.dumps(dimension["anchors"], ensure_ascii=False) in instructions


def test_runtime_missing_evidence_retries_without_rescoring_content(chapter_run, monkeypatch):
    run, sample = chapter_run
    quality_count = _quality_dimension_count(run)
    run["runtime_scoring_version"] = 1
    run["judge_repeats"] = 1
    run["private"]["judges"] = run["private"]["judges"][:1]
    calls = stub_judges(monkeypatch, runtime_ref="trace-body:8")
    runtime_ready = False

    def collect(_):
        if not runtime_ready:
            raise ValueError("运行 Trace 尚未校验")
        return _runtime_input()

    monkeypatch.setattr(evaluation, "_collect_runtime", collect, raising=False)
    runner.process_sample(run, sample)
    assert sample["status"] == "evaluation_error"
    assert sample["generation_status"] == "completed"
    assert len(sample["judge_results"]) == len(calls) == quality_count
    assert sample.get("scores") is None

    runtime_ready = True
    runner.process_sample(run, sample)
    assert sample["status"] == "completed"
    assert len(sample["judge_results"]) == len(calls) == quality_count + 1
    assert sample["scores"]["overall"] == pytest.approx(3.92)


def test_runtime_repeats_and_retry_reuse_the_first_prompt_after_reload(chapter_run, monkeypatch):
    run, sample = chapter_run
    run.update(runtime_scoring_version=1, judge_repeats=2)
    run["private"]["judges"] = run["private"]["judges"][:1]
    stub_judges(monkeypatch)
    runtime_messages = []
    monkeypatch.setattr(evaluation, "_collect_runtime", lambda _: _runtime_input())

    def send(_credentials, messages, *_):
        if "runtime_trace" in json.loads(messages[1]["content"]):
            runtime_messages.append(messages)
            evidence = ["missing:99"] if len(runtime_messages) == 1 else ["trace-body:8"]
        else:
            evidence = ["她在门边停下"]
        return json.dumps({"score": 4, "reason": "按证据评分", "evidence": evidence}), None

    monkeypatch.setattr(evaluation, "_send_judge", send)
    runner.process_sample(run, sample)
    assert sample["status"] == "evaluation_error"
    assert len(runtime_messages) == 2
    assert runtime_messages[0] == runtime_messages[1]

    saved = repo.get_run(run["id"])
    recovered = saved["samples"][0]
    saved["current_round"] = 1
    saved["samples"].append({**recovered, "id": "candidate", "phase": "exploration", "round": 1, "arm": "candidate",
                             "runtime_metrics": {"tools": {"successes": 5, "total": 5}, "tokens": {"value": 50_000}}})
    updated = _runtime_input()
    updated["summary"]["evidence"][0]["text"] = "后来同步的说明"
    monkeypatch.setattr(evaluation, "_collect_runtime", lambda _: updated)
    runner.process_sample(saved, recovered)

    assert recovered["status"] == "completed"
    assert runtime_messages == [runtime_messages[0]] * 3
    assert set(json.loads(runtime_messages[0][1]["content"])["runtime_statistics"]) == {"baseline"}
    assert all("runtime_judge_input" not in item for item in api.public_run(saved, can_read_content=True)["samples"])


def test_runtime_prompt_is_not_frozen_without_a_judge_request(chapter_run, monkeypatch):
    run, sample = chapter_run
    run.update(runtime_scoring_version=1, judge_repeats=1)
    monkeypatch.setattr(evaluation, "_collect_runtime", lambda _: _runtime_input())

    def credentials(_):
        raise ValueError("评委配置不可用")

    monkeypatch.setattr(repo, "decrypt_credentials", credentials)
    runner.process_sample(run, sample)

    assert sample["status"] == "evaluation_error"
    assert "runtime_judge_input" not in repo.get_run(run["id"])["samples"][0]


def test_scoring_trace_preserves_each_dimension_input_with_the_same_long_prefix(chapter_run, monkeypatch):
    run, sample = chapter_run
    run["judge_repeats"] = 2
    run["private"]["judges"] = run["private"]["judges"][:1]
    run["private"]["rules"]["chapter_text"]["discipline_rules"] = ["共同评分要求。" * 40]
    calls = stub_judges(monkeypatch)
    record_traces(monkeypatch)
    runner.process_sample(run, sample)

    trace_id, = sample["evaluation_trace_ids"]
    events = traces._reconstruct_incremental_inputs([traces.hydrate_event(event) for event in traces._load_events(trace_id)])
    recorded = [event.input for event in events if event.type == "llm_start"]
    assert len(calls) == run["judge_repeats"] * _quality_dimension_count(run)
    assert recorded == [call["messages"] for call in calls]


def test_runtime_token_scores_use_matching_inputs_and_repeats():
    runs = []
    for repeat, value in enumerate((100_000, 100_000, 200_000), 1):
        runs.append({"arm": "baseline", "phase": "regression", "round": 2, "package_id": "chapter",
                     "input_revision": "same", "repeat": repeat,
                     "runtime_metrics": {"tokens": {"value": value}}})
    run = {"samples": [{**runs[0], "input_revision": "different"}, *runs]}
    scores = []
    for repeat, value in enumerate((110_000, 110_000, 180_000), 1):
        sample = {**runs[repeat - 1], "arm": "candidate"}
        scores.append(evaluation._token_metric(run, sample, value)["score"])
    assert scores == pytest.approx([2, 2, 4])


def test_runtime_token_score_caps_and_invalid_baseline():
    run = {"samples": [{"arm": "baseline", "phase": "baseline", "round": 0, "package_id": "q", "repeat": 1,
                        "runtime_metrics": {"tokens": {"value": 80_000}}}]}
    sample = {"arm": "candidate", "phase": "exploration", "round": 1, "package_id": "q", "repeat": 1}
    assert [evaluation._token_metric(run, sample, value)["score"] for value in
            (60_000, 72_000, 76_000, 80_000, 88_000, 100_000)] == pytest.approx([5, 4, 3.5, 3, 2, 1])
    with pytest.raises(ValueError, match="Token"):
        evaluation._token_metric(run, run["samples"][0], 0)


def test_running_regression_reports_only_aggregate_runtime_drop(chapter_run):
    from app.experiments.history import _regression_drops

    run, sample = chapter_run
    run.update(runtime_scoring_version=1)
    run["settings"]["regression_ids"] = {"chapter_text": [sample["package_id"]]}
    run["samples"] = [{**sample, "phase": "regression", "round": 1, "arm": arm,
                       "scores": {"dimensions": {"运行时": score}}}
                      for arm, score in (("baseline", 4), ("candidate", 3.7))]
    assert _regression_drops(run, 1) == {"round": 1, "drops": {"运行时": -0.3}}


def test_runtime_judge_can_mark_evidence_insufficient():
    with pytest.raises(evaluation._InvalidEvidence) as caught:
        evaluation._judgement('{"evidence_insufficient":true,"reason":"缺少关键改稿记录"}', "运行时",
                              material={}, runtime_refs={"trace-body:8": "运行摘要证据"})
    assert caught.value.details == [{"reason": "缺少关键改稿记录"}]


@pytest.mark.parametrize("runtime_first", [True, False])
def test_runtime_returns_independently_and_receives_pair_stats(chapter_run, monkeypatch, runtime_first):
    run, baseline = chapter_run
    run.update(runtime_scoring_version=1, judge_repeats=1, current_round=1)
    run["private"]["judges"] = run["private"]["judges"][:1]
    baseline["runtime_metrics"] = {"tools": {"successes": 3, "total": 4}, "tokens": {"value": 100_000}}
    sample = {**baseline, "arm": "candidate", "phase": "exploration", "round": 1, "id": "candidate"}
    run["samples"].append(sample)
    stub_judges(monkeypatch)
    monkeypatch.setattr(evaluation, "_collect_runtime", lambda _: {**_runtime_input(), "tokens": 90_000})
    ready = Event()
    returns = []

    def send(_credentials, messages, *_):
        material = json.loads(messages[1]["content"])
        runtime = "runtime_trace" in material
        if runtime or '"key": "语言清晰度"' in messages[0]["content"]:
            if runtime != runtime_first:
                assert ready.wait(5)
            returns.append(runtime)
            ready.set()
        if runtime:
            assert material["runtime_statistics"] == {
                "baseline": {"tools": {"successes": 3, "total": 4}, "tokens": 100_000},
                "candidate": {"tools": {"successes": 4, "total": 5}, "tokens": 90_000},
            }
        evidence = ["trace-body:8"] if runtime else ["她在门边停下"]
        return json.dumps({"score": 4, "reason": "定档理由", "evidence": evidence}), None

    monkeypatch.setattr(evaluation, "_send_judge", send)
    result = evaluation.score_sample(run, sample)
    assert returns == [runtime_first, not runtime_first]
    assert result["overall"] == pytest.approx(4.02)


def make_completion(value):
    return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}


def record_traces(monkeypatch):
    recorder = trace_recorder.EvolutionTraceRecorder()
    monkeypatch.setattr(runner, "_recorder", recorder)
    return recorder


def trace_record(trace_id):
    return db.query_one("SELECT status, error FROM runs WHERE trace_id=?", (trace_id,))


def test_live_judge_publishes_provider_chunks_before_completion():
    trace_id = "test-live-judge"
    call_id = "judge-1"

    class JudgeStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"live thought"}}]}\n\n'
            _, packets = live_output.read(trace_id, None)
            assert "live thought" in packets[0]
            assert '"finished":false' in packets[0]
            yield b'data: {"choices":[{"delta":{"content":"{\\"score\\":"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"4}"},"finish_reason":"stop"}]}\n\n'
            yield b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
            yield b"data: [DONE]\n\n"

    def on_request(request):
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=JudgeStream())

    try:
        with httpx.Client(transport=httpx.MockTransport(on_request)) as client:
            output, usage = evaluation._judge_chat(client, "https://judge.invalid", {}, {}, trace_id, call_id, lambda: False)
        assert output == '{"score":4}'
        assert usage == {"prompt_tokens": 7, "completion_tokens": 3}
        assert '"finished":true' in live_output.read(trace_id, None)[1][0]
    finally:
        live_output.discard(trace_id)


def test_judge_can_use_compatible_gateway_without_stream_support():
    requests = []

    def on_request(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream"):
            return httpx.Response(400, json={"error": "Unsupported parameter: stream_options"})
        return httpx.Response(200, json=make_completion({"score": 4}))

    with httpx.Client(transport=httpx.MockTransport(on_request)) as client:
        output, _ = evaluation._judge_chat(client, "https://judge.invalid", {}, {}, "legacy-judge", "legacy-call", lambda: False)
    assert json.loads(output) == {"score": 4}
    assert len(requests) == 2 and "stream" not in requests[1]


def test_model_stream_authorizes_early_writing_output_and_expires_at_terminal(chapter_run, monkeypatch):
    run, sample = chapter_run
    sample.update(status="running", task_id="original")
    repo.put_run(run)
    trace_id = "test-live-writing"
    monkeypatch.setattr(ingestion.settings, "notify_token", "test-token")
    app = FastAPI()
    app.add_middleware(NotifyTokenMiddleware)
    app.include_router(ingestion.router, prefix="/api")
    app.include_router(traces.router, prefix="/api")
    batch = {"trace_id": trace_id, "experiment_id": run["id"], "sample_id": sample["id"],
             "updates": [{"op": "delta", "run_id": "call-1", "channel": "reasoning", "text": "正在推敲"}]}
    with TestClient(app) as client:
        assert client.post("/api/ingestion/model-stream", json=batch).status_code == 401
        headers = {"x-notify-token": "test-token"}
        assert client.post("/api/ingestion/model-stream", json={**batch, "sample_id": "other"}, headers=headers).status_code == 404
        assert client.post("/api/ingestion/model-stream", json=batch, headers=headers).status_code == 204
        assert client.get(f"/api/traces/{trace_id}/model-io/stream").status_code == 403

    async def consume():
        request = Request({"type": "http", "method": "GET", "path": "/"})
        request.state.is_super_admin = True
        stream = await traces.stream_trace_model_io(request, trace_id)
        reader = stream.body_iterator
        first = await anext(reader)
        assert first.isascii()
        assert json.loads(first.split("data: ", 1)[1])["calls"][0]["reasoning"] == "正在推敲"
        live_output.publish_delta(trace_id, "call-1", "text", "初步答案")
        assert "event: delta" in await anext(reader)
        live_output.finish_call(trace_id, "call-1")
        assert "event: end" in await anext(reader)
        db.execute("""INSERT INTO runs(trace_id,workspace_id,status,ingested_at,run_purpose)
                   VALUES(?, '', 'completed', '', 'optimization')""", (trace_id,))
        assert "event: done" in await asyncio.wait_for(anext(reader), 3)
        await reader.aclose()

    try:
        asyncio.run(consume())
        assert db.query_one("SELECT 1 FROM event_payloads WHERE trace_id=?", (trace_id,)) is None
        assert '"calls":[]' in live_output.read(trace_id, None)[1][0]
    finally:
        live_output.discard(trace_id)


def test_repeats_retry_missing_entry_and_keep_frozen_rule_and_content_boundary(chapter_run, monkeypatch):
    run, sample = chapter_run
    expected_calls = len(run["private"]["judges"]) * run["judge_repeats"] * _quality_dimension_count(run)
    app = FastAPI()

    @app.middleware("http")
    async def content_role(request: Request, call_next):
        request.state.is_super_admin = request.headers.get("x-test-role") == "admin"
        return await call_next(request)

    app.include_router(api.router)
    app.include_router(traces.router)
    calls = stub_judges(monkeypatch, invalid_at={2}, http_error_at={expected_calls + 1})
    recorder = record_traces(monkeypatch)
    runner.process_sample(run, sample)
    trace_id, = sample["evaluation_trace_ids"]
    assert len(calls) == expected_calls
    assert len(sample["judge_results"]) == expected_calls - 1
    assert sample["status"] == "evaluation_error"
    assert trace_record(trace_id)["status"] == "running"
    assert {call["model"] for call in calls} == {"judge-a", "judge-b"}
    assert "定档理由" in calls[0]["messages"][0]["content"]
    assert "谜底" in calls[0]["messages"][0]["content"]
    assert "本次生成的人物卡" not in calls[0]["messages"][1]["content"]
    sample["error"] = "评测异常附私有正文：她在门边停下"
    repo.put_run(run)
    with TestClient(app) as client:
        endpoint = "/experiments/runs/" + run["id"]
        privileged = client.get(endpoint, headers={"x-test-role": "admin"}).json()
        partial = privileged["samples"][0]
        assert privileged["scoring_rule"]["id"] == run["settings"]["rule_versions"]["chapter_text"]
        assert privileged["judge_repeats"] == 3
        assert partial["scores"] is None
        assert partial["score_progress"]["completed"] == 5
        assert partial["score_progress"]["total"] == 6
        assert "专用评语" in json.dumps(partial["score_progress"], ensure_ascii=False)
        failures = [entry for entries in partial["score_progress"]["reasons"].values()
                    for entry in entries if entry.get("error")]
        assert len(failures) == 1
        assert failures[0]["judge_id"] == 1
        assert failures[0]["repeat"] == 1
        assert "缺少定档理由" in failures[0]["error"]
        restricted = client.get(endpoint).json()["samples"][0]
        assert restricted["score_progress"] == {"completed": 5, "total": 6}
        assert "她在门边停下" not in json.dumps(restricted, ensure_ascii=False)
        assert "专用评语" not in json.dumps(restricted, ensure_ascii=False)
        usage_page = client.get(endpoint + "/usage/evaluation")
        assert "专用评语" not in usage_page.text
        assert usage_page.json()["items"][0]["usage"]["total_tokens"] == 7
        assert client.get(f"/traces/{trace_id}/events?event_ids=scoring-event").status_code == 403
        recorder.flush_sync(trace_id)
        model_io = f"/traces/{trace_id}/model-io"
        assert client.get(model_io).status_code == 403
        model_io_headers = {"x-test-role": "admin"}
        batch = client.get(model_io, params={"limit": 2}, headers=model_io_headers).json()
        assert batch["initial_input"]["input"] == calls[0]["messages"]
        assert [event["type"] for event in batch["outputs"]] == ["llm_end", "llm_error"]
        assert "专用评语" in batch["outputs"][0]["output"]
        assert batch["has_more"]
        cursor = batch["outputs"][-1]["sequence"]
        remaining = client.get(model_io, params={"after_sequence": cursor, "include_initial": False, "limit": 50},
                               headers=model_io_headers).json()
        assert remaining["initial_input"] is None
        sequences = [event["sequence"] for event in remaining["outputs"]]
        assert len(sequences) == expected_calls - 2 and all(sequence > cursor for sequence in sequences)
        assert sequences == sorted(sequences)
        assert not remaining["has_more"]
        saved = repo.get_run(run["id"])
        recovered = saved["samples"][0]
        runner.process_sample(saved, recovered)
        assert len(calls) == expected_calls + 1
        assert recovered["status"] == "evaluation_error"
        assert trace_record(trace_id)["status"] == "running"
        runner.process_sample(saved, recovered)
        assert len(calls) == expected_calls + 2
        assert calls[expected_calls:] == [calls[1], calls[1]]
        assert recovered["evaluation_trace_ids"] == [trace_id]
        assert trace_record(trace_id)["status"] == "completed"
        assert recorder.is_terminal(trace_id)
        rows = db.query_all("SELECT payload_json FROM event_payloads WHERE trace_id=? ORDER BY sequence", (trace_id,))
        failed_calls = [event for event in (json.loads(row["payload_json"]) for row in rows)
                        if event["type"] == "llm_error"]
        assert len(failed_calls) == 2
        assert "缺少定档理由" in failed_calls[0]["error"]
        assert "HTTP 503" in failed_calls[1]["error"]
        assert recovered["scores"]["overall"] == 4
        for entries in recovered["scores"]["reasons"].values():
            assert {(entry["judge_id"], entry["repeat"]) for entry in entries} == {
                (judge, repeat) for judge in (1, 2) for repeat in (1, 2, 3)
            }
            assert all(entry["reason"] and entry["evidence"] for entry in entries)
        assert recovered["artifacts"] == sample["artifacts"]
        restricted = client.get(endpoint).json()["samples"][0]
        assert restricted["scores"]["overall"] == 4
        assert restricted["scores"]["reasons"] == {}


def test_scoring_uses_fixed_chapter_and_prior_context_without_other_manuscripts():
    package = {"target": {"chapter": 2}, "input": {"files": {
        "正文/第001章.md": "前情", "章纲/第002章.md": "本章要求", "人物/主角.md": "固定设定",
        "正文/第002章.md": "旧版正文", "章纲/第003章.md": "后文计划",
    }}}
    sample = {"artifacts": {"正文/第002章.md": "实际成稿", "人物/主角.md": "本次运行产出"}}
    scored = evaluation._chapter_score_material(package, sample)
    assert scored["input"]["files"] == {"正文/第001章.md": "前情", "章纲/第002章.md": "本章要求", "人物/主角.md": "固定设定"}
    assert scored["artifacts"] == {"正文/第002章.md": "实际成稿"}


def test_reason_requires_a_verifiable_manuscript_quote_or_line():
    material = {"input": {"files": {"章纲/第001章.md": "章纲要求脱身"}},
                "artifacts": {"正文/第001章.md": "第一行内容\n第二行内容"}}
    reason = {"score": 2, "reason": "章纲要求没有落实。"}
    for citations in (["第二行内容"], ["章纲要求脱身", "正文/第001章.md:2"]):
        assert evaluation._judgement(json.dumps({**reason, "evidence": citations}), "章纲兑现", material=material)["score"] == 2
    for citations in (["无可核对内容"], ["章纲要求脱身"], ["正文/第001章.md:3"], []):
        with pytest.raises(ValueError, match="评分依据"):
            evaluation._judgement(json.dumps({**reason, "evidence": citations}), "章纲兑现", material=material)


@pytest.mark.parametrize(("citation", "accepted"), [
    ("她说“不能开门”，收起3号钥匙。", True),
    ("她说「不能开门」，收起3号钥匙。", True),
    ("她说不能开门，收起3号钥匙。", False),
    ("她说“能开门”，收起3号钥匙。", False),
    ("她说“不能开门”，收起4号钥匙。", False),
    ("她说“不能开门”……钥匙。", False),
    ("正文/第001章.md：1", False),
])
def test_quote_equivalence_preserves_meaning_and_requires_every_citation(citation, accepted):
    material = {"input": {"files": {}}, "artifacts": {"正文/第001章.md": '她说"不能开门"，收起3号钥匙。'}}
    evidence = ["收起3号钥匙", citation]
    raw = json.dumps({"score": 4, "reason": "原文有清楚的动机。", "evidence": evidence})
    if accepted:
        assert evaluation._judgement(raw, "事件因果逻辑", material=material)["evidence"] == evidence
    else:
        with pytest.raises(ValueError, match="评分依据"):
            evaluation._judgement(raw, "事件因果逻辑", material=material)


@pytest.mark.parametrize(("citation", "accepted"), [
    ("1: 她说“不能开门”。", True),
    ("3: 钥匙收好", True),
    ("3: 见 memo.md: 记录", True),
    ("3: 她说“不能开门”。", False),
    ("1: 她说“能开门”。", False),
    ("1: 她说……开门", False),
    ("1: 章纲要求脱身", False),
    ("2: 他把钥匙收好。", False),
    ("2: ", False),
    ("4: 他把钥匙收好。", False),
    ("0: 她说“不能开门”。", False),
    ("正文/第001章.md:1-3", True),
    ("正文/第001章.md:3-1", False),
    ("正文/第001章.md:0-2", False),
    ("正文/第001章.md:1-4", False),
    ("正文/第999章.md:1-3", False),
    ("正文/第001章.md:1: 她说“不能开门”。", True),
    ("正文/第001章.md:3：见 memo.md: 记录", True),
    ("正文/第001章.md:1-3: 她说“不能开门”。 / 他把钥匙收好。", True),
    ("正文/第001章.md:1-3: 她说“不能开门”。\n\n他把钥匙收好。", True),
    ("正文/第001章.md:1：他把钥匙收好。", False),
    ("正文/第001章.md:1-2: 她说“不能开门”。 / 他把钥匙收好。", False),
    ("正文/第001章.md:1-3: 她说“能开门”。 / 他把钥匙收好。", False),
    ("正文/第001章.md:1-3: 她说“不能开门”。……他把钥匙收好。", False),
    ("正文/第001章.md:1:", False),
])
def test_numbered_quote_and_line_range_require_the_declared_source(citation, accepted):
    material = {
        "input": {"files": {"章纲/第001章.md": "章纲要求脱身"}},
        "artifacts": {"正文/第001章.md": '她说"不能开门"。\n\n他把钥匙收好。见 memo.md: 记录。'},
    }
    evidence = [citation] if accepted else ["他把钥匙收好", citation]
    raw = json.dumps({"score": 4, "reason": "原文有清楚的动机。", "evidence": evidence})
    if accepted:
        assert evaluation._judgement(raw, "语言清晰度", material=material)["evidence"] == evidence
    else:
        with pytest.raises(ValueError, match="评分依据"):
            evaluation._judgement(raw, "语言清晰度", material=material)


def test_retry_preserves_first_request_and_keeps_evidence_errors_private(chapter_run, monkeypatch):
    run, sample = chapter_run
    quality_count = _quality_dimension_count(run)
    calls_per_judge = run["judge_repeats"] * quality_count
    expected_calls = len(run["private"]["judges"]) * calls_per_judge
    http_failure_call = quality_count + 2
    invalid_line_call = calls_per_judge + 1
    manuscript = next(path for path in sample["artifacts"] if path.startswith("正文/"))
    sample["artifacts"][manuscript] = "门开了。\n\n她在门边停下，听清脚步后才出门。"
    repo.put_run(run)
    invalid_quote = "她在门边……才出门"
    invalid_line = f"{manuscript}:99"
    valid_quote = "她在门边停下"
    calls = stub_judges(monkeypatch, http_error_at={http_failure_call}, evidence_at={
        1: [f"3: {valid_quote}", f"{manuscript}:1-3"],
        2: [valid_quote, invalid_quote],
        invalid_line_call: [invalid_line],
    })

    runner.process_sample(run, sample)
    assert len(calls) == expected_calls
    assert len(sample["judge_results"]) == expected_calls - 3
    assert sample["status"] == "evaluation_error"
    instructions, scoring_input = calls[0]["messages"]
    assert "行号: 正文原句" in instructions["content"]
    assert "文件路径:起始行-结束行" in instructions["content"]
    assert "带路径的条目到行号为止" in instructions["content"]
    first_material = json.loads(scoring_input["content"])
    assert first_material["artifacts"][manuscript] == "1: 门开了。\n2: \n3: 她在门边停下，听清脚步后才出门。"

    saved = repo.get_run(run["id"])
    recovered = saved["samples"][0]
    for can_read_content in (False, True):
        public = api.public_run(saved, can_read_content=can_read_content)
        assert "judge_evidence_feedback" not in public["samples"][0]
        serialized = json.dumps(public, ensure_ascii=False)
        assert invalid_quote not in serialized and invalid_line not in serialized

    issues = [error for entries in recovered["judge_evidence_feedback"].values() for error in entries if "citation" in error]
    assert issues == [{"index": 2, "citation": invalid_quote, "reason": "找不到连续原文"},
                      {"index": 1, "citation": invalid_line, "reason": "行号超出文件范围"}]
    runner.process_sample(saved, recovered)
    assert calls[expected_calls:] == [calls[1], calls[http_failure_call - 1], calls[invalid_line_call - 1]]
    assert recovered["status"] == "completed"
    assert recovered["scores"]["overall"] == 4
    assert not recovered.get("judge_evidence_feedback")


def test_exhausted_incomplete_score_does_not_evolve_or_compare(chapter_run, monkeypatch):
    run, sample = chapter_run
    accepted_calls = len(run["private"]["judges"]) * run["judge_repeats"] * _quality_dimension_count(run) - 1
    calls = stub_judges(monkeypatch, reject_after=accepted_calls)
    record_traces(monkeypatch)
    for _ in range(3):
        runner.process_sample(run, sample)
        assert sample["status"] == "evaluation_error"
        trace_id, = sample["evaluation_trace_ids"]
        assert trace_record(trace_id)["status"] == "running"
    runner.process_sample(run, sample)
    assert len(calls) == accepted_calls + 4
    assert sample["evaluation_trace_ids"] == [trace_id]
    trace = trace_record(trace_id)
    assert trace["status"] == "failed"
    assert "评委连接超时" in trace["error"]
    assert sample["status"] == "failed"
    assert sample["scores"] is None
    assert "评委连接超时" in sample["error"]
    assert len(sample["judge_results"]) == accepted_calls
    runner.advance_one(run["id"])
    saved = repo.get_run(run["id"])
    assert saved["status"] == "failed"
    assert "评测异常" in saved["end_reason"]
    assert saved["report"] == {}
    assert saved["rounds"] == []
    assert saved["samples"][0]["artifacts"] == sample["artifacts"]
    assert saved["samples"][0]["judge_results"] == sample["judge_results"]


@pytest.mark.parametrize("phase", ["exploration", "regression"])
def test_earlier_complete_pairs_do_not_create_phase_delta_after_a_scoring_outage(chapter_run, phase):
    run, base = chapter_run
    run["id"] += "-" + phase
    run["phase"] = phase
    run["current_round"] = 1
    run["rounds"] = [{"round": 1, "base_commit": "a" * 40, "source_commit": "b" * 40, "status": phase,
                      "report": {"passed": True} if phase == "regression" else {}}]
    run["settings"]["regression_ids"] = {"chapter_text": [base["package_id"]]}
    run["settings"]["optimization_ids"] += ["next-question"]
    sample_counts = (1, 2, 3) if phase == "regression" else (1,)
    ids = [base["package_id"]] if phase == "regression" else run["settings"]["optimization_ids"]
    run["samples"] = [{**base, "id": f"{arm}-{package_id}-{repeat}", "arm": arm, "round": 1,
                       "package_id": package_id, "repeat": repeat, "status": "completed",
                       "phase": "baseline" if phase == "exploration" and arm == "baseline" else phase,
                       "scores": {"overall": 3 if arm == "baseline" else 4}}
                      for arm in ("baseline", "candidate") for package_id in ids for repeat in sample_counts]
    run["samples"][-1] = {**run["samples"][-1], "status": "failed", "scores": None}
    repo.put_run(run)
    runner.advance_one(run["id"])
    saved = repo.get_run(run["id"])
    assert saved["status"] == "failed"
    assert "评测异常" in saved["end_reason"]
    assert "quality_delta" not in json.dumps(saved["report"])
    assert "passed" not in saved["report"]
    assert saved["rounds"][0]["status"] == "failed"


def test_archived_run_without_repeat_snapshot_uses_original_single_judgement(chapter_run, monkeypatch):
    run, sample = chapter_run
    run.pop("judge_repeats")
    frozen = run["private"]["rules"]["chapter_text"]
    frozen["profile"] = "chapter-text-outline-v2"
    frozen["dimensions"] = frozen["dimensions"][:4]
    calls = stub_judges(monkeypatch)
    result = evaluation.score_sample(run, sample)
    assert len(calls) == len(run["private"]["judges"]) * len(frozen["dimensions"])
    for entries in result["reasons"].values():
        assert {entry["judge_id"] for entry in entries} == {1, 2}
        assert all("达标" in entry and "repeat" not in entry for entry in entries)


@pytest.mark.parametrize("candidate_score, minimum, passes", [(4.1, .1, True), (4.09, .1, False), (4.096, .1, False),
                                                              (4, 0, True), (3.99, 0, False)])
def test_existing_total_score_thresholds_include_boundaries(candidate_score, minimum, passes):
    def scored(score):
        return {"package_id": "q", "repeat": 1, "completed": True, "scores": {"overall": score}}

    comparison = evaluation.compare_results([scored(4)], [scored(candidate_score)], completion_rate=1, quality_gain=minimum)
    assert comparison["passed"] is passes
