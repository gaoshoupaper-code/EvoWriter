"""probe worker —— 在独立子进程里执行候选包的真实装配门禁。

为什么必须子进程：生产 harness 包是薄包装，assemble 链 import executor 的
`app.platform.*`（Phase 7 迁移中间态）。而 platform 服务自己的顶层包也叫
`app`，同进程注入 sys.path 会与 executor 的 app 包撞名（regular package 的
__path__ 固定，子模块交错解析不可控）。子进程里以 executor 目录为 app 唯一
来源，冲突天然消解，且装配崩溃/内存污染不影响主进程。

运行方式（由 app.agent.probe 用 subprocess 调起，不直接手工运行）：
    python probe_worker.py <checkout_dir> <commit>
PYTHONPATH 由父进程注入（仓库根 + executor 目录），本文件只依赖 stdlib +
contracts + langchain-core + executor 的 app 包。

输出：stdout 一行 JSON {status: ready|rejected, reason, identity}。
任何异常收敛为 rejected（门禁语义：失败=拒绝，不 500）。
"""
from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProbeBackend:
    """probe 专用虚拟后端（等价实现，替代 deepagents FilesystemBackend）。

    harness 的 assemble 只把 backend 放进 ctx 供构建图消费，probe 阶段不执行
    图——保留 deepagents backend 的身份字段（root_dir / virtual_mode）即可
    通过包内 isinstance/attr 探测。运行时取证仍在 executor 装配链里。
    """

    root_dir: Path
    virtual_mode: bool = True


class _ProbeRecorder:
    """trace_recorder 桩：装配链可能回调 recorder 埋点，门禁只关心不抛错。"""

    def record_artifact_revision(self, *_args, **_kwargs):
        return None

    def record_middleware_assembly(self, *_args, **_kwargs):
        return None

    def record_skill_catalog(self, *_args, **_kwargs):
        return None


def _load_package(pkg_path: Path, mod_name: str):
    """importlib 加载包目录（与 executor loader 同机制，自包含复制）。"""
    import importlib.util

    init_path = pkg_path / "__init__.py"
    if not init_path.is_file():
        raise FileNotFoundError(f"Agent 包不存在: {init_path}")
    spec = importlib.util.spec_from_file_location(
        mod_name, init_path, submodule_search_locations=[str(pkg_path)],
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
    return mod


def _check_hook_signatures(checkout: Path, mod_prefix: str = "") -> str | None:
    """hook 签名硬门（REQ-20261001-225509 FR-001 发布侧兜底，DEC-002）。

    与 evolution 侧 hook_protocol.check_hook_signatures 同构：覆写 hook 的
    参数名必须与 AgentMiddleware 基类完全一致（框架按参数名注入，参数名错
    → 运行时 TypeError）。probe 跑 executor 同环境，比对基线即真正执行
    代码的框架版本——Agent 侧校验被跳过/版本漂移时这里是权威门。

    覆写解析沿 MRO 找第一个非框架定义（mixin 继承的错签名 hook 同拦），
    解包 staticmethod/classmethod 后比对；仅 **kwargs 算合法宽松写法。

    扫描面（mod_prefix 为已加载包名时取并集，按类名去重）：
      ① middleware/ 目录逐文件直载——未挂载的孤儿文件同样是隐患
      ② 已加载包的全部子模块——subagents/tools 等目录定义、被 __init__
         import 的中间件类（evolution 侧因缺 executor 私有包扫不到）

    Returns:
        违规描述（拒绝理由），None 表示通过。
    """
    import importlib.util
    import inspect
    import types

    from langchain.agents.middleware.types import AgentMiddleware

    framework_prefixes = ("langchain", "langgraph", "deepagents")
    base_hooks = {
        name: inspect.signature(obj)
        for name, obj in vars(AgentMiddleware).items()
        if not name.startswith("_") and inspect.isfunction(obj)
    }
    # 仅 **kwargs 豁免：框架按参数名注入，*args-only 收不到注入参数
    loose_kind = inspect.Parameter.VAR_KEYWORD

    def _is_framework_base(klass) -> bool:
        mod = getattr(klass, "__module__", "") or ""
        return any(
            mod == p or mod.startswith(p + ".") for p in framework_prefixes
        )

    def _effective_override(klass, hook_name):
        for base in klass.__mro__:
            if base is object or _is_framework_base(base):
                continue
            fn = base.__dict__.get(hook_name)
            if fn is not None:
                return fn
        return None

    def _check_class(obj, errors, seen_fn_ids) -> None:
        for hook, base_sig in base_hooks.items():
            override = _effective_override(obj, hook)
            if override is None:
                continue
            if isinstance(override, (staticmethod, classmethod)):
                override = override.__func__
            if not inspect.isfunction(override):
                continue
            if id(override) in seen_fn_ids:
                continue
            sub_sig = inspect.signature(override)
            if any(p.kind is loose_kind for p in sub_sig.parameters.values()):
                continue
            base_params = [p for p in base_sig.parameters if p != "self"]
            sub_params = [p for p in sub_sig.parameters if p != "self"]
            if sub_params != base_params:
                seen_fn_ids.add(id(override))
                errors.append(
                    f"{obj.__name__}.{hook} 参数应为 {base_params}，"
                    f"实际 {sub_params}"
                )

    errors: list[str] = []
    seen_fn_ids: set[int] = set()
    seen_class_names: set[str] = set()
    mw_dir = checkout / "middleware"

    # ① middleware/ 目录直载（合成父包解析相对 import）
    if mw_dir.is_dir():
        parent_name = "platform_probe_hook_check"
        parent = types.ModuleType(parent_name)
        parent.__path__ = [str(mw_dir)]
        sys.modules[parent_name] = parent
        try:
            for py in sorted(mw_dir.glob("*.py")):
                if py.name.startswith("__"):
                    continue
                mod_name = f"{parent_name}.{py.stem}"
                spec = importlib.util.spec_from_file_location(mod_name, py)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                try:
                    spec.loader.exec_module(mod)
                except Exception:
                    sys.modules.pop(mod_name, None)
                    continue  # import 失败的文件由装配阶段暴露
                for obj in vars(mod).values():
                    if not (
                        isinstance(obj, type)
                        and issubclass(obj, AgentMiddleware)
                        and obj is not AgentMiddleware
                        and obj.__module__ == mod_name
                    ):
                        continue
                    seen_class_names.add(obj.__name__)
                    _check_class(obj, errors, seen_fn_ids)
        finally:
            for name in [
                n for n in sys.modules
                if n == parent_name or n.startswith(parent_name + ".")
            ]:
                sys.modules.pop(name, None)

    # ② 已加载包的全部子模块（mod_prefix 非空时）——扫 subagents/tools
    #    等目录定义且被装配链 import 的中间件类
    if mod_prefix:
        for mod_name, mod in list(sys.modules.items()):
            if mod is None or not mod_name.startswith(mod_prefix + "."):
                continue
            for obj in vars(mod).values():
                if not (
                    isinstance(obj, type)
                    and issubclass(obj, AgentMiddleware)
                    and obj is not AgentMiddleware
                    and obj.__module__ == mod_name
                ):
                    continue
                if obj.__name__ in seen_class_names:
                    continue  # ① 已查过同一类（目录直载副本）
                _check_class(obj, errors, seen_fn_ids)

    if errors:
        return "hook 签名不匹配：" + "; ".join(dict.fromkeys(errors))
    return None


def main() -> int:
    import subprocess
    import tempfile

    from contracts.runtime_context import RuntimeContext
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    checkout = Path(sys.argv[1]).resolve()
    commit = sys.argv[2]
    module_name = f"platform_probe_{uuid.uuid4().hex}"

    def _reject(reason: str) -> int:
        print(json.dumps({"status": "rejected", "reason": reason}, ensure_ascii=False))
        return 0

    try:
        if not (checkout / "middleware" / "artifact_snapshot.py").is_file():
            return _reject(f"candidate {commit} 缺少 middleware/artifact_snapshot.py")
        package = _load_package(checkout, module_name)
        with tempfile.TemporaryDirectory(prefix="platform_probe_workspace_") as tmp:
            workspace = Path(tmp)
            context = RuntimeContext(
                model=FakeListChatModel(responses=["ok"]),
                backend=ProbeBackend(root_dir=workspace, virtual_mode=True),
                checkpointer=None,
                workspace_path=workspace,
                trace_id="harness-probe",
                trace_recorder=_ProbeRecorder(),
                artifact_snapshot_callback=lambda _data: None,
            )
            graph = package.assemble(context)
        # assemble 契约必须产出图：返回 None 说明包坏了或副作用吞了返回值，
        # 「不抛异常」不等于可发布（review #7）
        if graph is None:
            return _reject(f"candidate {commit} assemble 返回空图")
        # 装配后复查工作区改动（assemble 期间写文件 = 包有副作用，拒）。
        # 全量含 untracked：装配期新增文件同样是副作用（-uno 会放过，review #8）；
        # __pycache__/*.pyc 是 importlib 字节码缓存（机器副产物），过滤后再判。
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=checkout, capture_output=True, text=True, timeout=30,
        )
        if dirty.returncode != 0:
            return _reject(
                f"candidate {commit} 装配后 git status 失败: {dirty.stderr.strip()}"
            )
        real_changes = [
            line for line in dirty.stdout.splitlines()
            if line.strip()
            and "__pycache__/" not in line
            and not line.strip().endswith(".pyc")
        ]
        if real_changes:
            return _reject(f"candidate {commit} 装配后 checkout 不干净（包有副作用）")
        # hook 签名硬门放在装配后：此时装配链 import 的全部子模块都在
        # sys.modules 里，扫描面取「middleware 目录直载 ∪ 包子模块」并集。
        hook_violation = _check_hook_signatures(checkout, module_name)
        if hook_violation:
            return _reject(f"candidate {commit} {hook_violation}")
        print(json.dumps({
            "status": "ready",
            "identity": {
                "harness_commit": commit,
                "harness_dirty": False,
                "artifact_snapshot_middleware": True,
            },
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — 门禁语义：一切异常 = rejected
        return _reject(f"{type(exc).__name__}: {exc}")
    finally:
        for name in list(sys.modules):
            if name == module_name or name.startswith(module_name + "."):
                sys.modules.pop(name, None)


if __name__ == "__main__":
    sys.exit(main())
