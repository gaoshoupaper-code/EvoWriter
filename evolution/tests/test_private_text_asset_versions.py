"""Editing/reading an activated private Harness does not publish its ancestry."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import settings
from app.experiments import snapshots
from app.versioning import asset_candidates as assets
from app.versioning import elements_api, platform_ledger, upgrade_diff


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, encoding="utf-8").strip()


def test_active_bundle_source_manual_candidate_and_chat_freeze(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITING_AGENT_ID", "text")
    monkeypatch.setenv("ASSESSMENT_SNAPSHOT_DIR", str(tmp_path / "bundles"))
    monkeypatch.setattr(settings, "evolution_db", str(tmp_path / "data" / "evolution.db"))
    monkeypatch.setenv("SHARED_MIDDLEWARE_DIR", str(tmp_path / "shared"))
    base_work = tmp_path / "seed"
    base_work.mkdir()
    git(base_work, "init", "-b", "main")
    name = "prompts/storybuilding_system.md"
    (base_work / "prompts").mkdir()
    (base_work / name).write_text("base", encoding="utf-8")
    git(base_work, "add", ".")
    git(base_work, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-m", "base")
    baseline = git(base_work, "rev-parse", "HEAD")
    bare = tmp_path / "original.git"
    git(tmp_path, "clone", "--bare", str(base_work), str(bare))
    monkeypatch.setattr(settings, "harness_bare_repo", str(bare))

    # Older text releases also work as the source of a private manual edit.
    monkeypatch.setattr(assets, "_production", lambda: baseline)
    private = assets.create_candidate(baseline, {name: "private manuscript\n"})["commit"]
    assert (tmp_path / "bundles" / f"{private}.bundle").is_file()
    monkeypatch.setattr(assets, "_production", lambda: private)
    assert name in assets.source()["paths"]
    assert assets.source(name)["content"] == "private manuscript\n"
    monkeypatch.setattr(elements_api, "_version_to_commit", lambda version: private)
    assert elements_api.get_source(2, name)["content"] == "private manuscript\n"
    assert elements_api._list_files_at_commit(private, "prompts") == [name]
    view = elements_api.build_elements_view(2)
    assert any("private manuscript" in agent["prompt"]["body"] for agent in view["agents"])
    assert platform_ledger._resolve_based_on(private, {baseline: 1}) == (1, "resolved")
    monkeypatch.setattr(platform_ledger, "get_version", lambda _: {
        "version": 2, "commit": private, "based_on_status": "resolved", "based_on": 1})
    monkeypatch.setattr(platform_ledger, "resolve_commit", lambda _: baseline)
    difference = upgrade_diff.build_upgrade_diff(2)
    assert difference["base_kind"] == "ancestor" and difference["changes"]["agents"]
    edited = assets.create_candidate(private, {"skills/editor/SKILL.md": "new skill", "text_completion.py": "# chapter completion\n"})["commit"]
    assets.validate_candidate(edited, private)
    with pytest.raises(ValueError):
        assets.validate_candidate(edited, baseline)
    session = assets.session_checkout("example")
    assert git(session, "remote") == ""
    (session / name).write_text("chat edit", encoding="utf-8")
    frozen = assets.freeze_session("example")
    assert assets.freeze_session("example") == frozen
    assert git(session, "rev-parse", "HEAD") == private
    assets.validate_candidate(frozen["source_commit"], private)
    recovered = tmp_path / "recovered"
    snapshots.checkout_base(frozen["source_commit"], recovered)
    assert (recovered / name).read_text(encoding="utf-8") == "chat edit"
    assert git(bare, "show-ref") == f"{baseline} refs/heads/main"
    for commit in (private, edited, frozen["source_commit"]):
        assert subprocess.run(["git", "cat-file", "-e", commit], cwd=bare, capture_output=True).returncode != 0
