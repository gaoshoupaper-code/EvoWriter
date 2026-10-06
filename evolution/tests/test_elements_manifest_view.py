"""elements_api 架构清单视图测试（REQ-20261006-130414 FR-006/007/009 / AC-002/009/010 前置）。

用临时 bare 仓库承载真实 harness 包历史（镜像生产 git_ops 链路：
work → bare push → read_dir 只读查询），验证清单视图与旧版本回退。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PKG = REPO_ROOT / "evolution" / "harnesses" / "repo"

pytestmark = pytest.mark.skipif(not HARNESS_PKG.is_dir(), reason="本地无 harness 仓库镜像")


@pytest.fixture()
def bare(tmp_path: Path, monkeypatch):
    """临时 bare 承载 harness 全历史 + settings 指向它。"""
    evo_root = REPO_ROOT / "evolution"
    if str(evo_root) not in sys.path:
        sys.path.insert(0, str(evo_root))
    from app.core.settings import settings

    bare_dir = tmp_path / "harness.git"
    subprocess.run(["git", "init", "--bare", str(bare_dir)], check=True,
                   capture_output=True, cwd=tmp_path)
    subprocess.run(
        ["git", "push", str(bare_dir), "HEAD:refs/heads/main"],
        check=True, capture_output=True, cwd=HARNESS_PKG,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
        text=True, cwd=HARNESS_PKG,
    ).stdout.strip()
    monkeypatch.setattr(settings, "harness_bare_repo", str(bare_dir))
    # 防套件污染（test_benchmark_* 会 importlib.reload(app.core.settings) 且不
    # 恢复——reload 后 app.core.settings.settings 换新实例，而 git_ops 模块级
    # from-import 仍钉住旧实例）。对 git_ops 实际持有的实例同步打补丁，
    # 两个绑定都指向临时 bare，恢复由 monkeypatch 保证。
    from app.core import git_ops as git_ops_mod

    monkeypatch.setattr(git_ops_mod.settings, "harness_bare_repo", str(bare_dir))
    yield head, bare_dir


def test_manifest_view_from_bare(bare):
    head, _ = bare
    from app.versioning.elements_api import (
        _build_manifest_elements_view,
        _manifest_at_commit,
    )

    manifest = _manifest_at_commit(head)
    assert manifest is not None
    view = _build_manifest_elements_view(0, head, manifest)

    assert view["layout"] == "manifest"
    agents = {a["name"]: a for a in view["agents"]}
    assert set(agents) == {"storybuilding", "storybuilding_review"}
    assert agents["storybuilding"]["role"] == "main"
    assert agents["storybuilding"]["display_name"] == "故事专家"
    assert agents["storybuilding_review"]["role"] == "sub"
    assert agents["storybuilding_review"]["runtime_name"] == "review"

    # 委托关系（AC-002/009 数据面）
    assert view["subagent_relations"] == [
        {"from": "storybuilding", "to": "storybuilding_review", "role": "故事审查"}
    ]

    # middleware 栈：数据源 = 清单（AC-002）
    story_classes = [m["class_name"] for m in agents["storybuilding"]["middlewares"]]
    review_classes = [m["class_name"] for m in agents["storybuilding_review"]["middlewares"]]
    assert "QuotaConvergenceMiddleware" in story_classes
    assert "ObjectContractGuardMiddleware" in story_classes
    assert "QuotaConvergenceMiddleware" not in review_classes
    assert "ErrorRecoveryMiddleware" in review_classes  # 基础链全 agent 统一
    assert "RevisionLimitMiddleware" in story_classes   # 审查闭环（有委托）
    assert "RevisionLimitMiddleware" not in review_classes

    # 未挂载报告（DEC-005 诚实呈现）：遗留闲置模块可见
    assert "file_write_guard" in view["unmounted"]["middleware"]
    assert "memory_recall_middleware" in view["unmounted"]["middleware"]
    assert "prompts/memory_extraction_guide.md" in view["unmounted"]["prompts"]

    # skills 归属：两技能挂故事专家
    skill_paths = [s["path"] for s in agents["storybuilding"]["skills"]]
    assert any("storybuilding-initial" in p for p in skill_paths)
    assert agents["storybuilding_review"]["skills"] == []


def test_legacy_commit_falls_back_to_v7(bare):
    """旧 commit（无清单）→ 清单视图跳过，v7 静态探测接管（FR-009）。"""
    head, _ = bare
    from app.versioning.elements_api import _agent_specs_for_commit, _manifest_at_commit

    assert _manifest_at_commit(head) is not None  # 新 HEAD 有清单
    # 上一版提交（归档遗留之前 = 清单引入之前）无清单
    old = subprocess.run(
        ["git", "rev-parse", "HEAD~2"], check=True, capture_output=True,
        text=True, cwd=HARNESS_PKG,
    ).stdout.strip()
    assert _manifest_at_commit(old) is None
    specs, layout = _agent_specs_for_commit(old)
    assert layout == "v7"
    assert [s[0] for s in specs] == ["storybuilding", "storybuilding_review"]


def test_manifest_stack_order_matches_declaration(bare):
    """domain middleware 显示顺序与清单声明一致（有序挂载的可视化）。"""
    head, _ = bare
    from app.versioning.elements_api import (
        _build_manifest_elements_view,
        _manifest_at_commit,
    )

    manifest = _manifest_at_commit(head)
    view = _build_manifest_elements_view(0, head, manifest)
    story = next(a for a in view["agents"] if a["name"] == "storybuilding")
    declared = manifest.agent("storybuilding").middleware
    class_by_module = {
        "storyline_single_line_limit": "StorylineSingleLineLimitMiddleware",
        "storyline_integrity": "StorylineIntegrityMiddleware",
        "storyline_contract_guard": "StorylineContractGuardMiddleware",
        "object_contract_guard": "ObjectContractGuardMiddleware",
        "quota_convergence": "QuotaConvergenceMiddleware",
    }
    story_classes = [m["class_name"] for m in story["middlewares"]]
    positions = [story_classes.index(class_by_module[m]) for m in declared]
    assert positions == sorted(positions)
