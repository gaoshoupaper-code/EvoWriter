import json
import os
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import db
from app.core.settings import settings
from app.experiments import snapshots
from app.versioning.release_gate import ReleasePromoteError
from contracts.platform import PromoteResult


API = "/api/harness/manual-draft"
SKILL = "---\nname: rhythm\ndescription: Review scene rhythm.\n---\n\nOriginal skill.\n"


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, encoding="utf-8").strip()


def read_at(directory, commit, path):
    return git(directory, "show", f"{commit}:{path}")


@pytest.fixture
def scope(tmp_path, monkeypatch, request):
    from app.versioning import manual_assets as manual

    agent = getattr(request, "param", "text")
    assistant = {"volume": "volume_qc", "text": "review"}.get(agent)
    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    monkeypatch.setenv("ASSESSMENT_SNAPSHOT_DIR", str(tmp_path / "bundles"))
    monkeypatch.setattr(settings, "evolution_db", str(tmp_path / "evolution.db"))
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-b", "main")
    files = {
        "prompts/shared.md": "# Original prompt\n",
        "skills/rhythm/SKILL.md": SKILL,
        "skills/rhythm/examples/note.md": "Keep companion files.\n",
        "architecture.json": json.dumps({"schema_version": "writer.architecture/1", "agents": [
            {"name": agent, "role": "main", "prompt": "prompts/shared.md",
             "skills": ["rhythm"], "delegates": [assistant] if assistant else []},
            *([{"name": assistant, "role": "sub", "prompt": "prompts/shared.md",
                "skills": ["rhythm"], "delegated_by": agent}] if assistant else []),
        ]}, indent=2) + "\n",
    }
    for path, content in files.items():
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(work, "add", ".")
    git(work, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-m", "base")
    baseline = git(work, "rev-parse", "HEAD")
    bare = tmp_path / "harness.git"
    git(tmp_path, "clone", "--bare", str(work), str(bare))
    monkeypatch.setattr(settings, "harness_bare_repo", str(bare))
    production = {"version": 1, "commit": baseline}
    runtime = {"version": 1, "commit": baseline, "artifact_digest": "old"}
    attempted = []

    def promote(commit, version_note, *, expected_production_commit, expected_production_version):
        assert (expected_production_commit, expected_production_version) == (
            production["commit"], production["version"])
        attempted.append(commit)
        production.update(version=production["version"] + 1, commit=commit, artifact_digest="new")
        return PromoteResult(version=production["version"], commit=commit, artifact_digest="new",
                             reload_notified=False)

    monkeypatch.setattr(manual.clients, "production", lambda: dict(production))
    monkeypatch.setattr(manual.clients, "runtime", lambda: dict(runtime))
    monkeypatch.setattr(manual, "promote_release", promote)
    app = FastAPI()
    app.include_router(manual.router, prefix="/api")
    yield TestClient(app), production, runtime, attempted, bare, baseline
    db.get_conn().close()


def edit(client, view, role, operation, **kwargs):
    return client.put(API, json={"base_version": view["base_version"], "revision": view["revision"],
                                 "role": role, "operation": operation, **kwargs})


def release(client, view):
    return client.post(API + "/release", json={key: view[key] for key in ("base_version", "revision")})


def test_draft_reopens_and_publishes_both_assets_as_one_private_version(scope, tmp_path):
    client, production, runtime, attempted, bare, baseline = scope
    empty = client.get(API).json()
    prompt = edit(client, empty, "review", "prompt", path="prompts/shared.md", content="# Revised prompt\n").json()
    draft = edit(client, prompt, "text", "skill_edit", path="skills/rhythm",
                 content=SKILL.replace("Original", "Revised")).json()
    reopened = client.get(API).json()
    assert reopened == draft
    assert production == {"version": 1, "commit": baseline}
    assert [(row["role"], row["operation"]) for row in draft["changes"]] == [
        ("review", "prompt"), ("text", "skill_edit")]
    pending = release(client, draft).json()
    assert pending["state"] == "unconfirmed"
    assert pending["published_version"] == 2
    assert release(client, draft).json()["state"] == "unconfirmed"
    runtime.update(version=production["version"], commit="wrong", artifact_digest="new")
    assert client.get(API).json()["state"] == "unconfirmed"
    runtime.update(commit=production["commit"], artifact_digest="wrong")
    assert client.get(API).json()["state"] == "unconfirmed"
    runtime.update(version=1, artifact_digest="new")
    assert client.get(API).json()["state"] == "unconfirmed"
    runtime["version"] = production["version"]
    assert client.get(API).json()["state"] == "activated"
    assert release(client, draft).json()["published_version"] == 2
    assert len(attempted) == 1
    assert git(bare, "show-ref") == f"{baseline} refs/heads/main"

    checkout = tmp_path / "published"
    snapshots.checkout_base(production["commit"], checkout)
    actors = {row["name"]: row for row in json.loads(
        (checkout / "architecture.json").read_text(encoding="utf-8"))["agents"]}
    assert (checkout / actors["review"]["prompt"]).read_text(encoding="utf-8") == "# Revised prompt\n"
    assert (checkout / actors["text"]["prompt"]).read_text(encoding="utf-8") == "# Original prompt\n"
    for role, word in (("text", "Revised"), ("review", "Original")):
        directory = checkout / "skills" / actors[role]["skills"][0]
        assert word in (directory / "SKILL.md").read_text(encoding="utf-8")
        assert (directory / "examples/note.md").read_text(encoding="utf-8") == "Keep companion files.\n"


def test_delete_shared_skill_then_last_reference(scope):
    client, production, runtime, attempted, bare, baseline = scope
    empty = client.get(API).json()
    first = edit(client, empty, "text", "skill_delete", path="skills/rhythm").json()
    assert not next(row for row in first["agents"] if row["name"] == "text")["skills"]
    assert next(row for row in first["agents"] if row["name"] == "review")["skills"]
    second = edit(client, first, "review", "skill_delete", path="skills/rhythm").json()
    assert release(client, second).json()["state"] == "unconfirmed"
    checkout = bare.parent / "deleted"
    snapshots.checkout_base(production["commit"], checkout)
    assert not (checkout / "skills/rhythm").exists()
    assert "Original skill." in read_at(bare, baseline, "skills/rhythm/SKILL.md")


def test_import_validates_role_filename_and_skill_name(scope):
    client, *_ = scope
    view = client.get(API).json()
    for role, filename, content in [
        ("text", "other.md", SKILL),
        ("text", "SKILL.md", "# missing metadata"),
        ("text", "SKILL.md", SKILL),
        ("not-a-role", "SKILL.md", SKILL),
    ]:
        response = edit(client, view, role, "skill_import", filename=filename, content=content)
        assert response.status_code in (409, 422), response.text
    assert client.get(API).json() == view
    content = SKILL.replace("rhythm", "pacing")
    result = edit(client, view, "review", "skill_import", filename="SKILL.md", content=content)
    assert result.status_code == 200, result.text
    actors = {row["name"]: row for row in result.json()["agents"]}
    assert [item["name"] for item in actors["review"]["skills"]] == ["rhythm", "pacing"]
    assert [item["name"] for item in actors["text"]["skills"]] == ["rhythm"]
    assert edit(client, view, "text", "prompt", path="prompts/shared.md", content="stale input").status_code == 409


def test_import_same_name_into_different_role_does_not_overwrite(scope):
    client, *_ = scope
    initial = client.get(API).json()
    removed = edit(client, initial, "review", "skill_delete", path="skills/rhythm").json()
    imported = edit(client, removed, "review", "skill_import", filename="SKILL.md",
                    content=SKILL.replace("Original", "Review only"))
    assert imported.status_code == 200, imported.text
    actors = {row["name"]: row for row in imported.json()["agents"]}
    assert "Original" in actors["text"]["skills"][0]["content"]
    assert "Review only" in actors["review"]["skills"][0]["content"]
    assert actors["text"]["skills"][0]["path"] != actors["review"]["skills"][0]["path"]


def test_undoing_shared_prompt_keeps_original_reference_with_other_dirty_asset(scope, tmp_path):
    client, production, _, _, _, _ = scope
    initial = client.get(API).json()
    changed = edit(client, initial, "text", "prompt", path="prompts/shared.md", content="New").json()
    edited_path = changed["agents"][0]["prompt_files"][0]["path"]
    assert edited_path != "prompts/shared.md"
    added = edit(client, changed, "text", "skill_import", filename="SKILL.md",
                 content=SKILL.replace("rhythm", "pacing")).json()
    undid = edit(client, added, "text", "prompt", path=edited_path, content="# Original prompt\n").json()
    assert undid["agents"][0]["prompt_files"][0]["path"] == "prompts/shared.md"
    assert [row["operation"] for row in undid["changes"]] == ["skill_import"]
    assert release(client, undid).json()["state"] == "unconfirmed"
    checkout = tmp_path / "undo-version"
    snapshots.checkout_base(production["commit"], checkout)
    assert not (checkout / edited_path).exists()


@pytest.mark.parametrize("scope", ["outline", "volume", "chapter", "text"], indirect=True)
def test_each_harness_can_edit_and_promote_its_own_draft(scope, monkeypatch):
    client, production, _, _, bare, baseline = scope
    agent = os.environ["WRITING_AGENT_ID"]
    empty = client.get(API).json()
    response = edit(client, empty, agent, "prompt", path="prompts/shared.md", content=f"{agent} revision")
    assert response.status_code == 200, response.text
    draft = response.json()
    for other in {"outline", "volume", "chapter", "text"} - {agent}:
        monkeypatch.setenv("WRITING_AGENT_ID", other)
        assert client.get(API).json()["changes"] == []
    monkeypatch.setenv("WRITING_AGENT_ID", agent)
    assert release(client, draft).json()["state"] == "unconfirmed"
    if agent == "text":
        assert snapshots.snapshot_file(production["commit"]).is_file()
        assert git(bare, "show-ref") == f"{baseline} refs/heads/main"
    else:
        assert git(bare, "rev-parse", f"refs/heads/manual-releases/{production['commit']}") == production["commit"]
        assert git(bare, "rev-parse", "refs/heads/main") == baseline


def test_stale_same_commit_with_new_version_does_not_overwrite(scope):
    client, production, runtime, attempted, *_ = scope
    empty = client.get(API).json()
    draft = edit(client, empty, "review", "prompt", path="prompts/shared.md", content="new prompt").json()
    production["version"] = 2
    assert release(client, draft).status_code == 409
    view = client.get(API).json()
    assert view["state"] == "stale" and view["changes"] == draft["changes"]
    assert attempted == []
    response = client.request("DELETE", API, json={key: view[key] for key in ("base_version", "revision")})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] > draft["revision"]


def test_probe_rejection_preserves_draft_and_allows_another_edit(scope, monkeypatch):
    from app.versioning import manual_assets as manual

    client, production, runtime, attempted, bare, baseline = scope
    empty = client.get(API).json()
    draft = edit(client, empty, "review", "prompt", path="prompts/shared.md", content="broken").json()

    def rejected(*args, **kwargs):
        raise ReleasePromoteError("probe rejected")

    monkeypatch.setattr(manual, "promote_release", rejected)
    response = release(client, draft)
    assert response.status_code == 409 and "probe rejected" in response.text
    reopened = client.get(API).json()
    assert reopened["changes"] == draft["changes"]
    assert production["commit"] == baseline
    target = next(row for row in reopened["agents"] if row["name"] == "review")["prompt_files"][0]["path"]
    assert edit(client, reopened, "review", "prompt", path=target, content="fixed").status_code == 200


def test_lost_release_response_reconciles_without_a_second_promotion(scope, monkeypatch):
    from app.versioning import manual_assets as manual

    client, production, runtime, attempted, *_ = scope
    original_promote = manual.promote_release

    def lose_response(*args, **kwargs):
        original_promote(*args, **kwargs)
        raise ReleasePromoteError("connection closed before response")

    monkeypatch.setattr(manual, "promote_release", lose_response)
    draft = edit(client, client.get(API).json(), "text", "skill_import", filename="SKILL.md",
                 content=SKILL.replace("rhythm", "pacing")).json()
    assert release(client, draft).json()["state"] == "unconfirmed"
    runtime.update(version=production["version"], commit=production["commit"], artifact_digest="new")
    assert release(client, draft).json()["state"] == "activated"
    assert len(attempted) == 1


def test_shared_stage_reference_repoints_only_selected_main(tmp_path):
    from app.versioning.manual_asset_edit import DraftEdit, apply_edit

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "main.md").write_text("Main", encoding="utf-8")
    (prompts / "shared.md").write_text("Shared", encoding="utf-8")
    initial = "STAGE_PROMPT_FILES: dict = {'writer': 'shared.md'}\nUNRELATED = 'shared.md'\n"
    (tmp_path / "__init__.py").write_text(initial, encoding="utf-8")
    manifest = {"schema_version": "writer.architecture/1", "agents": [
        {"name": "text", "role": "main", "prompt": "prompts/main.md"},
        {"name": "review", "role": "sub", "prompt": "prompts/shared.md"},
    ]}
    (tmp_path / "architecture.json").write_text(json.dumps(manifest), encoding="utf-8")
    change = apply_edit(tmp_path.resolve(), DraftEdit(base_version=1, revision=0, role="text",
                        operation="prompt", content="Main stage", path="prompts/shared.md"))
    assert (tmp_path / change["path"]).read_text(encoding="utf-8") == "Main stage"
    assert (prompts / "shared.md").read_text(encoding="utf-8") == "Shared"
    updated = (tmp_path / "__init__.py").read_text(encoding="utf-8")
    assert updated == initial.replace("'writer': 'shared.md'", "'writer': 'text_shared.md'")
    assert json.loads((tmp_path / "architecture.json").read_text(encoding="utf-8")) == manifest
