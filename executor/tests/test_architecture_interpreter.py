"""架构清单解释器测试（REQ-20260906-130414 FR-001；五段制改造 REQ-20261018-115508）。

对真实 harness 包（evolution/harnesses/repo）做清单驱动装配，断言装配
产物形态：单 agent（author，无子代理）、按名装配、middleware 链、命名派生、
style suffix 注入（五段制承载段规程）、路径收权声明（disable_file_tools）。
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
    """清单装配产出非空编译图（probe 契约）；缺省 = 唯一 main（author）。"""
    from app.platform.agent.architecture import assemble_from_manifest

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        graph = assemble_from_manifest(ctx, package=mod_name)
        assert graph is not None
        named = assemble_from_manifest(ctx, package=mod_name, agent_name="author")
        assert named is not None
    finally:
        tmp.cleanup()


def test_manifest_shape_single_agent():
    """清单形态 = 单 agent（REQ-20261018-115508 DEC-001）：无委托、无子代理。"""
    from contracts.architecture_manifest import load_architecture_manifest

    manifest = load_architecture_manifest(HARNESS_PKG)
    assert [a.name for a in manifest.agents] == ["author"]
    main = manifest.agent("author")
    assert main.role == "main"
    assert main.delegates == []
    assert manifest.sub_agents == []
    # 阶段闸（L1 硬闸）是唯一 domain middleware
    assert main.middleware == ["stage_gate"]
    assert main.skills == []
    assert main.disable_file_tools is True  # 路径收权（FR-003）
    assert main.write_permissions == []
    assert set(main.tools) == {
        "read_artifact", "save_artifact", "update_foreshadow_table",
        "rewrite_rhythm_table", "append_chapter_deviation",
        "confirm_with_user",
    }  # handoff 已移除（REQ-20261019-232617 FR-004：跳段权收归界面）


def test_stage_prompt_files_complete():
    """段规程五件齐备（DEC-002：executor 按段经 styles 通道注入）。"""
    pkg = _load_pkg()
    mod = sys.modules[pkg]

    for stage, filename in mod.STAGE_PROMPT_FILES.items():
        path = HARNESS_PKG / "prompts" / filename
        assert path.is_file(), f"段规程缺失: {filename}"
        text = path.read_text(encoding="utf-8")
        assert len(text.strip()) > 200, f"段规程过短: {filename}"
    assert mod.stage_prompt("theme") is not None
    assert mod.stage_prompt("不存在的段") is None


def test_main_agent_trace_name_and_compiled_shape(build_ctx):
    """主 agent：trace 名派生（author-subagent）+ 编译图构成。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx, _build_compiled

    tmp, ctx = build_ctx()
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        main = manifest.agent("author")
        abc = AgentBuildCtx(ctx, manifest, main, mod_name, HARNESS_PKG)
        assert abc.trace_name == "author-subagent"
        compiled = _build_compiled(abc)
        assert compiled["name"] == "author"
        assert compiled["runnable"] is not None
    finally:
        tmp.cleanup()


def test_style_suffix_injected(build_ctx):
    """style suffix 按 manifest agent 名注入 prompt 尾部（五段制承载段规程）。"""
    from contracts.architecture_manifest import load_architecture_manifest
    from app.platform.agent.architecture import AgentBuildCtx, _system_prompt

    tmp, ctx = build_ctx(styles={"author": "# 当前段规程 · 测试段\n规则内容"})
    try:
        mod_name = _load_pkg()
        manifest = load_architecture_manifest(HARNESS_PKG)
        abc = AgentBuildCtx(ctx, manifest, manifest.agent("author"), mod_name, HARNESS_PKG)
        prompt = _system_prompt(abc)
        assert prompt.rstrip().endswith("规则内容")
        assert "陪练" in prompt  # 常驻骨架主体在
    finally:
        tmp.cleanup()


def test_single_agent_trace_name_derivation():
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
        manifest = load_architecture_manifest(HARNESS_PKG)
        abc = AgentBuildCtx(ctx, manifest, manifest.agent("author"), mod_name, HARNESS_PKG)
        assert abc.trace_name == "author-subagent"
    finally:
        Path(tmp.name)
        import os

        os.rmdir(tmp.name)
