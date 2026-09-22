"""双架构实验 harness 包装配冒烟（REQ-20260922-162823 FR-001/FR-002）。

与 Platform probe worker 同机制同隔离哲学：**子进程**内 importlib 加载包目录 +
FakeListChatModel 真实装配。子进程原因：harness assemble 链 import executor 的
app.platform.*，与 evolution 自身 app 包撞名，不能同进程混跑（同 probe_worker.py
docstring 的决策谱系）；主进程仅断言退出码与结果 JSON，不污染本套件其他测试。

覆盖：working 包（evolution/harnesses/repo，随 commit 切换 v13/v14 形态）在
full 配比 / minimal 留白两种 demand 下 assemble 产出非空图；线数预算解析正确。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXECUTOR_DIR = _REPO_ROOT / "executor"
_WORKING_PKG = _REPO_ROOT / "evolution" / "harnesses" / "repo"

_DEMAND_FULL = """# 创作需求文档

## 核心层

- **篇幅档位**：21-50章
- **目标配比**（主线 / 支线 / 角色线 / 暗线）：主线5 / 支线1 / 角色线1 / 暗线0
"""

_DEMAND_MINIMAL = """# 创作需求文档

- **题材**：玄幻 · 轻松治愈
- **篇幅档位**：21-50章
"""

# 子进程装配脚本：装配指定 demand，输出一行 JSON {"ok": bool, "error": str?}
_ASSEMBLE_CHILD = r"""
import importlib.util, json, sys, uuid
from pathlib import Path

workspace = Path(sys.argv[1])
pkg_dir = Path(sys.argv[2])

from contracts.runtime_context import RuntimeContext
from langchain_core.language_models.fake_chat_models import FakeListChatModel

class ProbeBackend:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.virtual_mode = True

class ProbeRecorder:
    def record_artifact_revision(self, *a, **k): return None
    def record_middleware_assembly(self, *a, **k): return None
    def record_skill_catalog(self, *a, **k): return None

mod_name = f"assemble_smoke_{uuid.uuid4().hex}"
spec = importlib.util.spec_from_file_location(
    mod_name, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)])
mod = importlib.util.module_from_spec(spec)
sys.modules[mod_name] = mod
spec.loader.exec_module(mod)

ctx = RuntimeContext(
    model=FakeListChatModel(responses=["ok"]),
    backend=ProbeBackend(root_dir=workspace),
    checkpointer=None,
    workspace_path=workspace,
    trace_id="assemble-smoke",
    trace_recorder=ProbeRecorder(),
    artifact_snapshot_callback=lambda _d: None,
)
graph = mod.assemble(ctx)

# 线数预算断言也在子进程做：v13 形态的 storybuilding.py 含相对导入
# （.factory/..middleware），主进程 spec 单文件加载不了（promote 拓扑形态相关）
from contracts.storybuilding_quota import parse_demand_quota

storybuilding = importlib.import_module(f"{mod.__name__}.subagents.storybuilding")
demand_md = (workspace / "demand.md").read_text(encoding="utf-8")
target = parse_demand_quota(demand_md)
budget_full = storybuilding.resolve_line_budget(target)
budget_none = storybuilding.resolve_line_budget(None)
print(json.dumps({
    "ok": graph is not None,
    "budget_full": (budget_full if isinstance(budget_full, int) else None),
    "budget_none": (budget_none if isinstance(budget_none, int) else None),
    "quota_parsed": target is not None,
}))
"""


def _run_assemble_child(demand_md: str) -> dict:
    """spawn 子进程装配 working 包，返回结果 JSON。"""
    stdlib = Path(sys.base_prefix) / "Lib"
    with tempfile.TemporaryDirectory(prefix="assemble_smoke_") as tmp:
        workspace = Path(tmp)
        (workspace / "demand.md").write_text(demand_md, encoding="utf-8")
        env_path = f"{stdlib};{_EXECUTOR_DIR};{_REPO_ROOT}"
        result = subprocess.run(
            [sys.executable, "-c", _ASSEMBLE_CHILD, str(workspace), str(_WORKING_PKG)],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env={"PYTHONPATH": env_path, "PATH": "", "SYSTEMROOT": "C:\\Windows"},
            cwd=str(_WORKING_PKG),  # 固定子进程 CWD：继承 pytest 目录时
            # sys.path[0]=cwd 会让 evolution/app 等遮蔽 executor/app（review P2）
        )
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout)[-800:]}
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": f"bad output: {result.stdout[-300:]}"}


class AssembleSmokeTest(unittest.TestCase):
    def test_assemble_full_quota_demand(self) -> None:
        """full 配比 demand：子进程真实 assemble 产出非空图（FR-001/FR-002）。"""
        result = _run_assemble_child(_DEMAND_FULL)
        self.assertTrue(result["ok"], msg=str(result.get("error")))

    def test_assemble_minimal_demand(self) -> None:
        """minimal 留白 demand（软终止，DEC-013）：assemble 产出非空图。"""
        result = _run_assemble_child(_DEMAND_MINIMAL)
        self.assertTrue(result["ok"], msg=str(result.get("error")))

    def test_line_budget_resolution(self) -> None:
        """线数预算 = 目标总数 + 余量；无配比走 minimal 宽松上限（FR-002①）。

        与装配同子进程执行：v13 形态的 storybuilding.py 含相对导入，
        主进程单文件加载不可行（形态随 promote 拓扑切换）。
        """
        result = _run_assemble_child(_DEMAND_FULL)
        self.assertTrue(result["ok"], msg=str(result.get("error")))
        self.assertTrue(result["quota_parsed"])
        self.assertEqual(result["budget_full"], 7 + 2)
        self.assertEqual(result["budget_none"], 8)

        result_minimal = _run_assemble_child(_DEMAND_MINIMAL)
        self.assertTrue(result_minimal["ok"], msg=str(result_minimal.get("error")))
        self.assertFalse(result_minimal["quota_parsed"])
        self.assertEqual(result_minimal["budget_none"], 8)


if __name__ == "__main__":
    unittest.main()
