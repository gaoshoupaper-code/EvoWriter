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


def test_runtime_reports_loaded_commit_without_reload(monkeypatch):
    from app.platform.agent import loader
    monkeypatch.setattr(loader, "production_commit", lambda: "a" * 40)
    monkeypatch.setattr(loader, "production_checkout", lambda: None)
    monkeypatch.setattr(loader, "reload_current", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not reload")))
    response = _client().get("/internal/target-tasks/runtime")
    assert response.status_code == 200
    assert response.json()["commit"] == "a" * 40


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
