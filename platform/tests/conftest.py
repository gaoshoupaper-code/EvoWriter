"""platform 测试公共 fixture。

sys.path 处理：platform/（app 包根）+ 仓库根（contracts 包根）+ 本目录
（tests_helpers）都进 path。executor/tests 无 conftest（直接 import app），
此处补齐同义机制：测试从任意 cwd 启动都能导入 app 与 contracts。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PLATFORM_DIR = TESTS_DIR.parent
REPO_ROOT = PLATFORM_DIR.parent
# platform/ 在前：`app` 解析到 platform/app；仓库根在后：contracts 可导入
for _p in (str(PLATFORM_DIR), str(REPO_ROOT), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests_helpers import HarnessGit, configure_platform_env, make_harness_repo  # noqa: E402


@pytest.fixture
def harness_git(tmp_path) -> HarnessGit:
    """tmp 内的 harness 工作库 + 裸库（含最小可装配包与 registry v1）。"""
    return make_harness_repo(tmp_path)


@pytest.fixture
def platform_env(tmp_path, harness_git, monkeypatch) -> SimpleNamespace:
    """PLATFORM_* env 指向 tmp（隔离 db/artifacts/checkout/bare），前后清 settings 缓存。"""
    paths = configure_platform_env(monkeypatch, tmp_path, harness_git.bare)
    yield paths
    from app.core.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture
def client(platform_env):
    """TestClient（进入时跑 lifespan：建表 + FR-001 初始导入）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
