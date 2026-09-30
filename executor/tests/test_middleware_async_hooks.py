"""中间件异步 hook 协程性防回归测试（线上 NoneType await 事故）。

事故：revision_limit.abefore_agent 漏写 async——同步函数返回 None，
langgraph 异步路径 ``await None`` 抛 TypeError，创作流首跑即崩
（v13 形态切回后第一次真实任务暴露）。
本测试静态扫描 harness 包 middleware/ 下全部中间件类：所有 ``a`` 前缀
异步 hook（abefore_/aafter_/awrap_）必须是协程函数。
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

from app.platform.agent.loader import load_package

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
load_package(_HARNESS_DIR)

import harness_current.middleware as mw_pkg  # noqa: E402
from langchain.agents.middleware.types import AgentMiddleware  # noqa: E402

_ASYNC_HOOK_PREFIXES = ("abefore_", "aafter_", "awrap_")


def _iter_middleware_classes():
    for mod_info in pkgutil.iter_modules(mw_pkg.__path__):
        mod = importlib.import_module(f"harness_current.middleware.{mod_info.name}")
        for attr_name, attr in vars(mod).items():
            if (
                inspect.isclass(attr)
                and issubclass(attr, AgentMiddleware)
                and attr is not AgentMiddleware
                and attr.__module__ == mod.__name__
            ):
                yield attr


def test_all_async_hooks_are_coroutine_functions() -> None:
    offenders: list[str] = []
    found = 0
    for cls in _iter_middleware_classes():
        for hook_name, func in vars(cls).items():
            if hook_name.startswith(_ASYNC_HOOK_PREFIXES) and callable(func):
                found += 1
                if not inspect.iscoroutinefunction(func):
                    offenders.append(f"{cls.__name__}.{hook_name}")
    assert found > 0, "未扫描到任何中间件异步 hook——扫描逻辑失效"
    assert not offenders, f"异步 hook 漏写 async（await None 事故源头）: {offenders}"
