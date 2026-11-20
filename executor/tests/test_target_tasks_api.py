"""Target-task routes must keep owner and production/runtime identities separate."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import current_user
from app.routers import target_tasks


def _client():
    app = FastAPI()
    app.include_router(target_tasks.router)
    app.include_router(target_tasks.internal_router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user_id="owner-one")
    return TestClient(app)


def test_runtime_reports_loaded_release_when_ledger_moves_to_same_commit(tmp_path, monkeypatch):
    from app.platform.agent import loader

    commit, digest = "a" * 40, "b" * 64
    (tmp_path / ".digest").write_text(digest, encoding="utf-8")
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    status = SimpleNamespace(version=1, commit=commit, artifact_digest=digest)
    monkeypatch.setattr(loader, "_artifact_client", lambda: SimpleNamespace(
        current_production=lambda: status, ensure_artifact=lambda *_a, **_k: tmp_path,
    ))
    loader.reset_cache()
    try:
        loader.load_current_package()
        status.version = 2
        client = _client()
        response = client.get("/internal/target-tasks/runtime")
        assert response.status_code == 200
        assert response.json() == {"version": 1, "commit": commit, "artifact_digest": digest}

        loader.reload_current(commit, digest, version=2)
        loader.reload_current(commit, digest, version=1)
        assert client.get("/internal/target-tasks/runtime").json() == {
            "version": 2, "commit": commit, "artifact_digest": digest,
        }
    finally:
        loader.reset_cache()


def test_public_start_rejects_unowned_workspace(monkeypatch):
    monkeypatch.setattr(target_tasks, "get_thread_store", lambda: SimpleNamespace(get_workspace=lambda *args: None))
    response = _client().post("/api/workspaces/foreign/target-tasks", json={
        "target": {"task_type": "chapter_text", "chapter": 1},
        "confirmation_rules": "自行决定并确认通过",
    })
    assert response.status_code == 404


def test_public_input_cannot_select_candidate_commit():
    response = _client().post("/api/workspaces/work-one/target-tasks", json={
        "target": {"task_type": "chapter_text", "chapter": 1},
        "confirmation_rules": "自行决定并确认通过", "source_commit": "b" * 40,
    })
    assert response.status_code == 422
