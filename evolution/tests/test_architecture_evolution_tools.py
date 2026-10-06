"""进化侧架构清单工具测试（REQ-20261006-130414 FR-002/003 / AC-003/005 前置）。

delete_file 的清单引用自动清理 + validate_changes 的清单强校验挂钩。
夹具直接用真实 harness 包副本（tmp 复制，不污染工作树）。
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PKG = REPO_ROOT / "evolution" / "harnesses" / "repo"


@pytest.fixture()
def pkg_copy(tmp_path: Path) -> Path:
    """真实 harness 包的干净副本（含 .git 排除）。"""
    dst = tmp_path / "pkg"
    shutil.copytree(
        HARNESS_PKG, dst,
        ignore=shutil.ignore_patterns("__pycache__", ".git"),
    )
    return dst


class _FakeCtx:
    """最小工具上下文：计数器 + step 事件收集。"""

    def __init__(self):
        self.code_mutations_since_validate = 0
        self.events: list[tuple] = []

    def emit_step(self, tool, status, **kw):
        self.events.append((tool, status, kw))


class _FakeBackend:
    """backend 桩：只提供 root_dir（delete_file 的路径锚点）。"""

    def __init__(self, root: Path):
        self.root_dir = root


def _make_writers(pkg: Path):
    evo_root = REPO_ROOT / "evolution"
    if str(evo_root) not in sys.path:
        sys.path.insert(0, str(evo_root))
    from app.evolve.agent.tools.writers import make_writer_tools

    return {
        t.name: t for t in make_writer_tools(_FakeBackend(pkg))
    }, _FakeCtx()


def _invoke(tool, ctx, **kw):
    """langchain @tool 调用：上下文经 ctxvar 注入。"""
    from app.evolve.ctx import set_tool_context

    set_tool_context(ctx)  # type: ignore[arg-type]  # 测试桩鸭子类型
    return tool.invoke(kw)


def test_delete_file_removes_middleware_and_cleans_manifest(pkg_copy):
    """删 domain middleware：文件删除 + 清单列表引用清理（AC-005 前置）。"""
    tools, ctx = _make_writers(pkg_copy)
    result = _invoke(tools["delete_file"], ctx, file_path="middleware/quota_convergence.py")
    assert "已删除" in result, result
    assert not (pkg_copy / "middleware" / "quota_convergence.py").exists()
    manifest = json.loads((pkg_copy / "architecture.json").read_text(encoding="utf-8"))
    story = next(a for a in manifest["agents"] if a["name"] == "storybuilding")
    assert "quota_convergence" not in story["middleware"]
    assert ctx.code_mutations_since_validate == 1


def test_delete_prompt_removes_agent_and_edges(pkg_copy):
    """删 agent 的 prompt：整条 agent 移除 + 委托边清理。"""
    tools, ctx = _make_writers(pkg_copy)
    result = _invoke(tools["delete_file"], ctx, file_path="prompts/storybuilding_review.md")
    assert "已删除" in result, result
    manifest = json.loads((pkg_copy / "architecture.json").read_text(encoding="utf-8"))
    names = [a["name"] for a in manifest["agents"]]
    assert "storybuilding_review" not in names
    story = next(a for a in manifest["agents"] if a["name"] == "storybuilding")
    assert story["delegates"] == []


def test_delete_file_refuses_manifest_and_contracts(pkg_copy):
    """红线：架构清单本体与 contracts/ 前缀拒删。"""
    tools, ctx = _make_writers(pkg_copy)
    r1 = _invoke(tools["delete_file"], ctx, file_path="architecture.json")
    assert "不可删除" in r1
    r2 = _invoke(tools["delete_file"], ctx, file_path="contracts/../middleware/retry.py")
    assert "CON-002" in r2 or "非法路径" in r2


def test_delete_file_missing(pkg_copy):
    tools, ctx = _make_writers(pkg_copy)
    result = _invoke(tools["delete_file"], ctx, file_path="middleware/nope.py")
    assert "不存在" in result


def test_validate_catches_dangling_manifest_reference(pkg_copy):
    """清单引用悬空被 validate 拦（AC-003）：直接手改清单加不存在 middleware。"""
    from contracts.architecture_manifest import load_and_validate

    manifest_file = pkg_copy / "architecture.json"
    raw = json.loads(manifest_file.read_text(encoding="utf-8"))
    raw["agents"][0]["middleware"].append("ghost_mw")
    manifest_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    _, result = load_and_validate(pkg_copy)
    assert not result.ok
    assert any("middleware 文件不存在" in e for e in result.errors)
