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
    storylines = 全部参与线名（多条 = 交汇事件，DEC-010）；
    tension/payoff = 节奏标注（REQ-20261010-000638 FR-006；旧大纲无此列为
    tension=None / payoff 空串）。
    """

    t: str = ""
    name: str = ""
    type: str = ""
    storylines: list[str] = Field(default_factory=list)
    characters: str = ""
    location: str = ""
    desc: str = ""
    tension: int | None = None
    payoff: str = ""


class RhythmPoint(BaseModel):
    """张力曲线上的一个点（REQ-20261010-000638 FR-006 / DEC-009）。"""

    t: str = ""          # 原始时序号（T1 / T12.5）
    name: str = ""       # 事件名
    tension: int | None = None  # 1~5；旧大纲缺失为 None
    payoff: str = ""     # ""/—/小/大
    line: str = ""       # 主属线名
    surface: bool = False  # 暗线浮出时点标记（FR-006）


class ShapeSlotModel(BaseModel):
    """目标形态的一个槽位锚点（DEC-008）。"""

    slot: str = ""       # 首事件 / 前段末 / 中点谷 / 终局
    op: str = "≈"        # >= | <= | ≈ | =
    values: list[int] = Field(default_factory=list)
    twin_peak: bool = False


class PromiseProgress(BaseModel):
    """许诺进度行（FR-004 台账的前端视图）。"""

    id: str = ""
    text: str = ""
    level: str = ""      # 主线大期待 / 线级期待 / 事件钩子
    line: str = ""       # 所属线（大期待为「全局」）
    status: str = ""     # 已许诺 / 推进中 / 已兑现 / 已放弃
    promise_events: list[str] = Field(default_factory=list)
    progress_events: list[str] = Field(default_factory=list)
    payoff_events: list[str] = Field(default_factory=list)
    note: str = ""


class RhythmDataModel(BaseModel):
    """节奏数据包（REQ-20261010-000638 FR-006 / DEC-009/012）。

    mainline = 主线张力曲线（与目标形态对比的对象）；
    synthesis = 全局合成曲线（全部明线事件按 T 号合并、同时点取各线最大张力）；
    dark = 暗线各自曲线（读者不可见，不计入 synthesis）；
    shape_slots = 故事核心「节奏曲线」槽位锚点；promises = 许诺进度。
    全部事件无张力标注（旧大纲）→ 整体为 None，前端显示降级提示（DEC-015）。
    """

    mainline: list[RhythmPoint] = Field(default_factory=list)
    synthesis: list[RhythmPoint] = Field(default_factory=list)
    dark: dict[str, list[RhythmPoint]] = Field(default_factory=dict)
    shape_slots: list[ShapeSlotModel] = Field(default_factory=list)
    promises: list[PromiseProgress] = Field(default_factory=list)


class WorkspaceStorylineContent(BaseModel):
    """故事线产物内容（REQ-20260930-002231 FR-014：双格式演进）。

    format="v2"：storyline.md 单文件（故事核心 + 线区块）——markdown 承载全文，
    entries 按线区块拆分（title=线名），index_markdown 同 markdown（兼容旧前端字段）。
    format="legacy"：旧多文件格式（storyline/ 目录）——维持旧读取行为（FR-013 降级）。
    panorama：跨线全景事件列表（FR-003/REQ-20260930-163019，仅 v2 有值——
    legacy 旧格式不解析，前端降级为按线分区块视图）。
    rhythm：节奏数据包（REQ-20261010-000638 FR-006，仅 v2 且事件含张力标注）。
    """

    workspace_id: str
    format: str = "v2"
    markdown: str = ""
    index_markdown: str = ""
    entries: list[StorylineEntry] = Field(default_factory=list)
    file_count: int = 0
    panorama: list[PanoramaEvent] = Field(default_factory=list)
    rhythm: RhythmDataModel | None = None


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


class ObjectMarkdownFile(BaseModel):
    """物品卡文件（REQ-20261004-221109 FR-007）：一物品一文件，markdown 承载全卡。"""

    filename: str
    name: str
    markdown: str


class WorkspaceObjectContent(BaseModel):
    workspace_id: str
    objects: list[ObjectMarkdownFile] = Field(default_factory=list)


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
    objects: WorkspaceObjectContent | None = None
