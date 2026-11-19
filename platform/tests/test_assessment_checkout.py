import pytest
from tests_helpers import git_run


def test_platform_checks_exact_private_snapshot_and_keeps_legacy_short_hash(platform_env, harness_git, tmp_path, monkeypatch):
    from app.agent.git_checkout import checkout_commit, cleanup_checkout

    archive = tmp_path / "private"
    archive.mkdir()
    monkeypatch.setenv("ASSESSMENT_SNAPSHOT_DIR", str(archive))
    (harness_git.work / "story.md").write_text("private")
    git_run(["add", "."], harness_git.work)
    git_run(["commit", "-m", "private candidate"], harness_git.work)
    sha = git_run(["rev-parse", "HEAD"], harness_git.work)
    bundle = archive / (sha + ".bundle")
    git_run(["bundle", "create", str(bundle), "HEAD"], harness_git.work)
    checkout = checkout_commit(sha)
    assert (checkout / "story.md").read_text() == "private"
    cleanup_checkout(checkout)
    checkout = checkout_commit(harness_git.head[:9])
    assert git_run(["rev-parse", "HEAD"], checkout) == harness_git.head
    cleanup_checkout(checkout)
    bundle.rename(archive / (harness_git.head + ".bundle"))
    with pytest.raises((ValueError, RuntimeError)):
        checkout_commit(harness_git.head)
    assert not list(platform_env.checkouts.iterdir())
