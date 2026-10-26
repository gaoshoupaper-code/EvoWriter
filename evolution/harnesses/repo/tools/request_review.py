"""request_review 工具 — 设计内停等点（REQ-20261026-191719 FR-003/009，TD-003）。

与 confirm_with_user（访谈拍板，auto 段会被系统代答）不同：本工具是
**设计内审查停等点**——无论段处于哪种创作模式，一律挂起等用户审查，
系统不得代答（DEC-010 只兜违纪的 choice 提问）。

三个审查口：
  - batch：正文批次停（写完 N 章，FR-003）——resume 文本携带「继续写 X 章」
    或「打回：<意见>」（FR-006）
  - style：文风卡停（章纲段自动流中生成文风卡后，FR-009）
  - stage：段产物齐的段间审查（FR-004；逐步模式沿用界面段间卡，本口
    供自动模式段内产物齐时主动交审）

机制与 confirm_with_user 相同：interrupt() 暂停整个图 → 执行端收尾
awaiting_input → 前端渲染审查卡 → 用户回复 Command(resume=...) →
resume 值作为本工具返回值回到模型。
"""
from __future__ import annotations

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

REQUEST_REVIEW_TOOL_DESCRIPTION = """提交产物给用户审查并暂停（设计内停等点，自动模式下也必须停）。

review_kind 按场景选：
- "batch"：正文写完一个批次（N 章）。question 里给出本批各章号与一行摘要。
  用户回复「继续写 X 章」→ 按新批次继续；回复「打回：<意见>」→ 只改意见涉及部分后重新交审。
- "style"：文风卡起草完成，交用户过目。用户回复确认 → 继续后续产物；回复意见 → 修订后重新交审。
- "stage"：本段产物已齐（定稿口径），交用户审查后进入段间确认。

除以上审查口，自动模式下严禁用 confirm_with_user 提问——遇到需要拍板的
小决策，自行决定并在进度行说明。"""


class ReviewOption(BaseModel):
    label: str = Field(min_length=1, max_length=12, description="选项标签，≤12 字")
    description: str = Field(default="", max_length=30, description="一句话解释，≤30 字")


class RequestReviewInput(BaseModel):
    review_kind: str = Field(
        description="审查口：batch（正文批次）| style（文风卡）| stage（段产物齐）"
    )
    question: str = Field(
        min_length=1,
        max_length=16000,
        description="交审内容（批次：各章号+一行摘要；文风：文风卡全文；段：产物清单+要点）",
    )
    options: list[ReviewOption] = Field(
        default_factory=list,
        max_length=6,
        description="可选引导项（如「继续下一批」「打回修改」），可为空",
    )


REVIEW_KINDS = ("batch", "style", "stage")


def build_review_payload(review_kind: str, question: str, options: list[ReviewOption]) -> dict[str, object]:
    """构造 review interrupt payload（纯逻辑，便于测试）。"""
    return {
        "kind": "review",
        "review_kind": review_kind,
        "source": "review",
        "question": question,
        "options": [opt.model_dump() for opt in options],
        "multi_select": False,
    }


def build_request_review_tool() -> StructuredTool:
    def request_review(review_kind: str, question: str, options: list[ReviewOption] | None = None):
        if review_kind not in REVIEW_KINDS:
            return f"review_kind 必须是 {'/'.join(REVIEW_KINDS)}，收到 {review_kind!r}；请重试。"
        payload = build_review_payload(review_kind, question, options or [])
        answer: str = interrupt(payload)
        return answer

    return StructuredTool.from_function(
        name="request_review",
        description=REQUEST_REVIEW_TOOL_DESCRIPTION,
        func=request_review,
        args_schema=RequestReviewInput,
        infer_schema=False,
    )


def build(abc):
    """架构清单挂载钩子（仅主控 author 声明挂载）。"""
    return build_request_review_tool()


__all__ = [
    "REQUEST_REVIEW_TOOL_DESCRIPTION",
    "REVIEW_KINDS",
    "RequestReviewInput",
    "ReviewOption",
    "build",
    "build_request_review_tool",
    "build_review_payload",
]
