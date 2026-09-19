"""harness bare repo checkout（自 executor/app/platform/agent/git_sync.py 迁移）。

Platform 侧只需要「bare repo → 指定 commit 的干净工作目录」这一个原语
（probe / 打包 / 算指纹共用），executor 的 pull_production 等生产同步逻辑
不迁移——executor 未来改从 Platform 下载 artifact（DEC-012），不再 git pull。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.settings import get_settings, resolve_path

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
    """clone bare repo 并 checkout 指定 commit 到独立临时目录。

    每次一个独立目录，调用方用完必须 cleanup_checkout（probe/打包/指纹都是
    一次性消费，不留缓存目录）。

    Raises:
        RuntimeError: commit 在 bare repo 中不存在（附最近 commit 列表辅助定位，
        线上常见原因：evolution 端 commit 后未 push）。
    """
    bare = bare_repo_path()
    if not bare.exists():
        raise RuntimeError(f"bare repo 不存在: {bare}")
    tmp = Path(tempfile.mkdtemp(prefix=f"harness_{commit}_", dir=str(_checkout_root())))

    _git(["clone", bare, str(tmp)])
    try:
        _git(["checkout", commit], tmp)
    except RuntimeError as exc:
        # 列出 bare repo 最近 commit 附在错误信息里方便诊断（继承 executor 线上经验）
        try:
            log = _git(["log", "--oneline", "-10"], tmp)
            available = "\n  ".join(log.splitlines()) if log else "(空仓库)"
        except Exception:  # noqa: BLE001
            available = "(无法读取 commit 列表)"
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(
            f"checkout commit {commit} 失败（bare repo 无此 commit）。"
            f"通常是 evolution 端 commit 未 push 到 bare repo 导致。\n"
            f"  原始错误: {exc.args[0]}\n"
            f"  bare repo 最近 commit:\n  {available}"
        ) from exc
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
        shutil.rmtree(path, ignore_errors=True)


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
