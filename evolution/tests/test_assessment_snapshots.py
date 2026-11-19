from pathlib import Path
import errno
import os
import subprocess

import pytest


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def test_archives_baseline_and_each_round_without_changing_legacy_repo(tmp_path, monkeypatch):
    from app.experiments import snapshots

    bare = tmp_path / "legacy.git"
    original = tmp_path / "original"
    git(tmp_path, "init", "-b", "main", str(original))
    card = Path("style.md")
    (original / card).write_text("baseline")
    git(original, "add", ".")
    git(original, "-c", "user.name=Test", "-c", "user.email=test@local", "commit", "-m", "baseline")
    git(tmp_path, "clone", "--bare", str(original), str(bare))
    baseline = git(original, "rev-parse", "HEAD")
    monkeypatch.setattr(snapshots.settings, "harness_bare_repo", str(bare))
    archive = tmp_path / "private"
    monkeypatch.setenv("ASSESSMENT_SNAPSHOT_DIR", str(archive))
    snapshots.ensure_baseline(baseline)
    first = tmp_path / "round1"
    snapshots.checkout_base(baseline, first)
    (first / card).write_text("next")
    git(first, "-c", "user.name=Test", "-c", "user.email=test@local", "commit", "-am", "updated")
    candidate = git(first, "rev-parse", "HEAD")
    snapshots.save(first, candidate)
    assert git(bare, "show-ref").splitlines() == [baseline + " refs/heads/main"]
    monkeypatch.setattr(snapshots.settings, "harness_bare_repo", str(tmp_path / "offline"))
    snapshots.ensure_baseline(candidate)
    second = tmp_path / "round2"
    snapshots.checkout_base(candidate, second)
    assert (second / card).read_text() == "next"
    assert git(second, "rev-parse", "HEAD^") == baseline
    assert git(second, "remote") == ""
    before = (archive / (candidate + ".bundle")).stat().st_mtime_ns
    snapshots.save(first, candidate)
    assert (archive / (candidate + ".bundle")).stat().st_mtime_ns == before
    with pytest.raises(ValueError):
        snapshots.save(first, baseline)
    with pytest.raises(ValueError):
        snapshots.checkout_base("../other", tmp_path / "invalid")

    monkeypatch.setattr(snapshots.settings, "evolution_db", str(tmp_path / "data" / "app.db"))
    real_link = os.link

    def cross_volume_link(source, destination):
        if source.parent == archive and "copy" in destination.parts:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_link(source, destination)

    monkeypatch.setattr(os, "link", cross_volume_link)
    for run_id in ("link", "copy"):
        snapshots.retain(run_id, candidate)
        retained = tmp_path / "data" / "assessments" / run_id / "versions" / (candidate + ".bundle")
        assert retained.read_bytes() == snapshots.snapshot_file(candidate).read_bytes()
        stamp = retained.stat().st_mtime_ns
        snapshots.retain(run_id, candidate)
        assert retained.stat().st_mtime_ns == stamp
    with pytest.raises(ValueError):
        snapshots.retain("../foreign", candidate)
