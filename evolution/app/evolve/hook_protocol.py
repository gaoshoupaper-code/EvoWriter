"""AgentMiddleware hook 协议内省 + 签名比对（REQ-20261001-225509 FR-001/FR-002）。

背景：2026-10-01 线上事故——进化产出的 review_gate.py 把 aafter_model 写成
(state, response)。框架把 hook 包成 LangGraph 节点，按参数名注入 state/runtime；
参数名对不上 → 调用只收到 1 个参数 → TypeError，首次真实执行才炸。

本模块把「hook 签名必须与基类一致」变成机器可判的确定性校验：
  - enumerate_base_hooks()      内省当前环境安装的 AgentMiddleware，
                                 列出全部可覆写 hook 及真实签名
  - check_hook_signatures()     逐类比对覆写方法参数名序列，错一个报一个
  - collect_middleware_classes_from_dir()  从包目录直接加载中间件类
                                 （绕开包 __init__ 的重 import，中间件文件
                                 只依赖 langchain/contracts/stdlib）
  - format_hook_table()         给 inspect_middleware_protocol 工具的展示层

零硬编码：比对基线就是当前环境真实安装的框架，升级自动跟随。
（platform probe_worker 有一份同构实现——两服务独立部署、各自比对各自
环境的框架版本；发布侧 probe 跑 executor 同环境，是权威基线，DEC-002。）
"""
from __future__ import annotations

import importlib.metadata
import inspect
import sys
from pathlib import Path

# 兼容性规则：仅 **kwargs（VAR_KEYWORD）视为兼容任意调用框架的合法宽松写法。
# 只有 *args 不豁免——框架按参数名注入关键字，*args-only 覆写在运行时收不到
# 注入参数（review 实测会 TypeError）。
_STAR_KINDS = frozenset({
    inspect.Parameter.VAR_KEYWORD,
})


# 框架包前缀：这些包内定义的 hook 实现是「基类默认实现」，不算进化产物覆写。
# mixin 继承链上非框架基类的 hook 定义同样属于进化产物（review 实测：同文件
# mixin 带错签名 hook 可绕过只查 klass.__dict__ 的旧逻辑）。
_FRAMEWORK_PKG_PREFIXES = ("langchain", "langgraph", "deepagents")


def _base_middleware_class() -> type:
    """取当前环境安装的 AgentMiddleware 基类（比对基线的唯一来源）。"""
    from langchain.agents.middleware.types import AgentMiddleware
    return AgentMiddleware


def _is_framework_base(klass: type) -> bool:
    """判断 MRO 上的类是否框架自带（其 hook 定义不算覆写）。"""
    mod = getattr(klass, "__module__", "") or ""
    return any(mod == p or mod.startswith(p + ".") for p in _FRAMEWORK_PKG_PREFIXES)


def _effective_override(klass: type, hook_name: str):
    """沿 MRO 找第一个非框架定义的 hook 实现。

    返回未解包的函数对象（可能是 staticmethod/classmethod 包装），
    None 表示未覆写。mixin 基类里的错签名 hook 与直接覆写同等拦截。
    """
    for base in klass.__mro__:
        if base is object or _is_framework_base(base):
            continue
        fn = base.__dict__.get(hook_name)
        if fn is not None:
            return fn
    return None


def _unwrap_hook_fn(override):
    """解包 staticmethod/classmethod；非函数形态返回 None（交给 import 层暴露）。"""
    if isinstance(override, (staticmethod, classmethod)):
        override = override.__func__
    if inspect.isfunction(override):
        return override
    return None


def enumerate_base_hooks() -> dict[str, inspect.Signature]:
    """基类全部可覆写 hook → 签名。

    规则：AgentMiddleware.__dict__ 里的公开普通函数即 hook 面
    （langchain 1.x 实测恰为 12 个 hook，无其他公开方法；若未来框架
    新增 hook，本函数自动覆盖，无需改代码）。
    """
    base = _base_middleware_class()
    hooks: dict[str, inspect.Signature] = {}
    for name, obj in vars(base).items():
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj):
            hooks[name] = inspect.signature(obj)
    return hooks


def framework_version() -> str:
    """当前环境 langchain 版本（evolution 与 executor 可能漂移，输出需可见）。"""
    return importlib.metadata.version("langchain")


def _param_names(sig: inspect.Signature) -> list[str]:
    return [p for p in sig.parameters if p != "self"]


def _is_loose(sig: inspect.Signature) -> bool:
    """覆写带 *args/**kwargs → 兼容任意调用方式。"""
    return any(p.kind in _STAR_KINDS for p in sig.parameters.values())


def check_hook_signatures(classes: list[type]) -> list[str]:
    """比对一组中间件类的覆写 hook 签名，返回违规清单。

    每条报错含类名、hook 名、基类正确参数名——模型可据此一次修正。
    """
    errors: list[str] = []
    base_hooks = enumerate_base_hooks()
    seen: set[int] = set()
    for klass in classes:
        for hook_name, base_sig in base_hooks.items():
            override = _effective_override(klass, hook_name)
            if override is None:
                continue  # 未覆写
            fn = _unwrap_hook_fn(override)
            if fn is None:
                continue  # 非函数形态（属性/描述器异型），交给 import/probe 层暴露
            if id(fn) in seen:
                continue  # 同一 mixin 函数被多个子类共享，只报一次
            sub_sig = inspect.signature(fn)
            if _is_loose(sub_sig):
                continue
            base_params = _param_names(base_sig)
            sub_params = _param_names(sub_sig)
            if sub_params != base_params:
                seen.add(id(fn))
                errors.append(
                    f"{klass.__name__}.{hook_name}: hook 参数应为 "
                    f"{base_params}，当前写的是 {sub_params}。框架按参数名注入，"
                    f"参数名/数量不一致 → 运行时 TypeError。"
                )
    return errors


def collect_middleware_classes_from_dir(mw_dir: Path) -> list[type]:
    """从中间件目录直接加载全部 AgentMiddleware 子类。

    以合成父包加载（parent.__path__ = mw_dir）：middleware 文件存在包内
    相对 import（如 from .path_guard import ...），裸模块名解析不了相对
    import；挂到合成包下，相对 import 在目录内正常解析。不经过包
    __init__——中间件文件只依赖 langchain/contracts/stdlib，加载不受
    executor 私有包（app.platform.*）缺位影响。
    """
    import importlib.util
    import types

    base = _base_middleware_class()
    loaded: list[type] = []
    if not mw_dir.is_dir():
        return loaded

    parent_name = f"_hook_check_pkg_{mw_dir.name}"
    parent = types.ModuleType(parent_name)
    parent.__path__ = [str(mw_dir)]
    sys.modules[parent_name] = parent
    try:
        for py in sorted(mw_dir.glob("*.py")):
            if py.name.startswith("__"):
                continue
            mod_name = f"{parent_name}.{py.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, py)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                try:
                    spec.loader.exec_module(mod)
                except Exception:
                    sys.modules.pop(mod_name, None)
                    continue  # import 失败的文件由 validate_changes 的 import 检查报告
            except Exception:
                continue
            for obj in vars(mod).values():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, base)
                    and obj is not base
                    and obj.__module__ == mod_name
                ):
                    loaded.append(obj)
    finally:
        # 清掉合成包注册，避免污染后续 import / 重复收集
        for name in [n for n in sys.modules
                     if n == parent_name or n.startswith(parent_name + ".")]:
            sys.modules.pop(name, None)
    return loaded


def format_hook_table() -> str:
    """全部可覆写 hook 的签名清单（inspect_middleware_protocol 展示层）。"""
    lines = [
        f"## AgentMiddleware 可覆写 hook 协议（动态内省当前环境）",
        f"框架版本：langchain {framework_version()}",
        "",
        "覆写规则：hook 参数名/数量必须与基类完全一致（框架按参数名注入，",
        "写错参数名 → 运行时 TypeError）。写中间件前对照本表；",
        "validate_changes 与发布侧 probe 都会做签名比对，不一致直接拦。",
        "",
        "| hook | 签名 |",
        "|---|---|",
    ]
    for name, sig in sorted(enumerate_base_hooks().items()):
        params = ", ".join(_param_names(sig))
        ret = sig.return_annotation if sig.return_annotation is not inspect.Signature.empty else ""
        ret_str = f" -> {ret}" if ret else ""
        lines.append(f"| `{name}` | `({params}){ret_str}` |")
    return "\n".join(lines)


__all__ = [
    "enumerate_base_hooks",
    "check_hook_signatures",
    "collect_middleware_classes_from_dir",
    "format_hook_table",
    "framework_version",
]
