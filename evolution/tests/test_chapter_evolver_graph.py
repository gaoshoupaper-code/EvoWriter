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
from app.experiments import agent, api, repo, runner
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


def _script_model(monkeypatch, responses):
    calls = []

    class ScriptedChapterModel(FakeMessagesListChatModel):
        def __init__(self, **_kwargs):
            super().__init__(responses=responses)

        def bind_tools(self, _tools, **_kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(list(messages))
            assert len(calls) <= len(responses), "流程超过预期的模型调用上限"
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", ScriptedChapterModel)
    return calls


def _tool_call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


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
