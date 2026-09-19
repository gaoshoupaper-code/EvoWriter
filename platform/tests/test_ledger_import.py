"""FR-001 初始导入测试：首次导入正确 / 二次启动幂等 / registry 缺 commit_hash 报错。"""
from __future__ import annotations

import pytest

from app.core.ledger import Ledger
from app.main import bootstrap_ledger
from tests_helpers import configure_platform_env, make_harness_repo


def _ledger_from_settings() -> Ledger:
    from app.core.settings import get_settings, resolve_path

    return Ledger(resolve_path(get_settings().db_path))


def test_first_import_populates_versions_and_production(platform_env, harness_git):
    bootstrap_ledger()

    ledger = Ledger(platform_env.db)
    versions = ledger.list_versions()
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["commit"] == harness_git.head
    assert versions[0]["note"] == "init"

    production = ledger.get_production()
    assert production is not None
    assert production.version == 1
    assert production.commit == harness_git.head
    assert production.promoted_at  # 导入时间戳非空


def test_second_boot_is_idempotent(platform_env, harness_git):
    bootstrap_ledger()
    ledger = Ledger(platform_env.db)
    count_after_first = ledger.versions_count()

    bootstrap_ledger()  # 二次启动：账本非空 → 跳过，不重复不覆盖
    assert ledger.versions_count() == count_after_first == 1
    assert ledger.get_production().version == 1


def test_registry_missing_commit_hash_fails_startup(tmp_path, monkeypatch):
    # registry 某条 version 缺 commit_hash → 启动（导入）必须报错
    def _drop_commit_hash(registry: dict) -> None:
        registry["versions"][0]["commit_hash"] = None

    repo = make_harness_repo(tmp_path, registry_mutate=_drop_commit_hash)
    configure_platform_env(monkeypatch, tmp_path, repo.bare)

    with pytest.raises(RuntimeError, match="commit_hash"):
        bootstrap_ledger()

    # 报错不留半导入状态：versions 表仍为空
    assert _ledger_from_settings().versions_count() == 0


def test_registry_without_production_pointer_fails(tmp_path, monkeypatch):
    def _drop_production(registry: dict) -> None:
        del registry["production"]

    repo = make_harness_repo(tmp_path, registry_mutate=_drop_production)
    configure_platform_env(monkeypatch, tmp_path, repo.bare)

    with pytest.raises(RuntimeError, match="production"):
        bootstrap_ledger()


def test_bare_without_registry_skips_import(tmp_path, monkeypatch):
    """bare repo 无可读 registry.json（空目录/未初始化）→ 跳过导入，允许空启动。"""
    empty_bare = tmp_path / "empty.git"
    empty_bare.mkdir()
    configure_platform_env(monkeypatch, tmp_path, empty_bare)

    bootstrap_ledger()  # 不抛错

    ledger = _ledger_from_settings()
    assert ledger.versions_count() == 0
    assert ledger.get_production() is None
