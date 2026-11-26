"""Exercise the production chapter-evolution graph with a scripted chat model."""
import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import deepagents
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.core.settings import settings
from app.evolve import db as sessions, docs
from app.experiments import agent, api, evaluation, history, repo, runner
from app.trace import EvolutionTraceRecorder


PROMPT = "prompts/creative_text.md"
BASELINE_LOCATION = {"round_number": 0, "sample_number": 1}


@pytest.fixture
def chapter_run(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setattr(settings, "evolution_db", str(tmp_path / "evolution.db"))
    monkeypatch.setattr(docs, "_docs_root", lambda: tmp_path / "docs")
    monkeypatch.setattr(repo, "decrypt_credentials", lambda _: {
        "model": "scripted", "api_key": "test-only", "base_url": "http://127.0.0.1"})
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", conn)
    db.init_db()
    repo.init_schema()
    package = next(item for item in repo.list_packages() if item["purpose"] == "optimization")
    root = tmp_path / "harness"
    (root / "prompts").mkdir(parents=True)
    (root / PROMPT).write_text("按章纲写正文。", encoding="utf-8")

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("add", "--", PROMPT)
    git("-c", "user.name=Test", "-c", "user.email=test@local", "commit", "-qm", "baseline")
    record = {"round": 1, "base_commit": git("rev-parse", "HEAD"), "session_id": "graph-evolve-1", "status": "evolving"}
    target = package["target"]
    sample = {"id": "sample-this-graph", "source_commit": record["base_commit"], "package_id": package["id"],
              "package_title": package["title"], "task_id": "generation-current", "repeat": 1,
              "round": 0, "phase": "baseline", "arm": "baseline", "status": "completed", "target": target,
              "scores": {"overall": 3.0, "dimensions": {"衔接": 3.0}, "reasons": {"衔接": ["此前线索未呈现"]}},
              "artifacts": {f"正文/第{target['chapter']:03d}章.md": "稚禾当即猜到真相。"}, "trace_ids": ["trace-current"]}
    run = {"id": "assessment-this-graph", "status": "running", "created_at": repo.now(), "task_type": "chapter_text",
           "assessment_version": 1, "flow_version": 3, "baseline": {"commit": record["base_commit"]},
           "settings": {"optimization_ids": [package["id"]]}, "rounds": [record], "samples": [sample],
           "private": {"evolver": {}, "package_revisions": {package["id"]: package["input_revision"]}}}
    try:
        yield run, record, sample, root
    finally:
        conn.close()


def test_real_graph_recovers_in_the_same_chapter_round(chapter_run, tmp_path, monkeypatch):
    run, record, sample, root = chapter_run
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    with TestClient(app) as client:
        response = client.get("/api/experiments/evolver-assets")
    assert response.status_code == 200
    assets = response.json()
    tool_descriptions = {tool["name"]: tool["description"] for tool in assets["tools"]}
    create_deep_agent = deepagents.create_deep_agent

    def check_system_prompt(**kwargs):
        assert kwargs["system_prompt"] == assets["system_prompt"]
        return create_deep_agent(**kwargs)

    monkeypatch.setattr(deepagents, "create_deep_agent", check_system_prompt)
    monkeypatch.setattr(settings, "trace_payload_dir", str(tmp_path / "payloads"))
    monkeypatch.setattr("app.trace.recorder._WAL_ROOT", tmp_path / "wal")
    monkeypatch.setattr(runner, "_recorder", EvolutionTraceRecorder())
    foreign = {**run, "id": "assessment-other", "rounds": [], "samples": [{
        **sample, "id": "sample-other", "task_id": "generation-other", "trace_ids": [],
        "artifacts": dict.fromkeys(sample["artifacts"], "外档案独有原文")}]}
    repo.put_run(foreign)
    db.execute("""INSERT INTO runs(trace_id,workspace_id,status,ingested_at,service,run_snapshot_json)
                  VALUES(?,?,'completed',?,'executor',?)""", ("trace-current", "workspace-current", repo.now(),
                  json.dumps({"target_task": {"task_id": sample["task_id"]}})))
    event = {"trace_id": "trace-current", "event_id": "event-current", "sequence": 1, "type": "tool_end",
             "source": "runtime", "status": "completed", "timestamp": repo.now(), "tool_name": "task",
             "tool_output": {"content": "审稿意见：补入线索。"}}
    db.execute("INSERT INTO event_payloads(trace_id,sequence,type,payload_json) VALUES(?,1,'tool_end',?)",
               ("trace-current", json.dumps(event)))

    replies = {}
    registered = []
    observed = []
    steps = [
        ("list_assessment_history", {}),
        ("read_assessment_round", {"round_number": 1, "section": "samples"}),
        (None, "第 1 轮没有样本，暂时无法确认。"),
        ("read_assessment_round", {"round_number": 0, "section": "samples"}),
        ("read_improvement_sample", {**BASELINE_LOCATION, "section": "scores"}),
        ("read_improvement_sample", {**BASELINE_LOCATION, "section": "manuscript"}),
        ("list_writing_events", {**BASELINE_LOCATION, "trace_number": 1}),
        ("read_writing_event", {**BASELINE_LOCATION, "trace_number": 1, "sequence": 1}),
        ("search_assessment_history", {"query": "外档案独有原文"}),
        ("read_source", {"file_path": PROMPT}),
        ("write_design_doc", {}),
        ("edit_source", {"file_path": PROMPT, "old_string": "按章纲写正文。", "new_string": "在揭示真相前呈现线索。"}),
        ("validate_changes", {}),
        (None, "改动已校验。"),
        ("write_change_log", {"summary": "增加推理线索", "applied": [
            {"target": PROMPT, "action": "edit_source", "result": "ok", "detail": "写明线索要求", "design_ref": 1}]}),
    ]

    class ChapterModel(FakeMessagesListChatModel):
        def __init__(self, **_kwargs):
            super().__init__(responses=[])

        def bind_tools(self, tools, **kwargs):
            assert {tool.name: tool.description for tool in tools} == tool_descriptions
            registered.append({tool.name for tool in tools})
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            observed.append(list(messages))
            if self.i == 0:
                assert all("先前评测秘密" not in str(message.content) for message in messages)
                assert all("此前线索未呈现" not in str(message.content) for message in messages)
                assert any(sample["id"] in str(message.content) for message in messages)
            elif self.i <= len(steps) and steps[self.i - 1][0]:
                result = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
                replies[self.i - 1] = result.content
                assert result.status == "success", result.content
            if self.i == len(steps):
                response = AIMessage(content="改动已验证。")
            else:
                assert self.i < len(steps), "流程结束后不应继续调用模型"
                name, args = steps[self.i]
                if name == "write_design_doc":
                    listed = json.loads(replies[3])["items"][0]
                    args = {"rationale": "先呈现线索，读者才能理解人物判断。", "changes": [{
                        "target": PROMPT, "change_desc": "要求先写出人物看到的线索", "reason": "原稿直接揭晓",
                        "evidence_ref": [listed["evidence_ref"]], "expected_up": "因果衔接", "expected_down": "无"}]}
                if name is None:
                    response = AIMessage(content=args)
                else:
                    assert name in registered[-1]
                    response = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"step-{self.i}"}])
            self.i += 1
            return ChatResult(generations=[ChatGeneration(message=response)])

    monkeypatch.setattr("langchain_openai.ChatOpenAI", ChapterModel)
    result = asyncio.run(agent._improve(run, record, root, [{"prior": "先前评测秘密"}]))
    assert set(replies) == {index for index, (name, _) in enumerate(steps) if name}
    assert all(not ({"read_work_trace", "read_agent_session", "read_file", "task"} & names) for names in registered)
    assert json.loads(replies[1])["items"] == []
    assert any(isinstance(message, SystemMessage) for message in observed[3][-2:])
    assert any(isinstance(message, SystemMessage) and "write_change_log" in str(message.content)
               for message in observed[14][-2:])
    assert "此前线索未呈现" in replies[4] and "稚禾当即猜到真相" in replies[5]
    assert "tool_end" in replies[6] and "补入线索" in replies[7]
    assert json.loads(replies[8])["items"] == []
    assert "设计文档已产出" in replies[10] and json.loads(replies[12])["passed"]
    assert (root / PROMPT).read_text(encoding="utf-8") == "在揭示真相前呈现线索。"
    assert result["validated"] and "增加推理线索" in result["summary"]
    saved = repo.get_run(run["id"])["rounds"][0]
    assert saved["evidence_samples"] == [sample["id"]] and "线索" in saved["design"]
    traces = db.query_all("SELECT trace_id, status FROM runs WHERE thread_id=?", (record["session_id"],))
    assert len(saved["trace_ids"]) == 1
    assert traces == [{"trace_id": saved["trace_ids"][0], "status": "completed"}]


def _script_model(monkeypatch, responses, *, on_exhausted=None):
    calls = []

    class ScriptedChapterModel(FakeMessagesListChatModel):
        def __init__(self, **_kwargs):
            super().__init__(responses=responses)

        def bind_tools(self, _tools, **_kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(list(messages))
            if on_exhausted is not None and len(calls) == len(responses) + 1:
                on_exhausted()
            assert len(calls) <= len(responses), "流程超过预期的模型调用上限"
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", ScriptedChapterModel)
    return calls


def _tool_call(name, *, call_id=None, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id or name}])


@pytest.fixture
def failed_round(chapter_run):
    run, first, baseline, _ = chapter_run
    baseline.update(completed=True, input_revision="same-topic-input")
    other_baseline = {
        **baseline, "id": "sample-other-topic-baseline", "package_id": "other-optimization-topic",
        "package_title": "另一题", "input_revision": "other-topic-input",
    }
    candidate_commit = "a" * 40
    manuscript_path = next(iter(baseline["artifacts"]))
    candidate = {
        **baseline, "id": "sample-failed-candidate", "arm": "candidate", "phase": "exploration", "round": 1,
        "source_commit": candidate_commit, "scores": {"overall": 2.0, "dimensions": {"衔接": 2.0}},
        "artifacts": {manuscript_path: "她还在屋内。\n门外的人递来信。"},
    }
    other_candidate = {
        **other_baseline, "id": "sample-other-topic-candidate", "arm": "candidate", "phase": "exploration",
        "round": 1, "source_commit": candidate_commit, "scores": {"overall": 3.0, "dimensions": {"衔接": 3.0}},
    }
    first.update(status="not_improved", source_commit=candidate_commit, report=evaluation.compare_results(
        [baseline, other_baseline], [candidate, other_candidate], completion_rate=1, quality_gain=.1,
    ))
    run["settings"]["optimization_ids"].append(other_baseline["package_id"])
    run["samples"].extend([other_baseline, candidate, other_candidate])
    record = {"round": 2, "base_commit": first["base_commit"], "session_id": "graph-evolve-2", "status": "evolving"}
    run["rounds"].append(record)
    record["failure_feedback"] = history.prepare_failure_feedback(run, record)
    assert record["failure_feedback"]["cases"][0]["candidate_id"] == candidate["id"]
    return record, candidate, other_baseline, f"{manuscript_path}:1-2"


def _failure_read(round_number, section, *, sample_number=1):
    return _tool_call("read_improvement_sample", call_id=f"read-{round_number}-{sample_number}-{section}",
                      round_number=round_number, sample_number=sample_number, section=section)


def _tool_reply(calls, call_id):
    return next(message for messages in reversed(calls) for message in reversed(messages)
                if isinstance(message, ToolMessage) and message.tool_call_id == call_id)


@pytest.mark.parametrize("decision", ["change_direction", "continue"])
def test_real_graph_requires_previous_failed_candidate_before_editing_after_rollback(chapter_run, failed_round, monkeypatch, decision):
    run, _, baseline, root = chapter_run
    record, candidate, other_baseline, location = failed_round
    refs = [baseline["id"], candidate["id"]]
    review = {"decision": "change_direction", "reason": "候选引入走位断裂，改查关键动作衔接。",
              "evidence_refs": refs, "manuscript_location": location}
    accepted = {**review, "decision": decision}
    if decision == "continue":
        accepted.update(reason="继续修正动作衔接，但把核对放到审稿。", new_evidence="候选对照显示动笔提示没防住走位断裂。")
    design = {"rationale": review["reason"], "changes": [{
        "target": PROMPT, "change_desc": "补足关键动作前后的空间信息", "reason": review["reason"],
        "evidence_ref": refs, "expected_up": "动作衔接", "expected_down": "无",
    }]}
    edit = {"file_path": PROMPT, "old_string": "按章纲写正文。", "new_string": "核对关键动作前后人物的位置。"}
    calls = _script_model(monkeypatch, [
        _tool_call("edit_source", call_id="edit-too-early", **{**edit, "new_string": "过早改了。"}),
        _failure_read(0, "scores"),
        _failure_read(0, "manuscript"),
        _failure_read(1, "scores"),
        _tool_call("record_failure_review", call_id="review-missing-manuscript", **review),
        _failure_read(1, "manuscript"),
        _failure_read(0, "scores", sample_number=2),
        _failure_read(0, "manuscript", sample_number=2),
        _tool_call("record_failure_review", call_id="review-wrong-topic",
                   **{**review, "evidence_refs": [other_baseline["id"], candidate["id"]]}),
        _tool_call("record_failure_review", call_id="review-invalid-location",
                   **{**review, "manuscript_location": location.split(":", 1)[0] + ":99"}),
        _tool_call("record_failure_review", call_id="continue-without-new-evidence", **{**review, "decision": "continue"}),
        _tool_call("record_failure_review", **accepted),
        _tool_call("edit_source", call_id="edit-before-design", **{**edit, "new_string": "漏写设计。"}),
        _tool_call("write_design_doc", call_id="design-baseline-only", rationale=design["rationale"],
                   changes=[{**design["changes"][0], "evidence_ref": [baseline["id"]]}]),
        _tool_call("write_design_doc", **design),
        _tool_call("edit_source", **edit),
        _tool_call("validate_changes"),
        _tool_call("write_change_log", summary="补足动作衔接", applied=[
            {"target": PROMPT, "action": "edit_source", "result": "ok", "detail": "核对走位", "design_ref": 1}]),
        AIMessage(content="已完成改动和校验。"),
    ])

    result = asyncio.run(agent._improve(run, record, root, []))

    for call_id in ("edit-too-early", "edit-before-design"):
        assert "已编辑" not in str(_tool_reply(calls, call_id).content)
    assert _tool_reply(calls, "design-baseline-only").status == "error"
    assert "已编辑" in str(_tool_reply(calls, "edit_source").content)
    for call_id in ("review-missing-manuscript", "review-wrong-topic"):
        assert "各读正文和评分" in str(_tool_reply(calls, call_id).content)
    assert "可定位的行号" in str(_tool_reply(calls, "review-invalid-location").content)
    assert "补充新证据" in str(_tool_reply(calls, "continue-without-new-evidence").content)
    assert "已记录本轮失败复盘决策" in str(_tool_reply(calls, "record_failure_review").content)
    assert result["validated"]
    assert (root / PROMPT).read_text(encoding="utf-8") == edit["new_string"]
    saved = repo.get_run(run["id"])["rounds"][1]
    assert saved["base_commit"] == run["baseline"]["commit"]
    assert saved["failure_review"]["round"] == 1
    assert saved["failure_review"]["decision"] == decision
    assert saved["failure_review"]["manuscript_location"] == location
    assert set(saved["failure_review"]["evidence_refs"]) == set(refs)


def test_real_graph_recovers_partial_failure_read_and_accepts_grounded_stop(chapter_run, failed_round, monkeypatch):
    run, _, baseline, root = chapter_run
    record, candidate, _, location = failed_round
    refs = [baseline["id"], candidate["id"]]
    reason = "失败候选缺少动作过渡，现有同题证据还不能定位通用缺口。"
    reads = [_failure_read(0, "scores"), _failure_read(0, "manuscript"), _failure_read(1, "scores")]

    def interrupt():
        saved = repo.get_run(run["id"])["rounds"][1]
        assert set(saved["failure_feedback_reads"][baseline["id"]]) == {"scores", "manuscript"}
        assert saved["failure_feedback_reads"][candidate["id"]] == ["scores"]
        raise RuntimeError("模拟进程中断")

    _script_model(monkeypatch, reads, on_exhausted=interrupt)

    with pytest.raises(RuntimeError, match="模拟进程中断"):
        asyncio.run(agent._improve(run, record, root, []))

    recovered = repo.get_run(run["id"])
    record = recovered["rounds"][1]
    assert not record.get("failure_review")
    calls = _script_model(monkeypatch, [
        _tool_call("finish_without_changes", call_id="finish-before-review", reason=reason, evidence_refs=refs),
        _failure_read(1, "manuscript"),
        _tool_call("record_failure_review", decision="stop", reason=reason, evidence_refs=refs, manuscript_location=location),
        _tool_call("finish_without_changes", reason=reason, evidence_refs=refs),
        AIMessage(content="已核对失败候选，结束本轮。"),
    ])

    result = asyncio.run(agent._improve(recovered, record, root, []))

    assert "已记录无改动" not in str(_tool_reply(calls, "finish-before-review").content)
    assert "已记录无改动" in str(_tool_reply(calls, "finish_without_changes").content)
    assert result["outcome"] == "no_change"
    assert set(result["evidence_samples"]) == set(refs)
    assert (root / PROMPT).read_text(encoding="utf-8") == "按章纲写正文。"
    saved = repo.get_run(run["id"])["rounds"][1]
    assert saved["failure_review"]["decision"] == "stop"
    assert set(saved["failure_feedback_reads"][candidate["id"]]) == {"scores", "manuscript"}


def test_real_graph_accepts_a_grounded_no_change_conclusion(chapter_run, monkeypatch):
    run, record, sample, root = chapter_run
    reason = "原版评分指出个案节奏问题，尚不能证实存在通用缺口。"
    _script_model(monkeypatch, [
        _tool_call("read_improvement_sample", **BASELINE_LOCATION, section="scores"),
        _tool_call("finish_without_changes", reason=reason, evidence_refs=[sample["id"]]),
        AIMessage(content="已核查原版证据，保留原版。"),
    ])

    result = asyncio.run(agent._improve(run, record, root, []))

    assert result["outcome"] == "no_change"
    assert result["summary"] == reason
    assert result["evidence_samples"] == [sample["id"]]
    assert not sessions.get_session(record["session_id"])["change_log_path"]
    assert (root / PROMPT).read_text(encoding="utf-8") == "按章纲写正文。"
    recovered = repo.get_run(run["id"])
    assert asyncio.run(agent._improve(recovered, recovered["rounds"][0], root, [])) == result


def test_real_graph_no_change_recovers_from_a_truncated_evidence_ref(chapter_run, monkeypatch):
    run, record, sample, root = chapter_run
    reason = "原版评分指出个案节奏问题，尚不能证实存在通用缺口。"
    truncated = sample["id"][:-1]
    calls = _script_model(monkeypatch, [
        _tool_call("read_improvement_sample", **BASELINE_LOCATION, section="scores"),
        _tool_call("finish_without_changes", call_id="finish-truncated", reason=reason, evidence_refs=[truncated]),
        _tool_call("finish_without_changes", reason=reason, evidence_refs=[sample["id"]]),
        AIMessage(content="已按提示原样引用，结束本轮。"),
    ])

    result = asyncio.run(agent._improve(run, record, root, []))

    rejection = str(_tool_reply(calls, "finish-truncated").content)
    assert truncated in rejection
    assert sample["id"] in rejection
    assert result["outcome"] == "no_change"
    assert result["evidence_samples"] == [sample["id"]]


@pytest.mark.parametrize("case", ["list_only", "dirty_worktree", "foreign_ref"])
def test_real_graph_rejects_invalid_no_change_and_stops_after_bounded_nudges(chapter_run, monkeypatch, case):
    run, record, sample, root = chapter_run
    baseline_read = _tool_call("read_improvement_sample", **BASELINE_LOCATION, section="scores")
    evidence_ref = sample["id"]
    if case == "list_only":
        baseline_read = _tool_call("read_assessment_round", round_number=0, section="samples")
    if case == "dirty_worktree":
        (root / PROMPT).write_text("已有真实改动。", encoding="utf-8")
    if case == "foreign_ref":
        evidence_ref = "sample-other"
    calls = _script_model(monkeypatch, [
        baseline_read,
        _tool_call("finish_without_changes", reason="声称没有通用改法。", evidence_refs=[evidence_ref]),
        *[AIMessage(content="我准备结束。") for _ in range(4)],
    ])

    with pytest.raises(agent.IncompleteImprovement):
        asyncio.run(agent._improve(run, record, root, []))

    assert len(calls) == 6
    assert not sessions.get_session(record["session_id"])["change_log_path"]
    if case == "dirty_worktree":
        assert (root / PROMPT).read_text(encoding="utf-8") == "已有真实改动。"
