"""A candidate package cannot replace a different creative Harness."""

import json

import pytest


@pytest.mark.parametrize("agent, foreign", [("outline", "text"), ("volume", "outline"),
                                            ("chapter", "volume"), ("text", "chapter")])
def test_release_scope_rejects_a_foreign_candidate(client, harness_git, monkeypatch, agent, foreign):
    assistants = {"outline": [], "volume": ["volume_qc"], "chapter": [], "text": ["review"]}
    actors = [{"role": "main", "name": foreign, "delegates": assistants[foreign]}]
    actors.extend({"role": "subagent", "name": name} for name in assistants[foreign])
    candidate = harness_git.commit(
        "foreign harness",
        mutate=lambda work: (work / "architecture.json").write_text(json.dumps({"agents": actors}), encoding="utf-8"),
    )
    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    before = client.get("/api/production").json()
    result = client.post("/api/release/probe", json={"source_commit": candidate}).json()
    assert result["status"] == "rejected"
    assert "归属与本套不符" in result["reason"]
    assert client.post("/api/artifacts/prepare", json={"source_commit": candidate}).status_code == 409
    assert client.get("/api/production").json() == before
