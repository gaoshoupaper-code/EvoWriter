"""Create immutable, private candidate commits from the selected Harness release."""
from __future__ import annotations

import re
import json
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.git_ops import _git, _git_with_author, show_file, version_source
from app.core.settings import settings
from app.experiments import snapshots
from app.versioning.platform_ledger import fetch_ledger
from contracts.architecture_manifest import BASE_CHAIN_MODULES
from contracts.assessment_snapshots import checkout_snapshot
from contracts.target_tasks import configured_task_type

router = APIRouter(prefix="/harness/candidates", tags=["harness"])
_COMMIT = re.compile(r"[0-9a-f]{40}")
_ASSET = re.compile(r"(?:prompts/[\w.-]+\.md|skills/[\w.-]+/(?:[\w.-]+/)*[\w.-]+|(?:tools|middleware|subagents)/[\w.-]+\.py|architecture\.json|__init__\.py)")


def is_candidate_asset(path: str) -> bool:
    if path == "text_completion.py":
        return configured_task_type() == "chapter_text"
    return bool(_ASSET.fullmatch(path)) and not any(
        part.startswith(".") for part in path.split("/")
    )


def _production() -> str:
    ledger = fetch_ledger()
    return next((row["commit"] for row in ledger["items"] if row["version"] == ledger["production_version"]), "")


def _paths(commit: str) -> list[str]:
    with version_source(commit) as directory:
        entries = _git(["-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", commit], directory)
    return [name for name in entries.splitlines() if is_candidate_asset(name)]


def _checkout_candidate(base: str, root: Path) -> None:
    if configured_task_type() == "chapter_text":
        snapshots.checkout_base(base, root)
        return
    _git(["clone", "--no-checkout", "--no-local", str(settings.harness_bare_repo_path), str(root)], root.parent)
    _git(["fetch", "origin", base], root)
    _git(["checkout", "--detach", base], root)


def create_candidate(base: str, changes: dict[str, str]) -> dict:
    if not _COMMIT.fullmatch(base) or base != _production():
        raise ValueError("线上版本已变化，请从当前版本重新创建候选。")
    if not changes or not all(is_candidate_asset(path) for path in changes):
        raise ValueError("改动必须位于本套的资产目录。")
    with tempfile.TemporaryDirectory(prefix="writer-candidate-") as tmp:
        root = Path(tmp) / "repo"
        _checkout_candidate(base, root)
        for name, content in changes.items():
            target = root / name
            if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
                raise ValueError("候选包中的资产路径无效。")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        _git(["add", "--", *changes], root)
        if not _git(["diff", "--cached", "--name-only"], root):
            raise ValueError("候选内容与当前版本相同。")
        _git_with_author(["commit", "-m", "Edit Harness assets"], root)
        commit = _git(["rev-parse", "HEAD"], root)
        diff = _git(["diff", "--no-ext-diff", base, commit], root)
        if configured_task_type() == "chapter_text":
            snapshots.save(root, commit)
        else:
            _git(["push", "origin", f"HEAD:refs/heads/asset-candidates/{commit}"], root)
    return {"base_commit": base, "commit": commit, "diff": diff}


def validate_candidate(commit: str, baseline_commit: str) -> None:
    if not _COMMIT.fullmatch(commit) or not _COMMIT.fullmatch(baseline_commit):
        raise ValueError("候选或基线版本号无效。")
    if configured_task_type() == "chapter_text" and snapshots.snapshot_file(commit).is_file():
        with version_source(commit) as directory:
            parents = _git(["show", "-s", "--format=%P", commit], directory)
        if parents != baseline_commit:
            raise ValueError("候选版与本次基线不匹配。")
        return
    bare = settings.harness_bare_repo_path
    try:
        found = _git(["rev-parse", "--verify", f"refs/heads/asset-candidates/{commit}"], bare)
        parent = _git(["rev-parse", f"{commit}^"], bare)
    except RuntimeError as exc:
        raise ValueError("本套 Harness 中没有该候选版。") from exc
    if found != commit or parent != baseline_commit:
        raise ValueError("候选版与本次基线不匹配。")


def session_checkout(session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        raise ValueError("无效会话编号。")
    root = settings.db_path.parent / "evolve-candidates" / session_id
    if not root.is_dir():
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
            checkout = Path(tmp) / "repo"
            _checkout_candidate(_production(), checkout)
            checkout.rename(root)
    return root


def freeze_session(session_id: str) -> dict:
    root = session_checkout(session_id)
    existing = root.parent / f"{session_id}.json"
    if existing.exists():
        return json.loads(existing.read_text(encoding="utf-8"))
    base = _git(["rev-parse", "HEAD"], root)
    if base != _production():
        raise ValueError("本套线上基线已变化，请基于最新版重新评测。")
    _git(["add", "--all"], root)
    names = _git(["-c", "core.quotepath=false", "diff", "--cached", "--name-only"], root).splitlines()
    if not names or not all(is_candidate_asset(name) for name in names):
        raise ValueError("候选改动必须全部属于本套资产。")
    # commit-tree leaves the private checkout on its original baseline, including on retry.
    tree = _git(["write-tree"], root)
    commit = _git_with_author(["commit-tree", tree, "-p", base, "-m", "Freeze evolution candidate"], root)
    if configured_task_type() == "chapter_text":
        with tempfile.TemporaryDirectory(prefix="writer-freeze-") as directory:
            private = Path(directory)
            checkout_snapshot(private, root, commit, bundle=False)
            snapshots.save(private, commit)
    else:
        _git(["push", "origin", f"{commit}:refs/heads/asset-candidates/{commit}"], root)
    result = {"status": "candidate", "source_commit": commit, "base_commit": base}
    with tempfile.NamedTemporaryFile(mode="w", dir=root.parent, delete=False, encoding="utf-8") as tmp:
        json.dump(result, tmp)
    os.replace(tmp.name, existing)
    return result


class EditAssets(BaseModel):
    base_commit: str
    changes: dict[str, str]


def _shared_dir(module: str) -> Path:
    if module not in BASE_CHAIN_MODULES:
        raise HTTPException(404, "该文件不属于公共中间件。")
    return Path(os.getenv("SHARED_MIDDLEWARE_DIR", "/app/evolution/shared_middleware")) / module


@router.get("/shared/{module}")
def shared_versions(module: str, version: str | None = None):
    directory = _shared_dir(module)
    if version is None:
        return {"versions": sorted(file.stem for file in directory.glob("*.py"))}
    if not re.fullmatch(r"[0-9a-f]{64}", version):
        raise HTTPException(404, "无此公共版本。")
    file = directory / f"{version}.py"
    if not file.is_file():
        raise HTTPException(404, "无此公共版本。")
    return {"content": file.read_text(encoding="utf-8"), "version": version}


class ShareRule(BaseModel):
    base_commit: str


@router.post("/shared/{module}")
def share_released_rule(module: str, body: ShareRule):
    directory = _shared_dir(module)
    if body.base_commit != _production():
        raise HTTPException(409, "只能共享本套当前线上使用的公共规则。")
    with version_source(body.base_commit) as repository:
        content = subprocess.check_output(["git", "show", f"{body.base_commit}:middleware/{module}.py"],
                                          cwd=repository)
    digest = hashlib.sha256(content).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as tmp:
        tmp.write(content)
    try:
        os.link(tmp.name, directory / f"{digest}.py")
    except FileExistsError:
        pass
    finally:
        Path(tmp.name).unlink()
    return {"version": digest}


@router.get("/source")
def source(path: str | None = None):
    commit = _production()
    if not _COMMIT.fullmatch(commit):
        raise HTTPException(409, "本套尚无线上版本。")
    with version_source(commit):
        paths = _paths(commit)
        if path is None:
            return {"base_commit": commit, "paths": paths}
        if path not in paths:
            raise HTTPException(404, "本套没有该资产。")
        return {"base_commit": commit, "path": path, "content": show_file(commit, path)}


@router.post("", status_code=201)
def save_candidate(body: EditAssets):
    try:
        return create_candidate(body.base_commit, body.changes)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
