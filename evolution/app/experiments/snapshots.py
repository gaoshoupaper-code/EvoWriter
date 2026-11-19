"""Persist complete text-Harness revisions in a private server volume."""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from contracts.assessment_snapshots import checkout_snapshot, git, snapshot_path
from app.core.settings import settings
from app.experiments.assessment import artifact_directory


def snapshot_file(commit: str) -> Path:
    configured = os.environ.get("ASSESSMENT_SNAPSHOT_DIR")
    directory = Path(configured) if configured else settings.db_path.parent / "assessment_snapshots"
    return snapshot_path(directory, commit)


def _check_bundle(path: Path, commit: str) -> None:
    if git(path.parent, "bundle", "list-heads", str(path.resolve()), "HEAD") != f"{commit} HEAD":
        raise ValueError("已保存的正文快照与提交标识不符")


def _publish(source: Path, dest: Path) -> None:
    # Publishing a link is atomic and never overwrites an earlier archive.
    try:
        os.link(source, dest)
    except FileExistsError:
        pass


def checkout_base(commit: str, root: Path) -> None:
    path = snapshot_file(commit)
    root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root.parent) as staging:
        work = Path(staging) / "checkout"
        work.mkdir()
        is_bundle = path.is_file()
        checkout_snapshot(work, path if is_bundle else settings.harness_bare_repo_path, commit, bundle=is_bundle)
        work.rename(root)


def save(root: Path, commit: str) -> None:
    path = snapshot_file(commit)
    if git(root, "rev-parse", "HEAD") != commit:
        raise ValueError("候选工作副本与待保存的提交标识不符")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path.parent) as staging:
            pending = Path(staging) / "revision.bundle"
            git(root, "bundle", "create", str(pending.resolve()), "HEAD")
            _publish(pending, path)
    _check_bundle(path, commit)


def ensure_baseline(commit: str) -> None:
    path = snapshot_file(commit)
    if path.is_file():
        _check_bundle(path, commit)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as staging:
        root = Path(staging) / "baseline"
        checkout_base(commit, root)
        save(root, commit)


def retain(run_id: str, commit: str) -> None:
    source = snapshot_file(commit)
    directory = artifact_directory(run_id).parent / "versions"
    dest = directory / source.name
    if not dest.is_file():
        _check_bundle(source, commit)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            _publish(source, dest)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            with TemporaryDirectory(dir=directory) as staging:
                local = Path(staging) / source.name
                shutil.copyfile(source, local)
                _publish(local, dest)
    _check_bundle(dest, commit)


def archived_source(run_id: str, commit: str, path: str | None = None) -> dict:
    from app.versioning.asset_candidates import is_candidate_asset

    bundle = snapshot_path(artifact_directory(run_id).parent / "versions", commit)
    if not bundle.is_file() or (path is not None and not is_candidate_asset(path)):
        raise FileNotFoundError("本档案没有该版本或资产")
    with TemporaryDirectory(prefix="assessment-source-") as directory:
        root = Path(directory)
        # Inspect objects only. No files or symlink targets from the candidate are executed or opened.
        git(root, "init", "--bare", "--quiet")
        git(root, "-c", "fetch.fsckObjects=true", "fetch", "--no-tags", str(bundle.resolve()), "HEAD")
        if git(root, "rev-parse", "FETCH_HEAD^{commit}") != commit:
            raise ValueError("档案快照和所选轮次版本不符")
        listing = git(root, "ls-tree", "-rz", commit)
        objects = {}
        for entry in listing.split("\0"):
            if entry:
                metadata, name = entry.split("\t", 1)
                mode, kind, blob = metadata.split()
                if kind == "blob" and mode in {"100644", "100755"} and is_candidate_asset(name):
                    objects[name] = blob
        if path is None:
            return {"commit": commit, "paths": sorted(objects)}
        if path not in objects:
            raise FileNotFoundError("该版本没有所选资产")
        raw = subprocess.check_output(["git", "cat-file", "blob", objects[path]], cwd=root, timeout=30)
        return {"commit": commit, "path": path, "content": raw.decode("utf-8")}
