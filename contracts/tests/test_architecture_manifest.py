"""架构清单契约测试（REQ-20261006-130414 FR-002 / AC-003/004 对应单测）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.architecture_manifest import (
    ARCHITECTURE_SCHEMA,
    MAX_SUBAGENTS,
    ManifestError,
    load_architecture_manifest,
    validate_architecture_manifest,
)

# 带 build() 的 middleware 样例（合格）
_MW_WITH_BUILD = '"""mw"""\nfrom typing import Any\n\n\ndef build(ctx: Any):\n    return object()\n'
# 缺 build() 的 middleware 样例（不合格）
_MW_WITHOUT_BUILD = '"""mw"""\n\nVALUE = 1\n'


def _make_pkg(tmp_path: Path) -> Path:
    """构造最小合法 harness 包夹具（故事专家 + 审查器双 agent 形态）。"""
    pkg = tmp_path / "pkg"
    (pkg / "middleware").mkdir(parents=True)
    (pkg / "prompts").mkdir()
    (pkg / "skills" / "skill-a").mkdir(parents=True)
    (pkg / "tools").mkdir()
    (pkg / "middleware" / "mw_a.py").write_text(_MW_WITH_BUILD, encoding="utf-8")
    (pkg / "prompts" / "main.md").write_text("MAIN", encoding="utf-8")
    (pkg / "prompts" / "review.md").write_text("REVIEW", encoding="utf-8")
    (pkg / "skills" / "skill-a" / "SKILL.md").write_text("# A", encoding="utf-8")
    manifest = {
        "schema_version": ARCHITECTURE_SCHEMA,
        "agents": [
            {
                "name": "storybuilding",
                "role": "main",
                "display_name": "故事专家",
                "prompt": "prompts/main.md",
                "middleware": ["mw_a"],
                "skills": ["skill-a"],
                "delegates": ["storybuilding_review"],
            },
            {
                "name": "storybuilding_review",
                "role": "sub",
                "delegated_by": "storybuilding",
                "prompt": "prompts/review.md",
                "middleware": [],
            },
        ],
    }
    (pkg / "architecture.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return pkg


def test_load_missing_manifest(tmp_path):
    with pytest.raises(ManifestError, match="不存在"):
        load_architecture_manifest(tmp_path)


def test_load_wrong_schema(tmp_path):
    pkg = _make_pkg(tmp_path)
    (pkg / "architecture.json").write_text(
        json.dumps({"schema_version": "other/9", "agents": []}), encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="schema 不匹配"):
        load_architecture_manifest(pkg)


def test_valid_manifest_passes(tmp_path):
    pkg = _make_pkg(tmp_path)
    manifest = load_architecture_manifest(pkg)
    result = validate_architecture_manifest(pkg, manifest)
    assert result.ok, result.errors
    assert result.unmounted_middleware == []
    assert result.unmounted_prompts == []


def test_dangling_prompt_reference(tmp_path):
    pkg = _make_pkg(tmp_path)
    manifest = load_architecture_manifest(pkg)
    manifest.agents[0].prompt = "prompts/missing.md"
    result = validate_architecture_manifest(pkg, manifest)
    assert any("prompt 文件不存在" in e for e in result.errors)


def test_middleware_without_build_hook(tmp_path):
    pkg = _make_pkg(tmp_path)
    (pkg / "middleware" / "mw_b.py").write_text(_MW_WITHOUT_BUILD, encoding="utf-8")
    manifest = load_architecture_manifest(pkg)
    manifest.agents[0].middleware = ["mw_b"]
    result = validate_architecture_manifest(pkg, manifest)
    assert any("缺少顶层 build" in e for e in result.errors)


def test_delegation_bidirectional_mismatch(tmp_path):
    pkg = _make_pkg(tmp_path)
    manifest = load_architecture_manifest(pkg)
    manifest.agents[1].delegated_by = "someone_else"
    result = validate_architecture_manifest(pkg, manifest)
    assert any("双向不一致" in e or "指向不存在" in e for e in result.errors)


def test_delegation_cycle_detected(tmp_path):
    """环藏在中位委托边（A=[D,B], B=[E,A]）——只沿首边走会漏，全图 DFS 必须抓住。"""
    pkg = _make_pkg(tmp_path)
    (pkg / "middleware" / "mw_d.py").write_text(_MW_WITH_BUILD, encoding="utf-8")
    manifest = load_architecture_manifest(pkg)
    a = manifest.agents[0]
    a.delegates = ["agent_d", "storybuilding_review"]
    manifest.agents[1].delegates = ["agent_e", a.name]
    from contracts.architecture_manifest import AgentSpec

    manifest.agents.append(AgentSpec(name="agent_d", role="sub", delegated_by=a.name,
                                     prompt="prompts/main.md"))
    manifest.agents.append(AgentSpec(name="agent_e", role="sub",
                                     delegated_by="storybuilding_review",
                                     prompt="prompts/main.md"))
    result = validate_architecture_manifest(pkg, manifest)
    assert any("委托环" in e for e in result.errors), result.errors


def test_subagent_count_limit(tmp_path):
    pkg = _make_pkg(tmp_path)
    manifest = load_architecture_manifest(pkg)
    from contracts.architecture_manifest import AgentSpec

    for i in range(MAX_SUBAGENTS):
        manifest.agents.append(AgentSpec(
            name=f"extra_{i}", role="sub", delegated_by="storybuilding",
            prompt="prompts/main.md",
        ))
    result = validate_architecture_manifest(pkg, manifest)
    assert any("子代理数量超上限" in e for e in result.errors)


def test_unmounted_reporting(tmp_path):
    pkg = _make_pkg(tmp_path)
    (pkg / "middleware" / "orphan_mw.py").write_text(_MW_WITH_BUILD, encoding="utf-8")
    (pkg / "prompts" / "orphan.md").write_text("x", encoding="utf-8")
    (pkg / "skills" / "orphan-skill").mkdir()
    (pkg / "subagents").mkdir()
    (pkg / "subagents" / "orphan_agent.py").write_text("X = 1", encoding="utf-8")
    manifest = load_architecture_manifest(pkg)
    result = validate_architecture_manifest(pkg, manifest)
    assert result.ok  # 未挂载不阻断
    assert result.unmounted_middleware == ["orphan_mw"]
    assert result.unmounted_prompts == ["prompts/orphan.md"]
    assert result.unmounted_skills == ["orphan-skill"]
    assert result.unmounted_subagents == ["subagents/orphan_agent.py"]


def test_missing_main_agent(tmp_path):
    pkg = _make_pkg(tmp_path)
    manifest = load_architecture_manifest(pkg)
    manifest.agents[0].role = "sub"
    manifest.agents[0].delegated_by = "storybuilding_review"
    result = validate_architecture_manifest(pkg, manifest)
    assert any("恰好声明一个 role=main" in e for e in result.errors)
