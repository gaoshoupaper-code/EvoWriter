"""自动正文按本批待写章数分配有限审稿额度。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from app.platform.agent.loader import load_package
from contracts.writing_artifacts import load_writing_state, save_writing_state

load_package(Path(__file__).resolve().parents[2] / "evolution/harnesses/repo")
from harness_current.middleware.revision_limit import build


def _build_budget(
    workspace: Path, *, stock: int, batch: int = 5, mode: str = "auto",
    stage: str = "text", per_chapter: int = 1,
):
    state = load_writing_state(workspace)
    state.confirmed_stage = stage
    state.stage_modes[stage] = mode
    state.text_batch_size = batch
    save_writing_state(workspace, state)
    outlines, texts = workspace / "章纲", workspace / "正文"
    outlines.mkdir()
    texts.mkdir()
    # 第一章已写完，不应再次占用本批审稿额度。
    (texts / "第001章.md").write_text("# 正文\n", encoding="utf-8")
    for chapter in range(1, stock + 2):
        (outlines / f"第{chapter:03d}章.md").write_text("# 章纲\n", encoding="utf-8")
    return build(SimpleNamespace(
        workspace_path=workspace, agent=SimpleNamespace(name="author", max_revisions=per_chapter),
    ))


def test_build_gives_non_review_delegates_their_own_manifest_cap(tmp_path: Path) -> None:
    """REQ-20261031-142504：非 review 委托目标的上限取子代理清单自己的 max_revisions（volume_qc=2）。"""
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "text"
    state.stage_modes["text"] = "auto"
    save_writing_state(tmp_path, state)
    sub_caps = {"volume_qc": 2}

    middleware = build(SimpleNamespace(
        workspace_path=tmp_path,
        agent=SimpleNamespace(
            name="author", max_revisions=1, delegates=["review", "volume_qc"],
        ),
        manifest=SimpleNamespace(agent=lambda n: SimpleNamespace(max_revisions=sub_caps[n])),
    ))

    assert middleware.limits["volume_qc"] == 2
    assert middleware.limits["review"] == middleware.max_revisions


def test_build_derives_limits_from_real_harness_manifest(tmp_path: Path) -> None:
    """真实清单派生（装配输入级）：author 的委托目标从 harness 包 architecture.json
    读出 delegates 与子代理 max_revisions——review=1（清单默认）、volume_qc=2（质检循环上限）。"""
    from contracts.architecture_manifest import load_architecture_manifest

    harness_dir = Path(__file__).resolve().parents[2] / "evolution" / "harnesses" / "repo"
    manifest = load_architecture_manifest(harness_dir)
    state = load_writing_state(tmp_path)
    state.confirmed_stage = "volume"
    save_writing_state(tmp_path, state)

    middleware = build(SimpleNamespace(
        workspace_path=tmp_path,
        agent=manifest.agent("author"),
        manifest=manifest,
    ))

    assert middleware.limits == {"review": 1, "volume_qc": 2}


@pytest.mark.parametrize("stock,batch,expected", [(3, 5, 3), (8, 5, 5), (0, 5, 1)])
def test_auto_text_budget_uses_only_this_batch(
    tmp_path: Path, stock: int, batch: int, expected: int,
) -> None:
    middleware = _build_budget(tmp_path, stock=stock, batch=batch)
    assert middleware.max_revisions == expected


def test_auto_text_budget_preserves_manifest_multiplier(tmp_path: Path) -> None:
    assert _build_budget(tmp_path, stock=3, per_chapter=2).max_revisions == 6


@pytest.mark.parametrize("stage,mode", [("text", "interactive"), ("chapter", "auto"), ("volume", "auto")])
def test_other_modes_and_stages_keep_original_budget(tmp_path: Path, stage: str, mode: str) -> None:
    assert _build_budget(tmp_path, stock=3, stage=stage, mode=mode).max_revisions == 1


@pytest.mark.parametrize("async_call", [False, True])
def test_reviews_beyond_actual_batch_are_still_blocked(tmp_path: Path, async_call: bool) -> None:
    middleware = _build_budget(tmp_path, stock=3)
    calls = []

    def handler(request):
        calls.append(request.tool_call["id"])
        return "审稿完成"

    async def ahandler(request):
        return handler(request)

    async def scenario():
        for number in range(1, 5):
            request = SimpleNamespace(tool_call={
                "name": "task", "id": f"review-{number}",
                "args": {"subagent_type": "review", "description": "审当前章"},
            })
            result = (
                await middleware.awrap_tool_call(request, ahandler) if async_call
                else middleware.wrap_tool_call(request, handler)
            )
            if number <= 3:
                assert result == "审稿完成"
            else:
                assert isinstance(result, ToolMessage)
                assert "审查上限（3 次" in result.content

    asyncio.run(scenario())
    assert calls == ["review-1", "review-2", "review-3"]
