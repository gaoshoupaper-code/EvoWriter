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


class HookProgress(BaseModel):
    """钩子登记行（FR-004 合并版：hooks.md 九列的前端视图）。"""

    id: str = ""
    text: str = ""       # 钩子内容
    level: str = ""      # 主线大期待 / 线级期待 / 事件钩子
    type: str = ""       # 悬念 / 期待 / 危机 / 反转暗示
    status: str = ""     # 未收 / 推进中 / 已收 / 已放弃
    plant_events: list[str] = Field(default_factory=list)
    progress_events: list[str] = Field(default_factory=list)
    payoff_events: list[str] = Field(default_factory=list)
    note: str = ""


class RhythmDataModel(BaseModel):
    """节奏数据包（REQ-20261010-000638 FR-006 / DEC-009/012）。

    mainline = 主线张力曲线（与目标形态对比的对象）；
    synthesis = 全局合成曲线（全部明线事件按 T 号合并、同时点取各线最大张力）；
    dark = 暗线各自曲线（读者不可见，不计入 synthesis）；
    shape_slots = 故事核心「节奏曲线」槽位锚点；hooks = 钩子登记进度。
    全部事件无张力标注（旧大纲）→ 整体为 None，前端显示降级提示（DEC-015）。
    """

    mainline: list[RhythmPoint] = Field(default_factory=list)
    synthesis: list[RhythmPoint] = Field(default_factory=list)
    dark: dict[str, list[RhythmPoint]] = Field(default_factory=dict)
    shape_slots: list[ShapeSlotModel] = Field(default_factory=list)
    hooks: list[HookProgress] = Field(default_factory=list)


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


class OutlineFileEntry(BaseModel):
    """outline 产物文件（REQ-20261011-142529 FR-001/010，DEC-004）。"""

    rel: str = Field(description="相对 workspace 路径，如 outline/04-总纲.md")
    markdown: str


class WorkspaceOutlineContent(BaseModel):
    """管线阶段条 + outline 产物全集（桌面端 outline 视图 / SSE watch 载荷）。"""

    kind: str = Field(description="five | legacy（旧五件套工作区，files 为空）")
    pipeline: dict[str, Any] = Field(default_factory=dict, description="pipeline_status 输出")
    files: list[OutlineFileEntry] = Field(default_factory=list)


class ManuscriptContent(BaseModel):
    """单章正文（REQ-20261018-115508 FR-009 人修编辑载荷）。"""

    chapter: int = Field(description="章号")
    markdown: str = Field(default="", description="正文全文（markdown）")
    status: str = Field(default="draft", description="draft（初稿）| finalized（人修定稿）")
    exists: bool = Field(default=False, description="该章正文是否已落盘")


class ManuscriptSaveRequest(BaseModel):
    """正文保存请求（客户端直写，不经过 agent）。"""

    markdown: str = Field(min_length=1, description="修改后的正文全文")
    finalize: bool = Field(default=False, description="true = 本次保存后人修定稿（状态行改写）")


class WritingStageUpdateRequest(BaseModel):
    """人工段指针写入请求（REQ-20260919-232617 FR-002/004）。

    前进/跳段/回退统一入口：只由界面人工确认动作调用（服务端写
    confirmed_stage，模型工具箱无此通道）。
    """

    stage: str = Field(description="目标大段：theme/outline/volume/chapter/text")


class WritingStageAdvanceRequest(BaseModel):
    """段审确认循环推进请求（自动模式「确认通过」时由界面调用）。

    trace_id 是待审 run 的幂等键：同一 trace 重复推进为 no-op
    （resume 网络重试不得二次跳段）。
    """

    trace_id: str = Field(default="", description="待审 trace_id（幂等键）")


class WritingModeUpdateRequest(BaseModel):
    """段级创作模式写入请求（REQ-20261026-191719 FR-008）。

    只由界面模式开关调用（模型不可写）；题材/总纲段服务端拒绝 auto。
    text 段可同时携带批次章数（批次审查卡现场指定，DEC-007）。
    """

    stage: str = Field(description="目标大段：volume/chapter/text")
    mode: str = Field(description="interactive|auto")
    batch_size: int | None = Field(
        default=None, ge=1, le=100, description="正文批次章数（仅 text 段生效）"
    )


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
    # 丢弃重开（REQ-20261011-142529 DEC-020 第 4 条）：true = 作废未决拍板、
    # 新会话新 thread（不强制回原会话——切新会话即表达对上次不满）。
    fresh_session: bool = False
    # 会话消息台账键（REQ-20261019-222403 FR-005/DEC-009）：客户端生成的用户消息
    # 稳定 id；断连后排队消息补跑时回传同一 id，服务端日志按 id 幂等入队。
    message_id: str | None = None

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
    outline: WorkspaceOutlineContent | None = None


class RunStateOption(BaseModel):
    """run-state 对账端点的选项（与 confirm_with_user 的 ConfirmOption 对齐）。"""

    label: str
    description: str = ""


class AwaitingInputDetail(BaseModel):
    """run-state 对账端点的等待输入详情（从 trace hitl/tool_start 事件重建）。

    字段与 SSE interrupt 帧对齐（hitl.py DD4 协议），桌面端据此恢复拍板卡。
    """

    kind: str = "choice"
    question: str = ""
    options: list[RunStateOption] | None = None
    multi_select: bool = False
    source: str | None = None
    # 卡点产生时间（hitl 事件时间戳）：客户端恢复卡片时作 askedAt 用。
    asked_at: str | None = None


class ThreadRunStateResponse(BaseModel):
    """GET /api/threads/{id}/run-state — 桌面端恢复对账（D-对账）。

    返回该会话最新 run 的服务端真相；无任何 run 时 status="idle"。
    awaiting 仅在 status="awaiting_input" 且提问重建成功时非 None。
    """

    trace_id: str | None = None
    status: str
    awaiting: AwaitingInputDetail | None = None
