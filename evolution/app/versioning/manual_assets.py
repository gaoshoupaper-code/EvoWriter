"""One durable hand-edited draft per creative Harness, with explicit production release."""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock

from fastapi import APIRouter, HTTPException

from app.core import db
from app.core.git_ops import _git, _git_with_author
from app.experiments import clients, snapshots
from app.versioning.asset_candidates import _checkout_candidate
from app.versioning.manual_asset_edit import DraftEdit, DraftRevision, apply_edit, draft_agents, restore_copy
from app.versioning.release_gate import ReleasePromoteError, promote_release
from contracts.creative_harness import AGENTS
from contracts.target_tasks import configured_task_type


router = APIRouter(prefix="/harness/manual-draft", tags=["harness"])
_lock = RLock()


def _scope() -> str:
    name = os.getenv("WRITING_AGENT_ID")
    if name not in AGENTS:
        raise HTTPException(404, "请在本套创作 Agent 的资产页维护手动草稿。")
    return name


def _production() -> dict:
    try:
        return clients.production()
    except (clients.ServiceUnavailable, clients.TaskNotFound, ValueError) as exc:
        raise HTTPException(503, "暂时无法读取本套生产版本。") from exc


def _new_draft(production: dict, revision: int) -> dict:
    return {"base_version": production["version"], "base_commit": production["commit"],
            "revision": revision, "changes": [], "agents": None}


def _read(scope: str, production: dict) -> tuple[dict, bytes]:
    row = db.query_one("SELECT payload, patch FROM manual_asset_drafts WHERE harness = ?", (scope,))
    return (json.loads(row["payload"]), row["patch"]) if row else (_new_draft(production, 0), b"")


def _write(scope: str, draft: dict, patch: bytes) -> None:
    db.execute(
        "INSERT INTO manual_asset_drafts (harness, payload, patch) VALUES (?, ?, ?) "
        "ON CONFLICT(harness) DO UPDATE SET payload = excluded.payload, patch = excluded.patch",
        (scope, json.dumps(draft, ensure_ascii=False), patch),
    )


def _identity(production: dict) -> tuple:
    return production["version"], production["commit"]


def _base(draft: dict) -> tuple:
    return draft["base_version"], draft["base_commit"]


def _reply(draft: dict, state: str, **extra) -> dict:
    return {
        "base_version": draft["base_version"], "base_commit": draft["base_commit"],
        "revision": draft["revision"], "state": state, "agents": draft["agents"],
        "changes": [{key: change[key] for key in ("role", "operation", "name", "before", "after")}
                    for change in draft["changes"]],
        **extra,
    }


def _view(scope: str, draft: dict, production: dict) -> dict:
    if draft.get("published_version") == production["version"] and _identity(production) == _base(draft):
        return _reply(draft, "activated", published_version=production["version"])

    if (draft.get("attempt") == production["commit"] and production["version"] > draft["base_version"]
            and draft.get("attempt_version", production["version"]) == production["version"]):
        version = production["version"]
        try:
            actual = clients.runtime()
        except (clients.ServiceUnavailable, clients.TaskNotFound, ValueError):
            actual = {}
        digest = production.get("artifact_digest")
        if (digest and _identity(production) == (actual.get("version"), actual.get("commit"))
                and actual.get("artifact_digest") == digest):
            done = _new_draft(production, draft["revision"])
            done.update(published_version=version, released_base=draft["base_version"])
            _write(scope, done, b"")
            return _reply(done, "activated", published_version=version)
        return _reply(draft, "unconfirmed", published_version=version,
                      message=f"v{version} 已写入发布账本，待执行端确认加载。")

    if draft["changes"] and _identity(production) != _base(draft):
        return _reply(draft, "stale", message=f"生产已更新至 v{production['version']}。请核对新版，旧草稿已保留。")

    visible = draft if draft["changes"] else _new_draft(production, draft["revision"])
    return _reply(visible, "editing")


def _require_revision(request: DraftRevision, view: dict) -> None:
    if request.revision != view["revision"] or request.base_version != view["base_version"]:
        raise HTTPException(409, "草稿或生产版本已变化，请刷新后再操作。")


@contextmanager
def _checkout(commit: str, patch: bytes):
    with TemporaryDirectory(prefix="writer-manual-assets-") as directory:
        root = Path(directory).resolve() / "harness"
        _checkout_candidate(commit, root)
        if patch:
            subprocess.run(["git", "apply", "--index", "--binary", "-"], cwd=root,
                           input=patch, capture_output=True, check=True, timeout=30)
        yield root


def _update_summary(root: Path, changes: list[dict], edit: DraftEdit, change: dict) -> None:
    for index, previous in enumerate(changes):
        if previous["role"] != edit.role or previous["operation"].startswith("skill_") != edit.operation.startswith("skill_"):
            continue
        if previous["path"] == edit.path or (
                edit.operation == "skill_import" and previous["after"] is None
                and previous["name"].casefold() == change["name"].casefold()):
            change["before"] = previous["before"]
            change["origin"] = previous["origin"]
            if change["before"] == change["after"]:
                restore_copy(root, change)
                changes.pop(index)
                return
            if edit.operation != "prompt" and change["after"] is not None:
                change["operation"] = "skill_import" if change["before"] is None else "skill_edit"
            changes[index] = change
            return
    changes.append(change)


def _save_candidate(scope: str, draft: dict, patch: bytes) -> str:
    with _checkout(draft["base_commit"], patch) as root:
        _git_with_author(["commit", "-m", f"Manual Harness assets ({scope})"], root)
        commit = _git(["rev-parse", "HEAD"], root)
        if configured_task_type() == "chapter_text":
            snapshots.save(root, commit)
        else:
            # These commits are independent of the public candidate-edit API.
            _git(["push", "origin", f"HEAD:refs/heads/manual-releases/{commit}"], root)
        return commit


@router.get("")
def get_draft() -> dict:
    scope = _scope()
    with _lock:
        production = _production()
        draft, _ = _read(scope, production)
        return _view(scope, draft, production)


@router.put("")
def edit_draft(edit: DraftEdit) -> dict:
    scope = _scope()
    with _lock:
        production = _production()
        draft, patch = _read(scope, production)
        view = _view(scope, draft, production)
        _require_revision(edit, view)
        if view["state"] not in ("editing", "activated"):
            raise HTTPException(409, view["message"])
        if not draft["changes"] or view["state"] == "activated":
            draft, patch = _new_draft(production, draft["revision"]), b""
        try:
            with _checkout(draft["base_commit"], patch) as root:
                change = apply_edit(root, edit)
                if change["before"] == change["after"]:
                    return view
                _update_summary(root, draft["changes"], edit, change)
                _git(["add", "-A", "-f"], root)
                changed = subprocess.check_output(
                    ["git", "diff", "--binary", "--cached", "HEAD"], cwd=root, timeout=30)
                if not changed or not draft["changes"]:
                    saved = _new_draft(production, draft["revision"] + 1)
                    changed = b""
                else:
                    saved = {**draft, "revision": draft["revision"] + 1,
                             "agents": draft_agents(root)}
                    saved.pop("attempt", None)
                    saved.pop("attempt_version", None)
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise HTTPException(409, f"未保存草稿：{exc}") from exc
        if _identity(_production()) != _base(draft):
            raise HTTPException(409, "生产版本已变化，原草稿已保留。")
        _write(scope, saved, changed)
        return _reply(saved, "editing")


@router.delete("")
def discard_draft(request: DraftRevision) -> dict:
    scope = _scope()
    with _lock:
        production = _production()
        draft, _ = _read(scope, production)
        view = _view(scope, draft, production)
        _require_revision(request, view)
        if view["state"] == "unconfirmed":
            raise HTTPException(409, "已发布的版本正在等待执行端确认。")
        cleared = _new_draft(production, draft["revision"] + 1)
        _write(scope, cleared, b"")
        return _reply(cleared, "editing")


@router.post("/release")
def release_draft(request: DraftRevision) -> dict:
    scope = _scope()
    with _lock:
        production = _production()
        draft, patch = _read(scope, production)
        view = _view(scope, draft, production)
        # A retry of the just-completed confirmation observes the same release.
        released_base = draft.get("released_base", draft["base_version"])
        if (view["state"] == "activated" and request.revision == view["revision"]
                and request.base_version == released_base):
            return view
        _require_revision(request, view)
        if view["state"] == "unconfirmed":
            return view
        if view["state"] == "stale":
            raise HTTPException(409, view["message"])
        if not draft["changes"]:
            raise HTTPException(409, "草稿没有待上线的改动。")

        try:
            commit = draft.get("attempt") or _save_candidate(scope, draft, patch)
            if not draft.get("attempt"):
                draft["attempt"] = commit
                _write(scope, draft, patch)
            result = promote_release(
                commit, version_note=f"手动维护 Harness 资产（{len(draft['changes'])} 项）",
                expected_production_commit=draft["base_commit"],
                expected_production_version=draft["base_version"],
            )
        except (ReleasePromoteError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            # A lost HTTP response may have followed a successful ledger update.
            observed = _view(scope, draft, _production())
            if observed["state"] in ("activated", "unconfirmed"):
                return observed
            if observed["state"] == "stale":
                raise HTTPException(409, observed["message"]) from exc
            raise HTTPException(409, f"未能上线，草稿已保留：{exc}") from exc
        if result.commit != commit:
            raise HTTPException(502, "发布服务回报了不同的版本，草稿已保留。")
        draft["attempt_version"] = result.version
        _write(scope, draft, patch)
        return _view(scope, draft, _production())
