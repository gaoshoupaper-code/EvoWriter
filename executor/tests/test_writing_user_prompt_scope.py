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
    # 五段制（REQ-20261018-115508 FR-001）：单 agent 技能切换 + 五段 + 语义工具 + 必停
    assert "五段式访谈创作管线" in prompt
    for stage in ("题材", "总纲", "卷纲", "章纲", "正文"):
        assert stage in prompt
    assert "语义" in prompt and "confirm_with_user" in prompt
    assert "必停" in prompt and "严禁自动连跑" in prompt


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
    """AC-001（REQ-20261018-115508）：五段产物全空时，委托文本注入新书首轮
    指令——题材段第 0 步摸底（流程介绍 + 书名/类型两问）。
    """
    payload = ScreenplayGenerateRequest(prompt="写一部玄幻长篇", thread_id="t-test")
    prompt = _service()._build_user_prompt(payload, _thread_with_workspace(tmp_path))  # type: ignore[arg-type]

    for fragment in (
        "新书首轮", "第 0 步", "暂定名", "严禁自动连跑",
    ):
        assert fragment in prompt, f"首发委托文本缺新书起步指令: {fragment}"
    assert "ask_user" not in prompt, "委托文本不得引用种子线工具"


def test_revision_after_outline_has_no_clarify_gate(tmp_path: Path) -> None:
    """主题.md 已存在（推进/修订轮）时不得注入新书首轮指令，避免干扰访谈。"""
    (tmp_path / "主题.md").write_text("# 主题 · 书\n> 状态：第 1 步\n", encoding="utf-8")
    payload = ScreenplayGenerateRequest(prompt="主角动机再改改", thread_id="t-test")
    prompt = _service()._build_user_prompt(payload, _thread_with_workspace(tmp_path))  # type: ignore[arg-type]

    assert "新书首轮" not in prompt
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
    """收尾锚点（REQ-20261018-115508）：five 工作区访谈轮可不落产物（回复即
    交付，不再强制产物锚）；legacy 锚 storyline.md / outline/ 产物（旧口径）。"""
    service = MetaAgentService.__new__(MetaAgentService)
    payload = ScreenplayGenerateRequest(prompt="x", thread_id="t-test")

    # 全新空工作区（five）→ 访谈问答轮合法，收尾成功
    response0 = service._response_from_workspace_artifacts(payload, "done", _fake_thread(tmp_path))
    assert response0.mode == "live"

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

    # legacy（旧七步）：outline/ 只有状态文件不算产物 → 失败；有 .md → 成功
    ws2 = tmp_path / "ws-pipeline"
    (ws2 / "outline").mkdir(parents=True)
    (ws2 / "outline" / "_pipeline.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="outline"):
        service._response_from_workspace_artifacts(payload, "done", _fake_thread(ws2))
    (ws2 / "outline" / "06-章纲").mkdir()
    (ws2 / "outline" / "06-章纲" / "第001章.md").write_text("# 章", encoding="utf-8")
    response2 = service._response_from_workspace_artifacts(payload, "done", _fake_thread(ws2))
    assert response2.mode == "live"
