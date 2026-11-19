"""Run a released middleware hook in executor, ingest its JSONL, and read the scoped API."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENTS = ("outline", "volume", "chapter", "text")
RULE = "middleware/path_guard.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def exercise_released_rules(directory):
    # Executor and evolution both have a top-level package called app.
    # Use a separate interpreter for the real executor recorder and its dependencies.
    from functools import partial
    from types import SimpleNamespace

    from langchain.tools.tool_node import ToolCallRequest
    from app.platform.trace.recorder import TraceRecorder
    from app.schemas.screenplay import ThreadSummary

    for agent in AGENTS:
        stage = directory / agent
        middleware = load_module("released_" + agent, stage / "released" / RULE)
        workspace = stage / "workspace"
        thread = ThreadSummary(thread_id="thread", workspace_id=agent, session_name=agent,
                               workspace_path=str(workspace), created_at="2026-11-19", updated_at="2026-11-19")
        for label, path in (("changed", "draft.md"), ("unchanged", "/draft.md")):
            recorder = TraceRecorder()
            trace = recorder.create_run(thread, "creative.generate")
            callback = partial(recorder.record_intervention, trace.trace_id, agent)
            hook = middleware.build(SimpleNamespace(workspace_path=workspace,
                                    agent=SimpleNamespace(write_permissions=["/draft.md"]),
                                    intervention_callback=callback))
            request = ToolCallRequest(tool_call={"id": "tool-1", "name": "write_file", "args": {"file_path": path}},
                                      tool=None, state={}, runtime=None)
            assert hook.wrap_tool_call(request, lambda r: r.tool_call["args"]["file_path"]) == "/draft.md"
            recorder.complete_run(thread, trace.trace_id)
            (stage / (label + ".txt")).write_text(trace.trace_id, encoding="utf-8")


def test_only_text_runs_new_rule_and_identical_versions_show_different_interventions(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core import db
    from app.core.settings import settings
    from app.ingestion.importer import ingest_trace
    from app.versioning import asset_candidates as assets
    from app.versioning.seed_agent import initialize
    from app.view.traces import router
    from contracts.platform import SurfaceFingerprint

    ledger_type = load_module("test_isolated_platform_ledger", ROOT / "platform/app/core/ledger.py").Ledger
    monkeypatch.setenv("SHARED_MIDDLEWARE_DIR", str(tmp_path / "common"))
    old_digests = set()
    for agent in AGENTS:
        stage = tmp_path / agent
        bare = stage / "bare"
        monkeypatch.setattr(settings, "harness_work_dir", str(stage / "repo"))
        monkeypatch.setattr(settings, "harness_bare_repo", str(bare))
        initialize(ROOT / "evolution/harnesses/repo", agent)
        ledger = ledger_type(stage / "platform.db")
        ledger.ensure_schema()
        ledger.import_registry(json.loads((stage / "repo/registry.json").read_text(encoding="utf-8")))
        base = ledger.get_production().commit
        monkeypatch.setattr(assets, "_production", lambda: ledger.get_production().commit)
        old = assets.share_released_rule("path_guard", assets.ShareRule(base_commit=base))["version"]
        old_digests.add(old)
        if agent == "text":
            content = assets.shared_versions("path_guard", old)["content"]
            edited = content.replace('                "modify",', '                "normalize_path",')
            assert edited != content
            commit = assets.create_candidate(base, {RULE: edited})["commit"]
            assets.validate_candidate(commit, base)
            digest = hashlib.sha256(edited.encode()).hexdigest()
            ledger.apply_promotion(commit=commit, note="scoped middleware upgrade",
                                   artifact_digest=digest, fingerprint=SurfaceFingerprint(c_code={RULE: digest}),
                                   expected_production_commit=base, expected_production_version=1)
            new = assets.share_released_rule("path_guard", assets.ShareRule(base_commit=commit))["version"]
            assert new != old
        pointer = ledger.get_production()
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(bare), str(stage / "released")], check=True)
        subprocess.run(["git", "checkout", "--quiet", pointer.commit], cwd=stage / "released", check=True)
        assert pointer.version == (2 if agent == "text" else 1)
    assert len(old_digests) == 1

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(map(str, (ROOT / "executor", ROOT))), "PYTHONUTF8": "1"}
    subprocess.run([sys.executable, __file__, str(tmp_path)], cwd=ROOT / "executor", env=env, check=True)
    api = FastAPI()
    api.include_router(router, prefix="/api")
    client = TestClient(api)
    for agent in AGENTS:
        stage = tmp_path / agent
        monkeypatch.setattr(settings, "evolution_db", str(stage / "evolution.db"))
        monkeypatch.setattr(db, "_conn", None)
        db.init_db()
        try:
            for label in ("changed", "unchanged"):
                trace = (stage / (label + ".txt")).read_text(encoding="utf-8")
                assert ingest_trace(next((stage / "workspace/traces").rglob(trace + ".jsonl"))) == trace
                response = client.get(f"/api/traces/{trace}/middleware-actions")
                assert response.status_code == 200
                items = response.json()["items"]
                if label == "unchanged":
                    assert items == []
                else:
                    assert [(item["rule"], item["action"], item["hook"]) for item in items] == [
                        ("FilesystemPathGuardMiddleware", "normalize_path" if agent == "text" else "modify", "wrap_tool_call")]
            foreign = "volume" if agent == "outline" else "outline"
            trace = (tmp_path / foreign / "changed.txt").read_text(encoding="utf-8")
            assert client.get(f"/api/traces/{trace}/middleware-actions").status_code == 404
        finally:
            db.get_conn().close()


if __name__ == "__main__":
    exercise_released_rules(Path(sys.argv[1]))
