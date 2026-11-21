"""Repeated chapter judging preserves completed evidence and gates comparisons."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
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


def stub_judges(monkeypatch, *, invalid_at=(), reject_after=None, http_error_at=()):
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
    app = FastAPI()

    @app.middleware("http")
    async def content_role(request: Request, call_next):
        request.state.is_super_admin = request.headers.get("x-test-role") == "admin"
        return await call_next(request)

    app.include_router(api.router)
    app.include_router(traces.router)
    calls = stub_judges(monkeypatch, invalid_at={2}, http_error_at={19})
    recorder = record_traces(monkeypatch)
    runner.process_sample(run, sample)
    trace_id, = sample["evaluation_trace_ids"]
    assert len(calls) == 18
    assert len(sample["judge_results"]) == 17
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
        remaining = client.get(model_io, params={"after_sequence": cursor, "include_initial": False},
                               headers=model_io_headers).json()
        assert remaining["initial_input"] is None
        sequences = [event["sequence"] for event in remaining["outputs"]]
        assert len(sequences) == 16 and all(sequence > cursor for sequence in sequences)
        assert sequences == sorted(sequences)
        assert not remaining["has_more"]
        saved = repo.get_run(run["id"])
        recovered = saved["samples"][0]
        runner.process_sample(saved, recovered)
        assert len(calls) == 19
        assert recovered["status"] == "evaluation_error"
        assert trace_record(trace_id)["status"] == "running"
        runner.process_sample(saved, recovered)
        assert len(calls) == 20
        assert calls[1] == calls[18] == calls[19]
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


def test_exhausted_incomplete_score_does_not_evolve_or_compare(chapter_run, monkeypatch):
    run, sample = chapter_run
    calls = stub_judges(monkeypatch, reject_after=17)
    record_traces(monkeypatch)
    for _ in range(3):
        runner.process_sample(run, sample)
        assert sample["status"] == "evaluation_error"
        trace_id, = sample["evaluation_trace_ids"]
        assert trace_record(trace_id)["status"] == "running"
    runner.process_sample(run, sample)
    assert len(calls) == 17 + 4
    assert sample["evaluation_trace_ids"] == [trace_id]
    trace = trace_record(trace_id)
    assert trace["status"] == "failed"
    assert "评委连接超时" in trace["error"]
    assert sample["status"] == "failed"
    assert sample["scores"] is None
    assert "评委连接超时" in sample["error"]
    assert len(sample["judge_results"]) == 17
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
    calls = stub_judges(monkeypatch)
    result = evaluation.score_sample(run, sample)
    assert len(calls) == len(run["private"]["judges"]) * len(frozen["dimensions"])
    for entries in result["reasons"].values():
        assert {entry["judge_id"] for entry in entries} == {1, 2}
        assert all("达标" in entry and "repeat" not in entry for entry in entries)


@pytest.mark.parametrize("candidate_score, minimum, passes", [(4.1, .1, True), (4.09, .1, False), (4, 0, True), (3.99, 0, False)])
def test_existing_total_score_thresholds_include_boundaries(candidate_score, minimum, passes):
    def scored(score):
        return {"package_id": "q", "repeat": 1, "completed": True, "scores": {"overall": score}}

    comparison = evaluation.compare_results([scored(4)], [scored(candidate_score)], completion_rate=1, quality_gain=minimum)
    assert comparison["passed"] is passes
