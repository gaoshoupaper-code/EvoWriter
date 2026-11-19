from pathlib import Path
import json
import subprocess

import pytest

from app.core.settings import settings
from app.versioning import asset_candidates as assets
from app.versioning.seed_agent import initialize


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def test_candidate_is_private_immutable_and_based_on_the_selected_production(tmp_path, monkeypatch):
    source = tmp_path / "work"
    source.mkdir()
    git(source, "init", "-b", "main")
    (source / "prompts").mkdir()
    (source / "prompts" / "author.md").write_text("before", encoding="utf-8")
    git(source, "add", ".")
    git(source, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    base = git(source, "rev-parse", "HEAD")
    bare = tmp_path / "scope.git"
    subprocess.run(["git", "clone", "--bare", str(source), str(bare)], check=True)
    monkeypatch.setattr(settings, "harness_bare_repo", str(bare))
    monkeypatch.setattr(assets, "_production", lambda: base)
    candidate = assets.create_candidate(base, {"prompts/author.md": "after"})
    commit = candidate["commit"]
    assets.validate_candidate(commit, base)
    assert git(bare, "rev-parse", "main") == base
    assert git(bare, "show", commit + ":prompts/author.md") == "after"
    assert "before" in candidate["diff"] and "after" in candidate["diff"]
    added = assets.create_candidate(base, {"skills/editor/SKILL.md": "review the scene"})
    assert git(bare, "show", added["commit"] + ":skills/editor/SKILL.md") == "review the scene"
    assert git(bare, "ls-tree", "-r", "main", "skills") == ""
    with pytest.raises(ValueError):
        assets.validate_candidate(base, base)
    with pytest.raises(ValueError):
        assets.validate_candidate(commit, "0" * 40)
    for path in ("registry.json", "../question.json", "prompts/../registry.json"):
        with pytest.raises(ValueError):
            assets.create_candidate(base, {path: "forbidden"})


def test_four_seed_histories_and_restarts(tmp_path, monkeypatch):
    seed = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
    commits = set()
    for agent, assistants in (("outline", []), ("volume", ["volume_qc"]), ("chapter", []), ("text", ["review"])):
        directory = tmp_path / agent
        root, bare = directory / "repo", directory / "bare"
        monkeypatch.setattr(settings, "harness_work_dir", str(root))
        monkeypatch.setattr(settings, "harness_bare_repo", str(bare))
        initialize(seed, agent)
        registry = json.loads(git(bare, "show", "main:registry.json"))
        base = registry["versions"][0]["commit_hash"]
        assert registry["production"] == 1 and len(registry["versions"]) == 1
        actors = json.loads(git(bare, "show", base + ":architecture.json"))["agents"]
        assert [a["name"] for a in actors] == [agent, *assistants]
        commits.add(base)
        first_head = git(bare, "rev-parse", "main")
        initialize(seed, agent)
        assert git(bare, "rev-parse", "main") == first_head
    assert len(commits) == 4


def test_shared_rules_are_content_addressed_and_adopted_individually(tmp_path, monkeypatch):
    seed = Path(__file__).resolve().parents[1] / "harnesses" / "repo"
    monkeypatch.setenv("SHARED_MIDDLEWARE_DIR", str(tmp_path / "common"))
    def use(agent):
        root = tmp_path / agent
        monkeypatch.setattr(settings, "harness_work_dir", str(root / "repo"))
        monkeypatch.setattr(settings, "harness_bare_repo", str(root / "bare"))
        initialize(seed, agent)
        return json.loads(git(root / "bare", "show", "main:registry.json"))["versions"][0]["commit_hash"]
    base = use("outline")
    monkeypatch.setattr(assets, "_production", lambda: base)
    public = assets.share_released_rule("path_guard", assets.ShareRule(base_commit=base))
    shared = assets.shared_versions("path_guard", public["version"])["content"]
    with pytest.raises(assets.HTTPException):
        assets.shared_versions("stage_gate")
    with pytest.raises(assets.HTTPException):
        assets.share_released_rule("path_guard", assets.ShareRule(base_commit="different"))
    text_base = use("text")
    monkeypatch.setattr(assets, "_production", lambda: text_base)
    result = assets.create_candidate(text_base, {"middleware/path_guard.py": shared + "\n# candidate change\n"})
    assets.validate_candidate(result["commit"], text_base)
    assert git(tmp_path / "outline" / "bare", "show", base + ":middleware/path_guard.py") == shared.strip()
    use("outline")
    with pytest.raises(ValueError):
        assets.validate_candidate(result["commit"], text_base)


def test_run_readout_only_returns_actual_interventions(monkeypatch):
    from app.view import traces
    events = [{"type": "middleware_assembly"}, {"type": "middleware_intervention",
               "intervention": {"middleware_name": "FilesystemPathGuardMiddleware", "hook": "wrap_tool_call", "action": "block"}}]
    monkeypatch.setattr(traces.db, "query_one", lambda query, args: args == ("own-trace",))
    def query(sql, args):
        assert "type='middleware_intervention'" in sql and args == ("own-trace",)
        return [{"payload_json": json.dumps(item)} for item in events if item["type"] == "middleware_intervention"]
    monkeypatch.setattr(traces.db, "query_all", query)
    assert [x["rule"] for x in traces.middleware_actions("own-trace")["items"]] == ["FilesystemPathGuardMiddleware"]
    with pytest.raises(assets.HTTPException):
        traces.middleware_actions("foreign-trace")
