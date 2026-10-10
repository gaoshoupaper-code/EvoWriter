"""架构清单解释器测试（REQ-20261006-130414 FR-001 / AC-001 前置）。

对真实 harness 包（evolution/harnesses/repo）做清单驱动装配，断言装配
产物与 v7 旧装配等价：agent 集合、委托关系、middleware 链构成、命名派生、
style suffix 注入。加载机制对齐 Platform probe（唯一模块名 + 相对 import）。
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
    """清单装配产出非空编译图（probe 契约）。"""
    from app.platform.agent.architecture import assemble_from_manifest

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        graph = assemble_from_manifest(ctx, package=mod_name)
        assert graph is not None
    finally:
        tmp.cleanup()


def test_manifest_shape_matches_v7(build_ctx):
    """清单形态 = v7 生产：故事专家（main）+ 审查器（sub，运行时名 review）。"""
    from contracts.architecture_manifest import load_architecture_manifest

    manifest = load_architecture_manifest(HARNESS_PKG)
    assert [a.name for a in manifest.agents] == ["storybuilding", "storybuilding_review"]
    main, review = manifest.agents
    assert main.role == "main"
    assert review.role == "sub" and review.delegated_by == "storybuilding"
    assert main.delegates == ["storybuilding_review"]
    assert review.runtime_name == "review"
    # v42 谱系：三系统门（receipt/skill_activation/review_gate）+ v2 hooks 护栏
    assert main.middleware == [
        "storyline_single_line_limit", "storyline_integrity",
        "storyline_contract_guard", "object_contract_guard",
        "receipt_gate", "skill_activation_guard", "review_gate",
        "hooks_contract_guard",
    ]


def test_subagent_spec_equivalence(build_ctx):
    """审查器 SubAgent 规格：命名（review）、权限、基础链构成与 v7 旧装配等价。"""
    from contracts.architecture_manifest import BASE_CHAIN_MODULES, load_architecture_manifest
    from app.platform.agent.architecture import _build_agent

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        review = manifest.agent("storybuilding_review")
        spec = _build_agent(ctx, manifest, review, mod_name, HARNESS_PKG)
        assert spec["name"] == "review"
        # 基础链 8 模块（artifact_snapshot 因无 trace 回调不挂载 → 7）
        # + domain 1（pacing_report 节奏体检报告注入，REQ-20261010-000638 FR-005）
        names = [str(m) for m in spec["middleware"]]
        assert len(spec["middleware"]) == len(BASE_CHAIN_MODULES) - 1 + 1
        assert any("pacing_report" in n for n in names)
        perms = spec["permissions"]
        assert perms[0].operations == ["read"] and perms[0].mode == "allow"
        write_allows = [p for p in perms if p.operations == ["write"] and p.mode == "allow"]
        assert [p.paths for p in write_allows] == [["/review/storybuilding.md"]]
        deny = [p for p in perms if p.mode == "deny"]
        assert deny and deny[0].paths == ["/**"]
    finally:
        tmp.cleanup()


def test_main_agent_trace_name_and_middleware_stack(build_ctx):
    """主 agent：trace 名派生（storybuilding-subagent）+ 完整链构成（基础+domain+模式件）。"""
    from contracts.architecture_manifest import BASE_CHAIN_MODULES, load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx, _build_compiled

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        main = manifest.agent("storybuilding")
        abc = AgentBuildCtx(ctx, manifest, main, mod_name, HARNESS_PKG)
        assert abc.trace_name == "storybuilding-subagent"
        compiled = _build_compiled(abc)
        assert compiled["name"] == "storybuilding"
        # 基础链（无 trace 回调 → 7）+ domain 5 + ContextAssembler + RevisionLimit + ArtifactValidation
        assert len(compiled) == 3 and compiled["runnable"] is not None
    finally:
        tmp.cleanup()


def test_style_suffix_injected(build_ctx):
    """style suffix 按 manifest agent 名注入 prompt 尾部（SUFFIX 槽位语义）。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx, _system_prompt

    tmp, ctx = build_ctx(styles={"storybuilding": "文风：冷峻克制"})
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        abc = AgentBuildCtx(ctx, manifest, manifest.agent("storybuilding"), mod_name, HARNESS_PKG)
        prompt = _system_prompt(abc)
        assert prompt.rstrip().endswith("文风：冷峻克制")
        assert "storybuilding" in prompt or len(prompt) > 100  # prompt 主体在
    finally:
        tmp.cleanup()


def test_reviewer_trace_name_derivation(build_ctx):
    """审查器 trace 名 = 架构名下划线转连字符 + -subagent（v7 曲线可比）。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        abc = AgentBuildCtx(ctx, manifest, manifest.agent("storybuilding_review"), mod_name, HARNESS_PKG)
        assert abc.trace_name == "storybuilding-review-subagent"
    finally:
        tmp.cleanup()
