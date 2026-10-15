"""架构清单解释器测试（REQ-20260906-130414 FR-001；三步制改造 REQ-20261015-161200）。

对真实 harness 包（evolution/harnesses/repo）做清单驱动装配，断言装配
产物形态：单主控（writer）+ 三子代理（worldview/outline/chapters）、按名
装配、middleware 链构成、命名派生、style suffix 注入、跨步骤只读白名单。
加载机制对齐 Platform probe（唯一模块名 + 相对 import）。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PKG = REPO_ROOT / "evolution" / "harnesses" / "repo"

pytestmark = pytest.mark.skipif(
    not HARNESS_PKG.is_dir(), reason="本地无 harness 仓库镜像"
)


def _load_pkg() -> str:
    mod_name = f"arch_test_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(
        mod_name, HARNESS_PKG / "__init__.py",
        submodule_search_locations=[str(HARNESS_PKG)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod_name


@pytest.fixture()
def build_ctx():
    """最小装配 ctx（对齐 Platform probe 的最小构造）。"""
    from contracts.runtime_context import RuntimeContext
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from deepagents.backends.filesystem import FilesystemBackend

    def _make(styles=None):
        tmp = tempfile.TemporaryDirectory(prefix="arch_asm_test_")
        workspace = Path(tmp.name)
        ctx = RuntimeContext(
            model=FakeListChatModel(responses=["ok"]),
            backend=FilesystemBackend(root_dir=workspace, virtual_mode=True),
            checkpointer=None,
            workspace_path=workspace,
            trace_id="",
            trace_recorder=None,
            styles=styles,
        )
        return tmp, ctx

    return _make


def test_assemble_from_manifest_produces_graph(build_ctx):
    """清单装配产出非空编译图（probe 契约）；缺省 = 唯一 main。"""
    from app.platform.agent.architecture import assemble_from_manifest

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        graph = assemble_from_manifest(ctx, package=mod_name)
        assert graph is not None
        named = assemble_from_manifest(ctx, package=mod_name, agent_name="writer")
        assert named is not None
    finally:
        tmp.cleanup()


def test_manifest_shape_single_main_three_subs():
    """清单形态 = 单主控 + 三子代理（REQ-20261015-161200 DEC-005）：无技能。"""
    from contracts.architecture_manifest import load_architecture_manifest

    manifest = load_architecture_manifest(HARNESS_PKG)
    assert [a.name for a in manifest.agents] == ["writer", "worldview", "outline", "chapters"]
    main = manifest.agent("writer")
    assert main.role == "main"
    assert main.delegates == ["worldview", "outline", "chapters"]
    # 三子代理：拍板闸 + outline 结构契约两件（DEC-003 清理后仅存）
    for name in ("worldview", "outline", "chapters"):
        sub = manifest.agent(name)
        assert sub.role == "sub"
        assert sub.middleware == ["receipt_gate", "outline_contract_guard"]
        assert sub.skills == []
    # 主控：拍板闸（纯记账 owner，无写权限）
    assert main.middleware == ["receipt_gate"]


def test_chapters_lane_permissions_enforce_upstream_readonly():
    """FR-006 上游只读：chapters 白名单不含世界观/总纲/卷纲/core；读全放行 + 写默认拒。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import _build_permissions

    manifest = load_architecture_manifest(HARNESS_PKG)
    perms = _build_permissions(manifest.agent("chapters"))
    assert perms[0].operations == ["read"] and perms[0].mode == "allow"
    write_allow_globs = [p for op in perms for p in op.paths if op.operations == ["write"] and op.mode == "allow"]
    for upstream in ("/outline/世界观.md", "/outline/总纲.md", "/outline/卷纲-*.md", "/character/core/"):
        assert not any(upstream in g for g in write_allow_globs), upstream
    assert any("/outline/章纲-*.md" in g for g in write_allow_globs)
    deny = [p for p in perms if p.mode == "deny"]
    assert deny and deny[0].paths == ["/**"]


def test_main_agent_trace_name_and_compiled_shape(build_ctx):
    """主控：trace 名派生（writer-subagent）+ 编译图构成。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx, _build_compiled

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        main = manifest.agent("writer")
        abc = AgentBuildCtx(ctx, manifest, main, mod_name, HARNESS_PKG)
        assert abc.trace_name == "writer-subagent"
        compiled = _build_compiled(abc)
        assert compiled["name"] == "writer"
        assert compiled["runnable"] is not None
    finally:
        tmp.cleanup()


def test_style_suffix_injected(build_ctx):
    """style suffix 按 manifest agent 名注入 prompt 尾部（SUFFIX 槽位语义）。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx, _system_prompt

    tmp, ctx = build_ctx(styles={"writer": "文风：冷峻克制"})
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        abc = AgentBuildCtx(ctx, manifest, manifest.agent("writer"), mod_name, HARNESS_PKG)
        prompt = _system_prompt(abc)
        assert prompt.rstrip().endswith("文风：冷峻克制")
        assert "主控" in prompt  # 主文档主体在
    finally:
        tmp.cleanup()


def test_chapters_trace_name_derivation():
    """trace 名 = 架构名下划线转连字符 + -subagent（进化端曲线口径不变）。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx

    import tempfile

    tmp = tempfile.TemporaryDirectory()
    from contracts.runtime_context import RuntimeContext

    ctx = RuntimeContext(
        model=object(), backend=object(), checkpointer=None,
        workspace_path=Path(tmp.name), trace_id="", trace_recorder=None,
    )
    try:
        mod_name = _load_pkg()
        from contracts.architecture_manifest import load_architecture_manifest

        manifest = load_architecture_manifest(HARNESS_PKG)
        abc = AgentBuildCtx(ctx, manifest, manifest.agent("chapters"), mod_name, HARNESS_PKG)
        assert abc.trace_name == "chapters-subagent"
    finally:
        tmp.cleanup()
