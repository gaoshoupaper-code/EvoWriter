"""创作委托文本范围测试（线上「还在做细纲」事故 + 「一直问第 0 步」事故的防回归）。

事故一：agent._build_user_prompt 残留 v8 多阶段流水线指令
（detail-outline → writing、chapter-XX/state_log 产物要求），
v9 切单故事专家后未清理——storybuilding 接到指令后自行写细纲与章节正文。

事故二（2026-10-19）：_build_user_prompt 硬编码「新书首轮（硬性）第 0 步
开场」+ 管线说明 + 产物要求，发版提示词删掉第 0 步后线上仍每轮问书名/类型
——代码里的行为指令作者改不到、发版盖不掉。

本测试锁定（注入禁令）：user prompt 只含信封+运行时数据+作者消息，
一切行为指令（开场话术/流程介绍/产物要求/问法模板）只住发版提示词。
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
    # FR-005/006：产物链路退休后委托文本不得再引用 outline/evaluation
    "outline.md",
    "evaluation",
)

# 行为指令特征（注入禁令，2026-10-19）：user prompt 出现任何一条都算回归。
# 这些内容的唯一居所是发版提示词（author_base.md 常驻骨架 + 段规程）。
_BEHAVIORAL_FRAGMENTS = (
    "新书首轮",
    "第 0 步",
    "硬性",
    "暂定名",
    "管线说明",
    "产物要求",
    "五段式访谈创作管线",
    "严禁自动连跑",
    "必停",
    "confirm_with_user",
    "落盘",
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


def test_user_prompt_carries_no_behavioral_instructions() -> None:
    """注入禁令：user prompt 零行为指令——行为纪律住发版提示词，不住代码。"""
    prompt = _build_prompt()
    for fragment in _BEHAVIORAL_FRAGMENTS:
        assert fragment not in prompt, f"user prompt 注入了行为指令: {fragment}"
    # 信封与作者消息仍在（这是 user prompt 的本职：运载数据，不运载指令）
    assert "作者消息：" in prompt and "写一部玄幻长篇" in prompt
    assert "回复请使用自然语言纯文本" in prompt


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


def test_first_build_injects_nothing_behavioral(tmp_path: Path) -> None:
    """空工作区（五段产物全空）也不得注入新书起步指令——开场话术由发版的
    题材段规程决定，executor 不替提示词做主（2026-10-19 事故防回归）。"""
    payload = ScreenplayGenerateRequest(prompt="写一部玄幻长篇", thread_id="t-test")
    prompt = _service()._build_user_prompt(payload, _thread_with_workspace(tmp_path))  # type: ignore[arg-type]

    for fragment in ("新书首轮", "第 0 步", "硬性", "暂定名", "这本书叫什么"):
        assert fragment not in prompt, f"空工作区注入了行为指令: {fragment}"
    assert "ask_user" not in prompt, "委托文本不得引用种子线工具"


def test_revision_after_outline_has_no_clarify_gate(tmp_path: Path) -> None:
    """主题.md 已存在（推进/修订轮）时同样零注入。"""
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
