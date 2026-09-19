"""Agent 包加载器（Phase 7 T3.1 + Phase 8 compose 热加载 + Phase A 去 git 化）。

执行端通过 importlib 加载 harness 包目录作为 Python package，
调用方取 mod.assemble(ctx) 装配完整 agent（单参数契约）。

加载机制（关键）：
  importlib.util.spec_from_file_location + submodule_search_locations。
  submodule_search_locations 是让包内相对 import（from .middleware import X）生效的
  关键——没有它，包被当作普通模块加载，相对 import 会失败。

Phase A 变更（REQ-20260919-202344，DEC-012 去 git 化）：
  - 生产/候选包来源从「git pull bare repo」改为「Platform artifact 下载 + digest
    校验 + 本地缓存解包」（artifact_client），本模块不再有任何 git 操作。
  - load_current_package()：Platform 查生产 commit → ensure_artifact → 加载。
  - reload_current(commit, digest)：热重载指定/当前生产版本（晋升通知触发）。
  - production_commit()/production_checkout()：记忆最近一次加载的 commit/解包目录，
    供 Run 绑定与 runtime_identity 使用（不再读 git 元数据——artifact 目录无 .git）。

设计依据：设计文档 D8=X + D10b + #16 + D9a + REQ-20260919-202344 DEC-012。
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

logger = logging.getLogger("writer.package_loader")

# 模块级缓存：生产包加载一次后复用（热加载时清缓存重建，决策 #16）
_loaded_package: ModuleType | None = None
# 最近一次加载的生产 commit（未加载时为空串）——Run 绑定 / trace 快照用
_current_commit: str = ""
# 最近一次加载的 artifact 解包目录——runtime_identity / 身份指纹用
_current_checkout: Path | None = None


def _artifact_client():
    from app.platform.agent.artifact_client import get_artifact_client

    return get_artifact_client()


def load_package(pkg_path: Path, mod_name: str = "harness_current") -> ModuleType:
    """加载指定路径的 Agent 包，返回包模块（含 assemble 函数）。

    通用加载函数：生产路径和候选 A/B 路径都用它，只是传不同的 pkg_path。
    submodule_search_locations 让包内相对 import 生效。

    Args:
        pkg_path: 包根目录（含 __init__.py）
        mod_name: 模块注册名（生产用 harness_current，候选用唯一名避免冲突）

    Returns:
        包模块对象，调用方取 mod.assemble(ctx) 装配 agent。

    Raises:
        FileNotFoundError: 包目录或 __init__.py 不存在。
        ImportError: 包 __init__.py 执行失败（含包内 import 错误）。
    """
    pkg_path = pkg_path.resolve()
    init_path = pkg_path / "__init__.py"
    if not init_path.exists():
        raise FileNotFoundError(f"Agent 包不存在: {init_path}")

    spec = importlib.util.spec_from_file_location(
        mod_name,
        init_path,
        submodule_search_locations=[str(pkg_path)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法创建包加载 spec: {init_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    logger.info("Agent 包已加载: %s (mod=%s)", pkg_path, mod_name)
    return mod


def load_current_package() -> ModuleType:
    """加载生产 Agent 包，返回包模块。

    Phase A：从 Platform 查生产 commit → artifact_client 下载校验解包 →
    importlib 加载解包目录。幂等：首次加载后缓存，reload_current() 后重新加载。

    冷启动 fail-static（DEC-008）：Platform 不可达时回退本地
    known_production.json 记录的 commit + 本地 artifact 缓存装配（缓存命中
    零 HTTP），并 warning 标记降级；known/缓存都没有则抛原异常。

    Raises:
        RuntimeError: Platform 不可达（且无本地回退）/ 无生产版本 /
                      artifact 校验失败（调用方决定是否降级）。
    """
    global _loaded_package, _current_commit, _current_checkout
    if _loaded_package is not None:
        return _loaded_package

    client = _artifact_client()
    try:
        status = client.current_production()
    except Exception as exc:
        _load_from_known_production(client, exc)
        return _loaded_package
    checkout = client.ensure_artifact(status.commit)
    _loaded_package = load_package(checkout, "harness_current")
    _current_commit = status.commit
    _current_checkout = checkout
    logger.info("生产包已就绪: commit=%s version=%s", status.commit, status.version)
    return _loaded_package


def _load_from_known_production(client, exc: Exception) -> None:
    """Platform 不可达时的冷启动回退：known_production commit + 本地缓存装配。

    成功则填好模块级缓存（_loaded_package/_current_commit/_current_checkout）
    并 warning 标记降级；known_production 或本地缓存不可用则抛回原异常
    （exc）——根因是 Platform 不可达，回退失败的细节只进日志。
    """
    global _loaded_package, _current_commit, _current_checkout
    from app.platform.agent.binding_client import get_binding_client

    known = get_binding_client().get_known_production()
    fallback_commit = str((known or {}).get("commit") or "")
    if not fallback_commit:
        raise exc
    try:
        checkout = client.ensure_artifact(fallback_commit)
    except Exception:
        logger.warning(
            "冷启动回退失败（本地缓存无 commit=%s）", fallback_commit, exc_info=True,
        )
        raise exc from None
    logger.warning(
        "冷启动降级：Platform 不可达，用本地缓存装配已知生产 commit=%s（err=%s）",
        fallback_commit, exc,
    )
    _loaded_package = load_package(checkout, "harness_current")
    _current_commit = fallback_commit
    _current_checkout = checkout


def reload_current(commit: str | None = None, digest: str | None = None) -> ModuleType:
    """热加载：清缓存 + 拉取 artifact + 重新加载生产包（决策 #16，不重启进程）。

    有 commit（晋升 reload 通知携带）用之；无 commit 向 Platform 对账取当前
    生产版本。digest 非空时 ensure_artifact 会比对 Platform meta，不符拒绝装配。

    Returns: 重新加载后的包模块。
    """
    global _loaded_package, _current_commit, _current_checkout
    # 清缓存：pop sys.modules 里包及其子模块（middleware.* 等）
    _purge_package_modules("harness_current")
    _loaded_package = None

    client = _artifact_client()
    if commit is None:
        commit = client.current_production().commit
    checkout = client.ensure_artifact(commit, expected_digest=digest)
    _loaded_package = load_package(checkout, "harness_current")
    _current_commit = commit
    _current_checkout = checkout
    logger.info("生产包热加载完成: commit=%s", commit)
    return _loaded_package


def production_commit() -> str:
    """最近一次加载的生产 commit（str；尚未加载时为空串，不触发加载/HTTP）。"""
    return _current_commit


def production_checkout() -> Path:
    """当前生产 artifact 解包目录（runtime_identity / 身份指纹用）。

    Raises:
        RuntimeError: 尚未加载生产包（先 load_current_package / reload_current）。
    """
    if _current_checkout is None:
        raise RuntimeError("生产 artifact 尚未加载（先 load_current_package）")
    return _current_checkout


def _purge_package_modules(prefix: str) -> None:
    """从 sys.modules 清除指定包前缀的所有模块（含子模块）。"""
    keys_to_remove = [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]
    for k in keys_to_remove:
        sys.modules.pop(k, None)


def reset_cache() -> None:
    """清除包缓存（测试用，或手动重载）。生产路径用 reload_current()。"""
    global _loaded_package, _current_commit, _current_checkout
    if _loaded_package is not None:
        _purge_package_modules("harness_current")
        _loaded_package = None
    _current_commit = ""
    _current_checkout = None
