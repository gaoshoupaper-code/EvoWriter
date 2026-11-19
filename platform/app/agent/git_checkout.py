"""harness bare repo checkout（自 executor/app/platform/agent/git_sync.py 迁移）。

Platform 侧只需要「bare repo → 指定 commit 的干净工作目录」这一个原语
（probe / 打包 / 算指纹共用），executor 的 pull_production 等生产同步逻辑
不迁移——executor 未来改从 Platform 下载 artifact（DEC-012），不再 git pull。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from app.core.settings import get_settings, resolve_path
from contracts.assessment_snapshots import checkout_snapshot, snapshot_path

logger = logging.getLogger("platform.git_checkout")


def _git(args: list[str], cwd: Path | None = None) -> str:
    """执行 git 命令，返回 stdout。失败 raise RuntimeError。"""
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败 (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def bare_repo_path() -> Path:
    """bare repo 本地路径（相对路径基于仓库根解析）。"""
    return resolve_path(get_settings().bare_repo)


def _checkout_root() -> Path:
    """临时 checkout 根目录（惰性创建）。"""
    path = resolve_path(get_settings().checkout_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def checkout_commit(commit: str) -> Path:
    """从已归档正文快照或既有 bare 库检出源码，调用方负责清理临时目录。"""
    if not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        raise RuntimeError("无效的提交标识")
    directory = os.environ.get("ASSESSMENT_SNAPSHOT_DIR")
    bundle = snapshot_path(Path(directory), commit) if directory and len(commit) == 40 else None
    tmp = Path(tempfile.mkdtemp(prefix=f"harness_{commit}_", dir=str(_checkout_root())))
    try:
        if bundle and bundle.is_file():
            checkout_snapshot(tmp, bundle, commit, bundle=True)
        else:
            _git(["clone", "--no-hardlinks", str(bare_repo_path()), str(tmp)])
            _git(["checkout", "--detach", commit], tmp)
    except Exception:
        cleanup_checkout(tmp)
        raise
    logger.info("harness checkout: commit=%s → %s", commit, tmp)
    return tmp


def cleanup_checkout(path: Path) -> None:
    """清理 checkout 临时目录（只清 checkout_dir 下的，防误删）。"""
    root = resolve_path(get_settings().checkout_dir)
    try:
        path.relative_to(root)
    except ValueError:
        logger.warning("拒绝清理非 checkout_dir 下的目录: %s", path)
        return
    if path.exists():
        def retry_readonly(operation, name, exc_info):
            # Git pack files on Windows can survive rmtree's first unlink attempt.
            if os.name == "nt" and isinstance(exc_info[1], PermissionError):
                os.chmod(name, stat.S_IWRITE | stat.S_IREAD)
                operation(name)
            else:
                logger.warning("无法清理 checkout 文件: %s", name, exc_info=exc_info)

        shutil.rmtree(path, onerror=retry_readonly)


def read_bare_registry() -> str | None:
    """读 bare repo main 分支的 registry.json 原文。

    Returns:
        registry 原文；无 main / 无 registry.json / bare 不存在 → None
        （视为「不可读」，FR-001 跳过导入；可读但内容不合法由账本导入报错）。
    """
    bare = bare_repo_path()
    if not bare.exists():
        return None
    try:
        return _git(["show", "main:registry.json"], cwd=bare)
    except RuntimeError:
        return None


__all__ = [
    "bare_repo_path",
    "checkout_commit",
    "cleanup_checkout",
    "read_bare_registry",
]
