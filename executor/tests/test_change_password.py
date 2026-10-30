"""本人改密：凭证校验、会话撤销与事务回滚。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
from app.platform.core.db import Database, SessionRepository, UserRepository, sqlite3
from app.platform.core.security import generate_master_key, load_master_key, verify_password


@pytest.fixture()
def account(tmp_path, monkeypatch):
    db = Database(tmp_path / "auth.db", load_master_key(generate_master_key()))
    monkeypatch.setattr(auth, "get_database", lambda: db)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(
        session_ttl_days=30, writer_frontend_origin="https://testserver",
    ))
    users = UserRepository(db)
    user = users.create(username="alice", password="old-password")
    other = users.create(username="bob", password="bob-password")
    sessions = SessionRepository(db)
    current_session = sessions.create(user_id=user["user_id"], ttl_days=30)
    other_session = sessions.create(user_id=user["user_id"], ttl_days=30)
    bob_session = sessions.create(user_id=other["user_id"], ttl_days=30)
    app = FastAPI()
    app.include_router(auth.auth_router)
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(auth.SESSION_COOKIE, current_session)
        yield SimpleNamespace(
            db=db, client=client, users=users, user=user, other=other,
            sessions=sessions, current_session=current_session,
            other_session=other_session, bob_session=bob_session,
        )
    db.close()


def change(account, **overrides):
    payload = {"current_password": "old-password", "new_password": "new-password"}
    payload.update(overrides)
    return account.client.post("/api/auth/change-password", json=payload)


def test_change_password_keeps_current_session_and_revokes_others(account):
    response = change(account)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    user = account.users.get_by_id(account.user["user_id"])
    assert user["password_hash"] != "new-password"
    assert verify_password("new-password", user["password_hash"])
    assert not verify_password("old-password", user["password_hash"])
    assert account.sessions.get(account.current_session) is not None
    assert account.sessions.get(account.other_session) is None
    assert account.sessions.get(account.bob_session) is not None
    assert account.client.get("/api/auth/me").status_code == 200
    assert account.client.post("/api/auth/login", json={
        "username": "alice", "password": "old-password",
    }).status_code == 401
    assert account.client.post("/api/auth/login", json={
        "username": "alice", "password": "new-password",
    }).status_code == 200
    account.client.cookies.clear()
    account.client.cookies.set(auth.SESSION_COOKIE, account.other_session)
    assert account.client.get("/api/auth/me").status_code == 401


def test_wrong_current_password_does_not_log_out_or_mutate(account):
    response = change(account, current_password="wrong-password")
    assert response.status_code == 400
    assert response.json()["detail"] == "当前密码错误"
    assert account.users.verify("alice", "old-password") is not None
    assert account.sessions.get(account.other_session) is not None
    assert account.client.get("/api/auth/me").status_code == 200


def test_unchanged_password_rejected(account):
    response = change(account, new_password="old-password")
    assert response.status_code == 400
    assert account.users.verify("alice", "old-password") is not None
    assert account.sessions.get(account.other_session) is not None


@pytest.mark.parametrize("password", ["", "12345", "x" * 129])
def test_new_password_length_rejected(account, password):
    assert change(account, new_password=password).status_code == 422
    assert account.users.verify("alice", "old-password") is not None
    assert account.sessions.get(account.other_session) is not None


@pytest.mark.parametrize("password", ["123456", "x" * 128, " 新密码含空格 "])
def test_password_boundaries_and_whitespace_preserved(account, password):
    assert change(account, new_password=password).status_code == 200
    assert account.users.verify("alice", password) is not None


@pytest.mark.parametrize("field", ["current_password", "new_password"])
def test_missing_password_rejected(account, field):
    payload = {"current_password": "old-password", "new_password": "new-password"}
    del payload[field]
    assert account.client.post("/api/auth/change-password", json=payload).status_code == 422


@pytest.mark.parametrize("session", [None, "unknown-session", "expired"])
def test_requires_valid_session(account, session):
    if session == "expired":
        session = account.current_session
        with account.db.transaction() as conn:
            conn.execute("UPDATE sessions SET expires_at = ? WHERE session_id = ?", (
                "2000-01-01T00:00:00+00:00", session,
            ))
    account.client.cookies.clear()
    if session:
        account.client.cookies.set(auth.SESSION_COOKIE, session)
    assert change(account).status_code == 401
    assert account.users.verify("alice", "old-password") is not None


def test_cannot_select_another_user(account):
    assert change(account, user_id=account.other["user_id"]).status_code == 200
    assert account.users.verify("alice", "new-password") is not None
    assert account.users.verify("bob", "bob-password") is not None
    assert account.sessions.get(account.bob_session) is not None


def test_disabled_user_cannot_change_password(account):
    account.users.set_disabled(account.user["user_id"], True)
    assert change(account).status_code == 401
    user = account.users.get_by_id(account.user["user_id"])
    assert verify_password("old-password", user["password_hash"])


def test_revoked_session_cannot_change_password_after_authentication(account):
    account.sessions.delete(account.current_session)
    assert not account.users.change_password(
        account.user["user_id"], "old-password", "new-password", account.current_session,
    )
    assert account.users.verify("alice", "old-password") is not None


def test_stale_credentials_cannot_overwrite_new_password(account):
    assert change(account).status_code == 200
    response = change(account, new_password="another-password")
    assert response.status_code == 400
    assert account.users.verify("alice", "new-password") is not None


def test_old_password_login_cannot_create_session_after_password_change(account, monkeypatch):
    original_verify = UserRepository.verify

    def verify_then_change(repo, username, password):
        verified = original_verify(repo, username, password)
        assert verified is not None
        assert repo.change_password(
            account.user["user_id"], "old-password", "new-password", account.current_session,
        )
        return verified

    monkeypatch.setattr(UserRepository, "verify", verify_then_change)
    response = account.client.post("/api/auth/login", json={
        "username": "alice", "password": "old-password",
    })
    assert response.status_code == 401
    rows = account.db.conn.execute(
        "SELECT session_id FROM sessions WHERE user_id = ?", (account.user["user_id"],),
    ).fetchall()
    assert [row["session_id"] for row in rows] == [account.current_session]


def test_session_revocation_failure_rolls_back_password(account):
    with account.db.transaction() as conn:
        conn.execute("""
            CREATE TRIGGER reject_session_delete BEFORE DELETE ON sessions
            BEGIN SELECT RAISE(ABORT, 'test revocation failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="test revocation failure"):
        change(account)
    assert account.users.verify("alice", "old-password") is not None
    assert account.sessions.get(account.current_session) is not None
    assert account.sessions.get(account.other_session) is not None
