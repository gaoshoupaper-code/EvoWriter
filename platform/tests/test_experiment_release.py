"""Candidate preparation and compare-and-set publication use the real ledger."""
from __future__ import annotations

from contracts.platform import HarnessReloadNotice

from app import release as release_mod
from app.core.ledger import Ledger


def test_prepare_candidate_does_not_promote(client, platform_env, harness_git):
    candidate = harness_git.commit(
        "candidate only",
        mutate=lambda work: (work / "candidate.md").write_text("candidate", encoding="utf-8"),
    )
    before = client.get("/api/production").json()
    response = client.post("/api/artifacts/prepare", json={"source_commit": candidate})
    assert response.status_code == 200
    meta = response.json()
    assert meta["commit"] == candidate
    assert len(meta["digest"]) == 64
    assert client.get(f"/api/artifacts/{candidate}/download").status_code == 200
    after = client.get("/api/production").json()
    assert (after["version"], after["commit"]) == (before["version"], before["commit"])
    ledger = Ledger(platform_env.db)
    assert ledger.max_version() == before["version"]
    assert ledger.get_manifest_by_commit(candidate).status == "candidate"


def test_prepare_unknown_candidate_is_rejected(client):
    response = client.post("/api/artifacts/prepare", json={"source_commit": "a" * 40})
    assert response.status_code == 409


def test_publish_checks_production_version_even_when_commit_returns_to_same_value(
    client, platform_env, harness_git, monkeypatch,
):
    monkeypatch.setattr(release_mod, "notify_reload", lambda _notice: True)
    initial = client.get("/api/production").json()
    advanced = client.post("/api/release/promote", json={"source_commit": harness_git.head})
    assert advanced.status_code == 200
    response = client.post(
        "/api/release/promote",
        json={
            "source_commit": harness_git.head,
            "expected_production_commit": initial["commit"],
            "expected_production_version": initial["version"],
        },
    )
    assert response.status_code == 409
    assert Ledger(platform_env.db).max_version() == advanced.json()["version"]


def test_reload_ack_must_report_the_requested_commit(monkeypatch):
    class WrongIdentity:
        status_code = 200

        def json(self):
            return {"status": "reloaded", "commit": "b" * 40}

    monkeypatch.setattr(release_mod.httpx, "post", lambda *_a, **_kw: WrongIdentity())
    monkeypatch.setattr(release_mod, "NOTIFY_ATTEMPTS", 1)
    assert release_mod.notify_reload(HarnessReloadNotice(version=2, commit="a" * 40)) is False


def test_restore_old_release_records_a_new_version(client, platform_env, harness_git, monkeypatch):
    monkeypatch.setattr(release_mod, "notify_reload", lambda _notice: True)
    candidate = harness_git.commit(
        "next release",
        mutate=lambda work: (work / "release.md").write_text("next", encoding="utf-8"),
    )
    promoted = client.post("/api/release/promote", json={"source_commit": candidate})
    assert promoted.status_code == 200
    response = client.post(
        "/api/release/restore",
        json={
            "source_commit": harness_git.head,
            "expected_production_commit": candidate,
            "expected_production_version": promoted.json()["version"],
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["commit"] == harness_git.head
    assert result["version"] > promoted.json()["version"]
    assert Ledger(platform_env.db).get_production().commit == harness_git.head
