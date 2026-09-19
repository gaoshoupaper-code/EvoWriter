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
