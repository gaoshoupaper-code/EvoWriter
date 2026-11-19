"""Version browsing is scoped to the selected round's retained bundle."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db
from app.core.settings import settings
from app.experiments import api, assessment, repo, snapshots


def test_round_source_is_private_and_uses_only_the_archived_version(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setattr(settings, "evolution_db", str(tmp_path / "data" / "experiments.db"))
    monkeypatch.setenv("ASSESSMENT_SNAPSHOT_DIR", str(tmp_path / "common"))
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_conn", conn)
    db.init_db()
    repo.init_schema()
    tree = tmp_path / "harness"
    tree.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tree, encoding="utf-8").strip()
    git("init", "-q")
    path = "prompts/写作.md"
    (tree / "prompts").mkdir()
    (tree / "registry.json").write_text("platform internal", encoding="utf-8")
    def version(text):
        (tree / path).write_bytes(text.encode("utf-8"))
        git("add", ".")
        git("-c", "user.name=tests", "-c", "user.email=t@local", "commit", "-qm", "test version")
        commit = git("rev-parse", "HEAD")
        snapshots.save(tree, commit)
        return commit
    original = version("基线\n")
    failed = version("尝试过的改法\n\n")
    foreign = version("另一档案\n")
    for run_id, candidates in [("this-run", [original, failed]), ("other-run", [foreign])]:
        repo.insert("self_evolution_runs", {"id": run_id, "created_at": repo.now(),
                    "assessment_version": 1, "flow_version": 3, "task_type": "chapter_text"})
        for number, commit in enumerate(candidates):
            snapshots.retain(run_id, commit)
            entry = "baseline" if number == 0 else f"round-{number}"
            conn.execute("INSERT INTO assessment_entries VALUES(?,?,?,?,?,?)", (
                run_id, "version", entry, number, commit, json.dumps({"status": "failed" if number else "baseline"})))
    conn.commit()

    app = FastAPI()
    @app.middleware("http")
    async def grant_read(request, call_next):
        request.state.is_super_admin = request.headers.get("role") == "reader"
        return await call_next(request)
    app.include_router(api.router)
    with TestClient(app) as client:
        route = "/experiments/runs/this-run/versions/"
        reader = {"role": "reader"}
        assert client.get(route + "0/source").status_code == 403
        assert client.get(route + "0/source", headers=reader).json() == {"commit": original, "paths": [path]}
        assert client.get(route + "0/source", headers=reader, params={"path": path}).json()["content"] == "基线\n"
        assert client.get(route + "1/source", headers=reader, params={"path": path}).json() == {
            "commit": failed, "path": path, "content": "尝试过的改法\n\n"}
        assert client.get(route + "3/source", headers=reader).status_code == 404
        for invalid in ["registry.json", ".git/config", "../other-run", "prompts/missing.md"]:
            assert client.get(route + "1/source", headers=reader, params={"path": invalid}).status_code == 404
        assert client.get(route + "1/source", headers=reader, params={"commit": foreign}).is_client_error
        bundle = assessment.artifact_directory("this-run").parent / "versions" / (failed + ".bundle")
        bundle.unlink()
        assert snapshots.snapshot_file(failed).is_file()
        assert client.get(route + "1/source", headers=reader).status_code == 404
        monkeypatch.setenv("WRITING_AGENT_ID", "chapter")
        assert client.get(route + "0/source", headers=reader).status_code == 404
    conn.close()
