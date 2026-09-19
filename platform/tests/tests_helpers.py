"""platform 测试的 git/环境辅助函数（conftest 与各测试文件共用）。

放在独立模块而非 conftest 内：`platform` 与标准库模块重名，
`from platform.tests.conftest import ...` 走不通；pytest prepend 模式会把
platform/tests/ 放进 sys.path，测试文件直接 `from tests_helpers import ...`。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

# 最小 harness 包源码（probe 门禁的最小可装配体）
INIT_PY = '''\
"""最小 harness 包（测试 fixture）。"""
from contracts.runtime_context import RuntimeContext


def assemble(ctx: RuntimeContext):
    return {"assembled": True, "model": type(ctx.model).__name__}
'''

ARTIFACT_SNAPSHOT_PY = '''\
"""artifact snapshot middleware 空实现（测试 fixture）。"""


def build_snapshot(*_args, **_kwargs):
    return None
'''

REGISTRY_CREATED_AT = "2026-01-01T00:00:00+00:00"


def git_run(args: list[str], cwd: Path) -> str:
    """跑 git 命令，失败直接 AssertionError（fixture 自身出了问题）。"""
    proc = subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {args} 失败: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _init_work_repo(work: Path) -> None:
    """初始化工作库，保证分支名是 main（Windows 老 git 兼容处理）。"""
    try:
        git_run(["init", "-q", "--initial-branch=main"], work)
    except AssertionError:
        git_run(["init", "-q"], work)
    if git_run(["symbolic-ref", "--short", "HEAD"], work) != "main":
        git_run(["branch", "-m", "main"], work)
    git_run(["config", "user.email", "test@local"], work)
    git_run(["config", "user.name", "platform-test"], work)


def _write_initial_package(work: Path) -> None:
    (work / "__init__.py").write_text(INIT_PY, encoding="utf-8")
    (work / "middleware").mkdir()
    (work / "middleware" / "artifact_snapshot.py").write_text(
        ARTIFACT_SNAPSHOT_PY, encoding="utf-8"
    )
    (work / "prompts").mkdir()
    (work / "prompts" / "x.md").write_text("# prompt x\n", encoding="utf-8")
    (work / "skills" / "s").mkdir(parents=True)
    (work / "skills" / "s" / "SKILL.md").write_text("# skill s\n", encoding="utf-8")
    # 与真实 harness 仓库一致：忽略字节码缓存（probe 装配会生成 __pycache__）
    (work / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")


@dataclass
class HarnessGit:
    """tmp 内的 harness 工作库 + 裸库句柄。"""

    work: Path
    bare: Path
    head: str  # 初始包内容的 commit（registry production 指向它）

    def commit(self, message: str, mutate=None) -> str:
        """可选改动工作区 → commit + push，返回新 commit 完整 hash。"""
        if mutate is not None:
            mutate(self.work)
        git_run(["add", "-A"], self.work)
        git_run(["commit", "-q", "-m", message], self.work)
        git_run(["push", "-q", "origin", "main"], self.work)
        return git_run(["rev-parse", "HEAD"], self.work)


def make_harness_repo(tmp: Path, registry_mutate=None) -> HarnessGit:
    """建工作库 + 裸库 + 最小包，registry v1 指向首个包 commit。

    registry.json 必须引用 commit hash，而自身也要被 commit——所以分两步：
    先 commit 包内容得 c1，再写 registry（production=v1 → c1）commit 得 c2。
    head = c1（包内容版本，probe/打包/绑定的目标）。
    """
    work = tmp / "harness_work"
    bare = tmp / "harness.git"
    work.mkdir()
    _init_work_repo(work)
    _write_initial_package(work)

    bare.mkdir()
    git_run(["init", "-q", "--bare"], bare)
    git_run(["symbolic-ref", "HEAD", "refs/heads/main"], bare)
    git_run(["remote", "add", "origin", str(bare)], work)

    git_run(["add", "-A"], work)
    git_run(["commit", "-q", "-m", "init harness package"], work)
    head = git_run(["rev-parse", "HEAD"], work)

    registry = {
        "production": 1,
        "versions": [
            {
                "version": 1,
                "commit_hash": head,
                "change_summary": "init",
                "created_at": REGISTRY_CREATED_AT,
            }
        ],
    }
    if registry_mutate is not None:
        registry_mutate(registry)
    (work / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    repo = HarnessGit(work=work, bare=bare, head=head)
    repo.commit("registry v1")
    return repo


def configure_platform_env(monkeypatch, tmp_path: Path, bare: Path) -> SimpleNamespace:
    """按 tmp 路径设置 PLATFORM_* env 并清 settings 缓存，返回路径视图。"""
    from app.core.settings import get_settings

    data = tmp_path / "svc"
    paths = SimpleNamespace(
        db=data / "platform.db",
        artifacts=data / "artifacts",
        checkouts=data / "checkouts",
        bare=bare,
    )
    monkeypatch.setenv("PLATFORM_DB_PATH", str(paths.db))
    monkeypatch.setenv("PLATFORM_BARE_REPO", str(bare))
    monkeypatch.setenv("PLATFORM_ARTIFACT_DIR", str(paths.artifacts))
    monkeypatch.setenv("PLATFORM_CHECKOUT_DIR", str(paths.checkouts))
    monkeypatch.setenv("PLATFORM_EXECUTOR_URL", "http://executor-test:7788")
    get_settings.cache_clear()
    return paths
