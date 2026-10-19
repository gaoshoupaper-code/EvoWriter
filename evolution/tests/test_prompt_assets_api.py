"""prompt_assets_api + 要素视图 prompt_files 投影测试。

数据面全部合成（临时 git 仓库 / 临时工作区），不依赖 harnesses/repo
工作区形态（该工作区随架构演进变动，历史断言见 test_elements_manifest_view）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 纯函数：STAGE_PROMPT_FILES 提取 / 标题提取 ───────────────────


def test_extract_stage_prompt_files_literal_dict():
    from app.versioning.elements_api import _extract_stage_prompt_files

    src = (
        "STAGE_PROMPT_FILES = {\n"
        '    "theme": "stage_theme.md",\n'
        '    "text": "stage_zhengwen.md",\n'
        '    "theme2": "stage_theme.md",\n'
        "}\n"
    )
    # 去重 + 排序，保证展示顺序稳定
    assert _extract_stage_prompt_files(src) == ["stage_theme.md", "stage_zhengwen.md"]


def test_extract_stage_prompt_files_absent_or_broken():
    from app.versioning.elements_api import _extract_stage_prompt_files

    assert _extract_stage_prompt_files("X = 1\n") == []
    assert _extract_stage_prompt_files("def broken(:\n") == []
    assert _extract_stage_prompt_files("STAGE_PROMPT_FILES = [1, 2]\n") == []


def test_prompt_title_from_first_heading():
    from app.versioning.elements_api import _prompt_title

    body = "# 当前段规程 · 题材（theme）\n\n正文……"
    assert _prompt_title(body, "prompts/stage_theme.md") == "当前段规程 · 题材（theme）"
    # 首个非空行不是标题 → 回退文件名
    assert _prompt_title("直接正文", "prompts/x.md") == "x.md"


# ── 合成仓库：清单视图 prompt_files 投影（基础 + 段规程挂 main）────


@pytest.fixture()
def synthetic_bare(tmp_path: Path, monkeypatch):
    """合成 harness 仓库：单 main + 单 sub，main 声明 2 个段规程文件。"""
    evo_root = REPO_ROOT / "evolution"
    if str(evo_root) not in sys.path:
        sys.path.insert(0, str(evo_root))
    from app.core.settings import settings
    from app.core import git_ops as git_ops_mod

    work = tmp_path / "pkg"
    (work / "prompts").mkdir(parents=True)
    manifest = {
        "schema_version": "writer.architecture/1",
        "agents": [
            {
                "name": "author", "role": "main",
                "prompt": "prompts/author_base.md",
            },
            {
                "name": "sub", "role": "sub", "delegated_by": "author",
                "prompt": "prompts/sub.md",
            },
        ],
    }
    (work / "architecture.json").write_text(json.dumps(manifest), encoding="utf-8")
    (work / "__init__.py").write_text(
        'STAGE_PROMPT_FILES = {"text": "stage_zhengwen.md", "theme": "stage_theme.md"}\n',
        encoding="utf-8",
    )
    (work / "prompts" / "author_base.md").write_text(
        "# 常驻骨架标题\n骨架正文\n", encoding="utf-8")
    (work / "prompts" / "sub.md").write_text("# 子代理\n", encoding="utf-8")
    (work / "prompts" / "stage_theme.md").write_text(
        "# 当前段规程 · 题材（theme）\n题材正文\n", encoding="utf-8")
    (work / "prompts" / "stage_zhengwen.md").write_text(
        "# 当前段规程 · 正文（text）\n正文规程\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], check=True, capture_output=True, cwd=work)

    git("init", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-m", "init")
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True, cwd=work).stdout.strip()

    bare = tmp_path / "harness.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True,
                   capture_output=True)
    # 工作区 origin 指向临时 bare（commit_and_push 经 origin 推送）
    git("remote", "add", "origin", str(bare))
    subprocess.run(["git", "push", str(bare), "HEAD:refs/heads/main"], check=True,
                   capture_output=True, cwd=work)

    monkeypatch.setattr(settings, "harness_bare_repo", str(bare))
    monkeypatch.setattr(git_ops_mod.settings, "harness_bare_repo", str(bare))
    monkeypatch.setattr(settings, "harness_work_dir", str(work))
    monkeypatch.setattr(git_ops_mod.settings, "harness_work_dir", str(work))
    return head


def test_manifest_view_prompt_files_base_plus_stages(synthetic_bare):
    from app.versioning.elements_api import (
        _build_manifest_elements_view,
        _manifest_at_commit,
    )

    head = synthetic_bare
    manifest = _manifest_at_commit(head)
    assert manifest is not None
    view = _build_manifest_elements_view(0, head, manifest)
    agents = {a["name"]: a for a in view["agents"]}

    # main：基础文件 + 2 个段规程（声明序排序），kind 区分
    author_files = agents["author"]["prompt_files"]
    assert [(f["kind"], f["path"]) for f in author_files] == [
        ("base", "prompts/author_base.md"),
        ("stage", "prompts/stage_theme.md"),
        ("stage", "prompts/stage_zhengwen.md"),
    ]
    assert author_files[0]["title"] == "常驻骨架标题"
    assert author_files[1]["title"] == "当前段规程 · 题材（theme）"
    assert "题材正文" in author_files[1]["body"]

    # sub：只有自己的基础文件，不挂段规程
    assert [f["kind"] for f in agents["sub"]["prompt_files"]] == ["base"]


# ── 工作区草稿端点 ───────────────────────────────────────────────


@pytest.fixture()
def draft_workdir(synthetic_bare):
    """复用合成仓库的工作区（已 git init + committed，git status 可用）。"""
    from app.core.git_ops import work_dir

    return Path(work_dir())


def test_list_and_save_draft(draft_workdir):
    from app.versioning.prompt_assets_api import (
        list_workdir_prompts,
        save_workdir_prompt,
    )
    from app.versioning.prompt_assets_api import PromptSaveRequest

    view = list_workdir_prompts()
    paths = [f["path"] for f in view["files"]]
    assert "prompts/author_base.md" in paths
    assert "prompts/stage_theme.md" in paths
    assert view["dirty"] is False

    result = save_workdir_prompt(PromptSaveRequest(
        path="prompts/author_base.md", content="# 改后\n新内容\n"))
    assert result["ok"] is True
    assert result["dirty"] is True
    assert (draft_workdir / "prompts" / "author_base.md").read_text(
        encoding="utf-8") == "# 改后\n新内容\n"
    assert list_workdir_prompts()["dirty"] is True


def test_save_draft_rejects_bad_paths(draft_workdir):
    from fastapi import HTTPException

    from app.versioning.prompt_assets_api import PromptSaveRequest, save_workdir_prompt

    for bad in (
        "prompts/../architecture.json",      # 穿越
        "prompts/nested/author_base.md",     # 子目录
        "middleware/stage_gate.py",          # 其他目录
        "prompts/not_exist.md",              # 不存在（不新建）
        "prompts/no_ext",                    # 非 .md
    ):
        with pytest.raises(HTTPException) as exc:
            save_workdir_prompt(PromptSaveRequest(path=bad, content="x"))
        assert exc.value.status_code == 409, bad


def test_release_requires_changes(draft_workdir):
    from fastapi import HTTPException

    from app.versioning.prompt_assets_api import (
        PromptReleaseRequest,
        release_prompt_draft,
    )

    with pytest.raises(HTTPException) as exc:
        release_prompt_draft(PromptReleaseRequest(note=""))
    assert exc.value.status_code == 409


def test_release_commits_and_promotes(draft_workdir):
    from app.versioning import prompt_assets_api
    from app.versioning.prompt_assets_api import (
        PromptReleaseRequest,
        release_prompt_draft,
    )

    (draft_workdir / "prompts" / "author_base.md").write_text(
        "# 再改\n", encoding="utf-8")

    class FakePromoted:
        version = 99
        commit = "abc1234"

    with patch.object(prompt_assets_api, "promote_release", return_value=FakePromoted()) as promoted:
        result = release_prompt_draft(PromptReleaseRequest(note="审核修订"))
    assert result == {"version": 99, "commit": "abc1234"}
    assert promoted.call_args.kwargs["version_note"] == "审核修订"
    # 发版后工作区干净（commit_and_push 已提交全部改动）
    from app.core.git_ops import has_changes
    assert has_changes() is False


def test_release_rejected_while_evolve_round_running(draft_workdir):
    from fastapi import HTTPException

    from app.evolve import api as evolve_api
    from app.versioning.prompt_assets_api import (
        PromptSaveRequest,
        PromptReleaseRequest,
        release_prompt_draft,
        save_workdir_prompt,
    )

    class RunningTask:
        def done(self):
            return False

    (draft_workdir / "prompts" / "author_base.md").write_text("# x\n", encoding="utf-8")
    with patch.object(evolve_api, "_running_tasks", {"s1": RunningTask()}):
        with pytest.raises(HTTPException) as exc:
            save_workdir_prompt(PromptSaveRequest(
                path="prompts/author_base.md", content="y"))
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException):
            release_prompt_draft(PromptReleaseRequest(note=""))
