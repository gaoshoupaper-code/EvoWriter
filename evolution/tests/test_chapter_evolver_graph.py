"""Exercise the production chapter-evolution graph with a scripted chat model."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import deepagents
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.core.settings import settings
from app.evolve import docs
from app.experiments import agent, api, repo


def test_real_chapter_evolution_graph_queries_evidence_and_commits_a_design(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
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
    monkeypatch.setattr(settings, "evolution_db", str(tmp_path / "evolution.db"))
    monkeypatch.setattr(docs, "_docs_root", lambda: tmp_path / "docs")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", conn)
    db.init_db()
    repo.init_schema()
    package = next(item for item in repo.list_packages() if item["purpose"] == "optimization")
    prompt = "prompts/creative_text.md"
    root = tmp_path / "harness"
    (root / "prompts").mkdir(parents=True)
    (root / prompt).write_text("按章纲写正文。", encoding="utf-8")
    record = {"round": 1, "base_commit": "a" * 40, "session_id": "graph-evolve-1", "status": "evolving"}
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
    location = {"round_number": 0, "sample_number": 1}
    steps = [
        ("list_assessment_history", {}),
        ("read_assessment_round", {"round_number": 0, "section": "samples"}),
        ("read_improvement_sample", {**location, "section": "scores"}),
        ("read_improvement_sample", {**location, "section": "manuscript"}),
        ("list_writing_events", {**location, "trace_number": 1}),
        ("read_writing_event", {**location, "trace_number": 1, "sequence": 1}),
        ("search_assessment_history", {"query": "外档案独有原文"}),
        ("read_source", {"file_path": prompt}),
        ("write_design_doc", {}),
        ("edit_source", {"file_path": prompt, "old_string": "按章纲写正文。", "new_string": "在揭示真相前呈现线索。"}),
        ("validate_changes", {}),
        ("write_change_log", {"summary": "增加推理线索", "applied": [
            {"target": prompt, "action": "edit_source", "result": "ok", "detail": "写明线索要求", "design_ref": 1}]}),
    ]

    class ChapterModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert {tool.name: tool.description for tool in tools} == tool_descriptions
            registered.append({tool.name for tool in tools})
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.i == 0:
                assert all("先前评测秘密" not in str(message.content) for message in messages)
                assert all("此前线索未呈现" not in str(message.content) for message in messages)
            else:
                result = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
                replies[self.i - 1] = result.content
                assert result.status == "success", result.content
            if self.i == len(steps):
                response = AIMessage(content="改动已验证。")
            else:
                name, args = steps[self.i]
                assert name in registered[-1]
                if name == "write_design_doc":
                    listed = json.loads(replies[1])["items"][0]
                    args = {"rationale": "先呈现线索，读者才能理解人物判断。", "changes": [{
                        "target": prompt, "change_desc": "要求先写出人物看到的线索", "reason": "原稿直接揭晓",
                        "evidence_ref": [listed["evidence_ref"]], "expected_up": "因果衔接", "expected_down": "无"}]}
                response = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"step-{self.i}"}])
            self.i += 1
            return ChatResult(generations=[ChatGeneration(message=response)])

    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda **kwargs: ChapterModel(responses=[]))
    monkeypatch.setattr(repo, "decrypt_credentials", lambda _: {
        "model": "scripted", "api_key": "test-only", "base_url": "http://127.0.0.1"})
    try:
        result = asyncio.run(agent._improve(run, record, root, [{"prior": "先前评测秘密"}]))
        assert set(replies) == set(range(len(steps)))
        assert all(not ({"read_work_trace", "read_agent_session", "read_file", "task"} & names)
                   for names in registered)
        assert "此前线索未呈现" in replies[2] and "稚禾当即猜到真相" in replies[3]
        assert "tool_end" in replies[4] and "补入线索" in replies[5]
        assert json.loads(replies[6])["items"] == []
        assert "设计文档已产出" in replies[8] and json.loads(replies[10])["passed"]
        assert (root / prompt).read_text(encoding="utf-8") == "在揭示真相前呈现线索。"
        assert result["validated"] and "增加推理线索" in result["summary"]
        saved = repo.get_run(run["id"])["rounds"][0]
        assert saved["evidence_samples"] == [sample["id"]] and "线索" in saved["design"]
    finally:
        conn.close()
