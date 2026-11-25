"""Current automated evolution assets use the existing evaluation authentication."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.tools import BaseTool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.core.settings import settings
from app.core.sso_auth import SSOAuthMiddleware
from app.experiments import api, repo


URL = "/api/experiments/evolver-assets"
COMMON_TOOLS = {
    "list_elements", "read_source", "inspect_state_schema", "read_assemble", "inspect_middleware_protocol",
    "write_prompt", "write_middleware", "write_tool", "write_skill", "write_subagent", "edit_source", "delete_file",
    "write_design_doc", "validate_changes", "write_change_log",
}
ASSESSMENT_TOOLS = {
    "list_assessment_history", "search_assessment_history", "read_assessment_round", "read_improvement_sample",
    "list_writing_events", "read_writing_event", "finish_without_changes",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "allowed_user_ids", "reader")

    async def verify_session(_middleware, session):
        return session, False

    def unexpected_call(*_args, **_kwargs):
        pytest.fail("Reading assets must not load an experiment, access credentials, start a model, or invoke a tool")

    monkeypatch.setattr(SSOAuthMiddleware, "_verify_with_executor", verify_session)
    monkeypatch.setattr(repo, "get_run", unexpected_call)
    monkeypatch.setattr(repo, "decrypt_credentials", unexpected_call)
    monkeypatch.setattr(db, "query_one", unexpected_call)
    monkeypatch.setattr("deepagents.create_deep_agent", unexpected_call)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", unexpected_call)
    monkeypatch.setattr(BaseTool, "invoke", unexpected_call)
    monkeypatch.setattr(BaseTool, "ainvoke", unexpected_call)
    app = FastAPI()
    app.add_middleware(SSOAuthMiddleware)
    app.include_router(api.router, prefix="/api")
    return TestClient(app)


@pytest.mark.parametrize("suite", ["outline", "volume", "chapter", "text"])
def test_evolver_assets_follow_the_authenticated_suite(client, monkeypatch, suite):
    monkeypatch.setenv("WRITING_AGENT_ID", suite)
    client.cookies.set("session", "reader")
    response = client.get(URL)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"system_prompt", "tools"}
    assert data["system_prompt"].startswith("你是Writer Harness进化工程师。")
    is_text = suite == "text"
    assert ("先查第 0 轮原版" in data["system_prompt"]) == is_text
    names = {tool["name"] for tool in data["tools"]}
    expected = COMMON_TOOLS | ASSESSMENT_TOOLS if is_text else COMMON_TOOLS
    assert names == expected
    assert len(data["tools"]) == len(expected)
    assert all(set(tool) == {"name", "description"} and tool["description"].strip() for tool in data["tools"])
    # REQ-20261125-210621 FR-002/AC-003：改工具描述的规则只进正文套件的提示。
    assert ("某一类句子反复写不到位" in data["system_prompt"]) == is_text


def test_text_evolver_prompt_routes_repeated_sentence_problems_to_the_tool_description(client, monkeypatch):
    """REQ-20261125-210621 FR-002/AC-003：规则写明信号、动作和五类句子对应的工具；对话式进化的提示不变。"""
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    client.cookies.set("session", "reader")
    prompt = client.get(URL).json()["system_prompt"]

    sentence = next((part for part in prompt.split("。") if "某一类句子反复写不到位" in part), "")
    assert "动作句缺来路" in sentence
    assert all(tool in prompt.split("某一类句子反复写不到位", 1)[1].split("\n", 1)[0]
               for tool in ("write_action", "write_dialogue", "write_sensory", "write_reaction", "write_narration"))

    from app.evolve.agent.prompt import evolve_system_prompt
    assert "某一类句子反复写不到位" not in evolve_system_prompt("s", "", "")


@pytest.mark.parametrize(("session", "status"), [(None, 401), ("outside-allowlist", 403)])
def test_evolver_assets_follow_existing_access_boundary(client, monkeypatch, session, status):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    if session:
        client.cookies.set("session", session)
    response = client.get(URL)
    assert response.status_code == status
    assert set(response.json()) == {"detail"}
