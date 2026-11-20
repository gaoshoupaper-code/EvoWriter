from fastapi.testclient import TestClient

from app.core import db
from app.core.settings import settings
from app.core.sso_auth import SSOAuthMiddleware
from app.main import app
from app.versioning import manual_assets


def test_manual_draft_uses_evolution_sso_and_the_configured_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allowed_user_ids", "asset-owner")
    monkeypatch.setattr(settings, "evolution_db", str(tmp_path / "evolution.db"))
    monkeypatch.setattr(app, "middleware_stack", None)
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.delenv("WRITING_AGENT_ID", raising=False)

    async def verify_session(_self, session):
        return session, False

    monkeypatch.setattr(SSOAuthMiddleware, "_verify_with_executor", verify_session)
    production = {"version": 1, "commit": "a" * 40}
    monkeypatch.setattr(manual_assets.clients, "production", lambda: production)
    db.init_db()
    try:
        client = TestClient(app)
        url = "/api/harness/manual-draft"
        assert client.get(url).status_code == 401
        assert client.post(url + "/release", json={"base_version": 1, "revision": 0}).status_code == 401
        assert client.get(url, headers={"Cookie": "session=another-user"}).status_code == 403

        signed_in = {"Cookie": "session=asset-owner"}
        assert client.get(url, headers=signed_in).status_code == 404
        for index, agent in enumerate(("outline", "volume", "chapter", "text"), start=1):
            monkeypatch.setenv("WRITING_AGENT_ID", agent)
            production.update(version=index, commit=str(index) * 40)
            response = client.get(url, headers=signed_in)
            assert response.status_code == 200, response.text
            assert response.json()["base_version"] == index
            assert response.json()["revision"] == 0
    finally:
        db.get_conn().close()
