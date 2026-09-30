from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domains.writing.styling.schemas import StyleSummary


class WorkspaceCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    domain: str = Field(default="writing")


class WorkspaceSummary(BaseModel):
    workspace_id: str
    title: str
    domain: str = "writing"
    workspace_path: str
    created_at: str
    updated_at: str
    session_count: int = 0
    active_style_id: str | None = None


class ThreadCreateRequest(BaseModel):
    workspace_id: str
    session_name: str | None = None


class ThreadUpdateRequest(BaseModel):
    session_name: str = Field(min_length=1)


class ThreadSummary(BaseModel):
    thread_id: str
    workspace_id: str
    session_name: str
    workspace_path: str
    created_at: str
    updated_at: str
    # 归属用户 ID（Phase 2 D2/D20）：trace 链路需要 user_id 做按用户隔离。
    # 可选（T18）：现有构造点和测试不传此字段，默认 "unknown"。
    # thread_store._to_thread_summary 已有 owner_id 在手，会注入真实值。
    user_id: str = "unknown"


class StorylineEntry(BaseModel):
    filename: str
    title: str
    markdown: str


class PanoramaEvent(BaseModel):
    """大纲全景表事件行（FR-003/DEC-005：时序/所属线/事件/类型/角色/地点/描述）。

    t = 原始时序号文本（T1 / T12.5，保留插入语义——DEC-010）；
    storylines = 全部参与线名（多条 = 交汇事件，DEC-010）。
    """

    t: str = ""
    name: str = ""
    type: str = ""
    storylines: list[str] = Field(default_factory=list)
    characters: str = ""
    location: str = ""
    desc: str = ""


class WorkspaceStorylineContent(BaseModel):
    """故事线产物内容（REQ-20260930-002231 FR-014：双格式演进）。

    format="v2"：storyline.md 单文件（故事核心 + 线区块）——markdown 承载全文，
    entries 按线区块拆分（title=线名），index_markdown 同 markdown（兼容旧前端字段）。
    format="legacy"：旧多文件格式（storyline/ 目录）——维持旧读取行为（FR-013 降级）。
    panorama：跨线全景事件列表（FR-003/REQ-20260930-163019，仅 v2 有值——
    legacy 旧格式不解析，前端降级为按线分区块视图）。
    """

    workspace_id: str
    format: str = "v2"
    markdown: str = ""
    index_markdown: str = ""
    entries: list[StorylineEntry] = Field(default_factory=list)
    file_count: int = 0
    panorama: list[PanoramaEvent] = Field(default_factory=list)


class WorkspaceWorldviewContent(BaseModel):
    workspace_id: str
    markdown: str


class CharacterMarkdownFile(BaseModel):
    filename: str
    name: str
    markdown: str


class WorkspaceCharacterContent(BaseModel):
    workspace_id: str
    characters: list[CharacterMarkdownFile]


class ScreenplayGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: str | None = None
    content: str | None = None
    text: str | None = None
    title: str | None = None
    genre: str | None = None
    premise: str | None = None
    tone: str | None = None
    audience: str | None = None
    thread_id: str
    # 表单直入（FR-002/DEC-009）：desktop 表单模板化渲染的 demand.md 全文。
    # 有值时执行端写入 workspace/demand.md，故事专家据此生成大纲三件套。
    demand_md: str | None = None
    # resume 回传：复用活跃 trace，把一次提问的多次 HITL 缝合成同一条 trace（点3）
    trace_id: str | None = None

    def primary_text(self) -> str:
        return self.prompt or self.content or self.text or self.premise or ""

    def fallback_title(self) -> str:
        return self.title or "未命名大纲"

    def loose_context(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude={"thread_id"})


class ScreenplayGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str
    thread_id: str
    workspace_id: str
    session_name: str
    workspace_path: str
    title: str
    content: str
    logline: str = ""
    synopsis: str = ""
    beats: list[str] = Field(default_factory=list)


class InitResponse(BaseModel):
    """GET /api/init — 页面首次加载时一次性返回 workspaces + styles。"""
    workspaces: list[WorkspaceSummary]
    styles: list[StyleSummary]


class WorkspaceBootstrapResponse(BaseModel):
    """GET /api/workspaces/{id}/bootstrap — 选中工作区后一次性返回全部面板数据。

    FR-004/005（REQ-20260930-163019）：outline/detail_outline/novel 已随 v8 产物链路退役。
    """
    threads: list[ThreadSummary]
    storyline: WorkspaceStorylineContent | None = None
    characters: WorkspaceCharacterContent | None = None
    worldview: WorkspaceWorldviewContent | None = None
