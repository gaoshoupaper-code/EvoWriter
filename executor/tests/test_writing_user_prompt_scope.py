"""创作委托文本范围测试（线上「还在做细纲」事故的防回归）。

事故：agent._build_user_prompt 残留 v8 多阶段流水线指令
（detail-outline → writing、chapter-XX/state_log 产物要求），
v9 切单故事专家后未清理——storybuilding 接到指令后自行写细纲与章节正文。
本测试锁定：委托文本只描述大纲阶段，不出现任何细纲/正文/章节指令。

REQ-20260930-163019 扩展：outline.md / evaluation.md 链路退休后，
委托文本与收尾检查同样不得再引用这两个产物；收尾唯一锚点是 storyline.md
（FR-005/006/007）——旧代码强制检查两文件曾致全新任务收尾必炸。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domains.writing.agent import MetaAgentService
from app.schemas.screenplay import ScreenplayGenerateRequest

# v8 流水线指令的残留特征——任何一个出现都算回归
# （用肯定式指令片段，避免误伤「不要创建 detail/、chapter/ 目录」这类否定句）
_FORBIDDEN_FRAGMENTS = (
    "detail-outline",
    "detail/overview",
    "writing 阶段",
    "细纲和正文",
    "chapter-XX",
    "写入 chapter/",
    "写入 state_log",
    "skeleton skill",
    "expand skill",
    # FR-005/006：产物链路退休后委托文本不得再要求维护 outline/evaluation
    "outline.md",
    "evaluation",
)


def _build_prompt() -> str:
    service = MetaAgentService.__new__(MetaAgentService)  # 只测纯文本构造
    payload = ScreenplayGenerateRequest(prompt="写一部玄幻长篇", thread_id="t-test")
    thread = type(
        "T",
        (),
        {"thread_id": "t-test", "session_name": "s"},
    )()
    return service._build_user_prompt(payload, thread)  # type: ignore[arg-type]


def test_user_prompt_has_no_pipeline_remnants() -> None:
    prompt = _build_prompt()
    for fragment in _FORBIDDEN_FRAGMENTS:
        assert fragment not in prompt, f"委托文本残留 v8 流水线指令: {fragment}"


def test_user_prompt_states_single_expert_scope() -> None:
    prompt = _build_prompt()
    # 双 agent 管线（REQ-20261011-142529 FR-002）：两泳道 + 七步 + 不写正文
    assert "双 agent 工业七步法管线" in prompt
    assert "storydesign" in prompt
    assert "storystructure" in prompt
    assert "不写正文" in prompt


def _thread_with_workspace(tmp_path: Path):
    return type(
        "T",
        (),
        {
            "thread_id": "t-test",
            "session_name": "s",
            "workspace_path": str(tmp_path),
        },
    )()


def _service() -> MetaAgentService:
    return MetaAgentService.__new__(MetaAgentService)


def test_first_build_injects_clarify_gate(tmp_path: Path) -> None:
    """FR-003（REQ-20261011-142529）：无任何大纲产物时，委托文本注入首次构建
    指令——从第 0 步赛道确认开始、单元拍板后落盘、严禁自动连跑。
    """
    payload = ScreenplayGenerateRequest(prompt="写一部玄幻长篇", thread_id="t-test")
    prompt = _service()._build_user_prompt(payload, _thread_with_workspace(tmp_path))  # type: ignore[arg-type]

    for fragment in (
        "首次构建", "赛道确认", "拍板", "严禁自动连跑",
    ):
        assert fragment in prompt, f"首发委托文本缺管线起步指令: {fragment}"
    assert "ask_user" not in prompt, "委托文本不得引用种子线工具"


def test_revision_after_outline_has_no_clarify_gate(tmp_path: Path) -> None:
    """FR-003：storyline.md 已存在（修订/增量）时不得注入首次构建指令，避免干扰迭代。"""
    (tmp_path / "storyline.md").write_text("# 故事核心\n\n- **Logline**：测试", encoding="utf-8")
    payload = ScreenplayGenerateRequest(prompt="主角动机再改改", thread_id="t-test")
    prompt = _service()._build_user_prompt(payload, _thread_with_workspace(tmp_path))  # type: ignore[arg-type]

    assert "首次构建" not in prompt
    assert "ask_user" not in prompt


def _fake_thread(tmp_path: Path):
    return type(
        "T",
        (),
        {
            "thread_id": "t-test",
            "workspace_id": "w-test",
            "session_name": "s",
            "workspace_path": str(tmp_path),
        },
    )()


def test_finalization_anchors_on_storyline_only(tmp_path: Path) -> None:
    """FR-007 收尾锚点（REQ-20261011-142529 双锚点）：legacy 锚 storyline.md，
    管线工作区锚 outline/ 产物——锚点跟产物走，否则管线收尾必炸误标 failed。"""
    service = MetaAgentService.__new__(MetaAgentService)
    payload = ScreenplayGenerateRequest(prompt="x", thread_id="t-test")

    # 全新空工作区（pipeline 口径）→ 收尾失败，错误指向 outline 产物
    with pytest.raises(FileNotFoundError, match="outline"):
        service._response_from_workspace_artifacts(payload, "done", _fake_thread(tmp_path))

    # legacy：storyline.md 存在但为空 → 收尾失败
    (tmp_path / "storyline.md").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="storyline"):
        service._response_from_workspace_artifacts(payload, "done", _fake_thread(tmp_path))

    # legacy：storyline.md 有内容 → 收尾成功
    (tmp_path / "storyline.md").write_text("# 故事核心\n\n- **Logline**：测试", encoding="utf-8")
    response = service._response_from_workspace_artifacts(payload, "done", _fake_thread(tmp_path))
    assert response.mode == "live"
    assert not hasattr(response, "markdown") or not response.markdown
    assert not hasattr(response, "evaluation_markdown") or not response.evaluation_markdown

    # pipeline：outline/ 有产物（含子目录）→ 收尾成功；_pipeline.json 不算产物
    ws2 = tmp_path / "ws-pipeline"
    (ws2 / "outline").mkdir(parents=True)
    (ws2 / "outline" / "_pipeline.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="outline"):
        service._response_from_workspace_artifacts(payload, "done", _fake_thread(ws2))
    (ws2 / "outline" / "06-章纲").mkdir()
    (ws2 / "outline" / "06-章纲" / "第001章.md").write_text("# 章", encoding="utf-8")
    response2 = service._response_from_workspace_artifacts(payload, "done", _fake_thread(ws2))
    assert response2.mode == "live"
