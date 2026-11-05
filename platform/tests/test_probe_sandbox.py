"""Candidate imports cannot cross the release worker's OS boundary."""
from __future__ import annotations

import socket


def _prepend(work, source):
    entry = work / "__init__.py"
    entry.write_text(source + "\n" + entry.read_text(encoding="utf-8"), encoding="utf-8")


def test_probe_cannot_read_other_task_files(client, harness_git, tmp_path):
    secret = tmp_path / "holdout-input.md"
    secret.write_text("private held-out task", encoding="utf-8")
    commit = harness_git.commit("attempt cross-task read", mutate=lambda work: _prepend(
        work, f"from pathlib import Path\nPath({str(secret)!r}).read_text()"))
    response = client.post("/api/release/probe", json={"source_commit": commit})
    assert response.json()["status"] == "rejected"
    assert "PermissionError" in response.json()["reason"]


def test_probe_cannot_write_outside_own_workspace(client, harness_git, tmp_path):
    protected = tmp_path / "service-config.py"
    protected.write_text("original", encoding="utf-8")
    commit = harness_git.commit("attempt service write", mutate=lambda work: _prepend(
        work, f"from pathlib import Path\nPath({str(protected)!r}).write_text('changed')"))
    response = client.post("/api/release/probe", json={"source_commit": commit})
    assert protected.read_text(encoding="utf-8") == "original"
    assert response.json()["status"] == "rejected"


def test_probe_does_not_inherit_service_secrets(client, harness_git, monkeypatch):
    monkeypatch.setenv("EVOLUTION_MASTER_KEY", "test-only-secret")
    commit = harness_git.commit("inspect inherited env", mutate=lambda work: _prepend(
        work, "import os\nif os.environ.get('EVOLUTION_MASTER_KEY'): raise RuntimeError('secret inherited')"))
    response = client.post("/api/release/probe", json={"source_commit": commit})
    assert response.json()["status"] == "ready", response.json()


def test_probe_cannot_connect_to_internal_services(client, harness_git):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        commit = harness_git.commit("attempt internal service access", mutate=lambda work: _prepend(
            work, f"import socket\nsocket.create_connection(('127.0.0.1', {port}), timeout=1).close()"))
        response = client.post("/api/release/probe", json={"source_commit": commit})
    assert response.json()["status"] == "rejected"
    assert "PermissionError" in response.json()["reason"]


def test_printed_ready_then_early_exit_cannot_forge_probe_result(client, harness_git):
    commit = harness_git.commit("forge stdout result", mutate=lambda work: _prepend(
        work, 'import os\nprint(\'{"status":"ready"}\', flush=True)\nos._exit(0)'))
    response = client.post("/api/release/probe", json={"source_commit": commit})
    assert response.json()["status"] == "rejected"
