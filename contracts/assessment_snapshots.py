"""Read an isolated assessment bundle by its exact commit identity."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def snapshot_path(directory: Path, commit: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("正文快照需要完整的 40 位提交标识")
    return directory / f"{commit}.bundle"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=90)
    if result.returncode:
        raise RuntimeError(f"读取或保存正文快照失败: {result.stderr.strip()}")
    return result.stdout.strip()


def checkout_snapshot(root: Path, source: Path, commit: str, *, bundle: bool) -> None:
    # A bundle advertises HEAD even when it was created in a detached checkout.
    # Fetching just that ref leaves the private worktree without a push remote.
    git(root, "init", "--quiet")
    git(root, "-c", "fetch.fsckObjects=true", "fetch", "--no-tags", str(source.resolve()),
        "HEAD" if bundle else commit)
    if git(root, "rev-parse", "FETCH_HEAD^{commit}") != commit:
        raise ValueError("正文快照与请求的提交标识不符")
    git(root, "checkout", "--detach", commit)
