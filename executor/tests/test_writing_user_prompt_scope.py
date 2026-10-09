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
    assert "单故事专家" in prompt
    assert "不要创建 detail/" in prompt
    assert "storybuilding-initial" in prompt
    assert "storybuilding-expand" in prompt


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
    """FR-003（REQ-20260909-224433）：storyline.md 不存在时，委托文本注入澄清前置指令。

    措辞工具无关——澄清/拍板的具体工具以 harness 系统提示词为准
    （生产为 confirm_with_user，种子线为 ask_user），委托文本不点名工具。
    FR-001（REQ-20261010-000638）：澄清必含「设计原则」候选问（DEC-016）。
    """
    payload = ScreenplayGenerateRequest(prompt="写一部玄幻长篇", thread_id="t-test")
    prompt = _service()._build_user_prompt(payload, _thread_with_workspace(tmp_path))  # type: ignore[arg-type]

    for fragment in (
        "需求澄清", "拍板", "不用问了，直接写", "demand.md",
        # FR-001（REQ-20261010-000638）设计原则候选问三要素（DEC-016）
        "2~3 个候选设计原则", "各配一句展开说明", "挑选或修改", "自行拟定一条并在澄清汇总中明示",
    ):
        assert fragment in prompt, f"首发委托文本缺澄清指令: {fragment}"
    assert "ask_user" not in prompt, "委托文本不得点名具体工具（与 harness 版本解耦）"


def test_revision_after_outline_has_no_clarify_gate(tmp_path: Path) -> None:
    """FR-003：storyline.md 已存在（修订/增量）时不得注入澄清指令，避免干扰迭代。"""
    (tmp_path / "storyline.md").write_text("# 故事核心\n\n- **Logline**：测试", encoding="utf-8")
    payload = ScreenplayGenerateRequest(prompt="主角动机再改改", thread_id="t-test")
    prompt = _service()._build_user_prompt(payload, _thread_with_workspace(tmp_path))  # type: ignore[arg-type]

    assert "需求澄清" not in prompt
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
    """FR-007：收尾只检查 storyline.md 存在且非空；outline/evaluation 缺失不再是失败条件。"""
    service = MetaAgentService.__new__(MetaAgentService)
    payload = ScreenplayGenerateRequest(prompt="x", thread_id="t-test")

    # storyline.md 缺失 → 收尾失败，错误指向故事线
    with pytest.raises(FileNotFoundError, match="storyline"):
        service._response_from_workspace_artifacts(payload, "done", _fake_thread(tmp_path))

    # storyline.md 为空 → 收尾失败
    (tmp_path / "storyline.md").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="storyline"):
        service._response_from_workspace_artifacts(payload, "done", _fake_thread(tmp_path))

    # storyline.md 有内容且无 outline.md / evaluation.md → 收尾成功
    (tmp_path / "storyline.md").write_text("# 故事核心\n\n- **Logline**：测试", encoding="utf-8")
    response = service._response_from_workspace_artifacts(payload, "done", _fake_thread(tmp_path))
    assert response.mode == "live"
    assert not hasattr(response, "markdown") or not response.markdown
    assert not hasattr(response, "evaluation_markdown") or not response.evaluation_markdown
