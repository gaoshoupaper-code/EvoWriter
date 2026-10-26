export type WorkspacePanel = "chat" | "artifacts";

// ── 五段式创作管线（REQ-20261018-115508 FR-011，DEC-001）──

/** 管线阶段条状态（executor pipeline_status 输出） */
export type PipelineStatus = {
  kind: string; // five | legacy
  phase: string; // theme | outline | volume | chapter | text | legacy
  next_unit: string;
  /** 段就绪信号（REQ-20261019-232617 FR-001）：产物齐全可请用户确认进下一段 */
  ready?: { ready: boolean; next: string | null };
  awaiting: Record<string, string>;
  updated_at?: string;
  agent?: string | null;
};

export type OutlineFileEntry = {
  rel: string;
  markdown: string;
};

export type WorkspaceOutlineContent = {
  kind: string;
  pipeline: PipelineStatus;
  files: OutlineFileEntry[];
};

export type Style = {
  style_id: string;
  name: string;
  meta_style: string;
  storybuilding_style: string;
  detail_outline_style: string;
  writing_style: string;
  created_at: string;
};

export type ScreenplayResponse = {
  mode: string;
  thread_id: string;
  workspace_id: string;
  session_name: string;
  workspace_path: string;
  title: string;
  content: string;
  logline: string;
  synopsis: string;
  beats: string[];
};

export type ThreadSummary = {
  thread_id: string;
  workspace_id: string;
  session_name: string;
  workspace_path: string;
  created_at: string;
  updated_at: string;
};

export type WorkspaceSummary = {
  workspace_id: string;
  title: string;
  domain: string;
  workspace_path: string;
  created_at: string;
  updated_at: string;
  session_count: number;
  active_style_id: string | null;
};

export type StorylineEntry = {
  filename: string;
  title: string;
  markdown: string;
};

export type WorkspaceStorylineContent = {
  workspace_id: string;
  format: string; // "v2"（单文件区块）| "legacy"（storyline/ 目录旧格式）
  index_markdown: string;
  entries: StorylineEntry[];
  file_count: number;
  panorama: PanoramaEvent[]; // 跨线全景事件（仅 v2；legacy 为空数组，前端降级）
  rhythm?: RhythmData | null; // 节奏数据包（REQ-20261010-000638 FR-006；旧大纲无张力为 null，旧后端缺字段为 undefined）
};

// 大纲全景表事件行（FR-003/REQ-20260930-163019）：时序/所属线/事件/类型/角色/地点/描述
export type PanoramaEvent = {
  t: string;
  name: string;
  type: string;
  storylines: string[];
  characters: string;
  location: string;
  desc: string;
  tension?: number | null; // 张力 1~5（旧大纲缺失）
  payoff?: string; // ""/—/小/大
};

// 节奏数据（REQ-20261010-000638 FR-006 / DEC-009/012）
export type RhythmPoint = {
  t: string; // 原始时序号（T1 / T12.5）
  name: string;
  tension: number | null; // 1~5；缺失为 null
  payoff: string; // ""/—/小/大
  line: string; // 主属线名
  surface?: boolean; // 暗线浮出时点标记（FR-006）
};

export type ShapeSlot = {
  slot: string; // 首事件 / 前段末 / 中点谷 / 终局
  op: string; // >= | <= | ≈ | =
  values: number[];
  twin_peak: boolean;
};

export type HookProgress = {
  id: string;
  text: string; // 钩子内容
  level: string; // 主线大期待 / 线级期待 / 事件钩子
  type: string; // 悬念 / 期待 / 危机 / 反转暗示
  status: string; // 未收 / 推进中 / 已收 / 已放弃
  plant_events: string[];
  progress_events: string[];
  payoff_events: string[];
  note: string;
};

export type RhythmData = {
  mainline: RhythmPoint[]; // 主线张力曲线（与目标形态对比）
  synthesis: RhythmPoint[]; // 全局合成曲线（明线合并、同时点取最大张力）
  dark: Record<string, RhythmPoint[]>; // 暗线各自曲线（读者不可见，不计入合成）
  shape_slots: ShapeSlot[];
  hooks: HookProgress[];
};

export type WorkspaceWorldviewContent = {
  workspace_id: string;
  markdown: string;
};

export type CharacterMarkdownFile = {
  filename: string;
  name: string;
  markdown: string;
};

export type WorkspaceCharacterContent = {
  workspace_id: string;
  characters: CharacterMarkdownFile[];
};

export type ObjectMarkdownFile = {
  filename: string;
  name: string;
  markdown: string;
};

export type WorkspaceObjectContent = {
  workspace_id: string;
  objects: ObjectMarkdownFile[];
};

export type StreamEvent = {
  type: "model_output" | "tool_call" | "tool_output" | "tool_error" | "model_stream" | "reasoning_stream" | "final" | "trace_event" | "trace_snapshot" | "interrupt" | "auto_answer" | "credit_exhausted" | "pipeline" | "row" | "status";
  data: Record<string, unknown>;
};

// ── 行级时间线（REQ-20261018-235653，FR-001）──
// 服务端行装配（executor WritingEventSink）产出的行事件落成的时间线行，
// 新消息的真相源（DEC-001 A 档）：思考/正文/工具/子代理按行序交错。
// 旧消息无 rows 字段 → 走旧渲染（FR-007 双轨）。

export type TimelineRowKind = "reasoning" | "text" | "tool" | "subagent";

export type TimelineRowStatus = "streaming" | "complete" | "interrupted" | "failed";

export type TimelineRow = {
  /** 全局单调递增行序号（服务端分配），时间线按它排序 */
  seq: number;
  id: string;
  kind: TimelineRowKind;
  /** 同一模型响应的思考/正文/工具共享的响应标识（对齐 ZCode assistantResponseId） */
  responseId?: string;
  /** 思考段开段时的活跃子任务人话名快照 */
  label?: string;
  /** 思考/正文行的文本（end 携带全量时整体替换，保证流式/全量对齐） */
  text?: string;
  status: TimelineRowStatus;
  startedAt: number;
  endedAt?: number;
  // 工具行
  toolName?: string;
  callId?: string;
  // 子代理行
  subagentType?: string;
  iteration?: number;
  /** 子代理行摘要（task 工具产出概要，FR-005） */
  summary?: string;
};

export type TraceStatus = "running" | "awaiting_input" | "completed" | "failed" | "cancelled" | "interrupted";

export type TraceEventType =
  | "run_start"
  | "run_end"
  | "run_error"
  | "run_awaiting"
  | "run_cancelled"
  | "llm_start"
  | "llm_end"
  | "llm_error"
  | "tool_start"
  | "tool_end"
  | "tool_error";

export type TraceUsage = {
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
};

export type TraceContextRange = {
  start_anchor_id?: string | null;
  end_anchor_id?: string | null;
};

export type TraceNodeKind = "run" | "agent" | "llm" | "tool" | "todo" | "error" | "skill" | (string & {});
export type TraceAgentRole = "main" | "subagent" | (string & {});
export type TraceContextKind = "system" | "human" | "ai" | "tool" | "todo" | "error" | "skill" | (string & {});

export type TraceLogEvent = {
  trace_id: string;
  event_id: string;
  sequence: number;
  type: TraceEventType;
  status: TraceStatus;
  timestamp: string;
  source: "system" | "middleware";
  duration_ms?: number | null;
  run_id?: string | null;
  parent_run_id?: string | null;
  parent_event_id?: string | null;
  agent_name?: string | null;
  node_name?: string | null;
  model_name?: string | null;
  input?: unknown;
  output?: unknown;
  usage?: TraceUsage | null;
  tool_calls?: unknown;
  tool_call_id?: string | null;
  tool_name?: string | null;
  tool_args?: unknown;
  tool_output?: unknown;
  context_anchor_id?: string | null;
  input_context_range?: TraceContextRange | null;
  output_context_anchor_id?: string | null;
  error?: string | null;
  skill_name?: string | null;
};

export type TraceNode = {
  node_id: string;
  parent_node_id?: string | null;
  kind: TraceNodeKind;
  label: string;
  status: TraceStatus;
  agent_name?: string | null;
  agent_role?: TraceAgentRole | null;
  depth: number;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  model_name?: string | null;
  tool_name?: string | null;
  skill_name?: string | null;
  usage?: TraceUsage | null;
  context_anchor_id?: string | null;
  input_context_range?: TraceContextRange | null;
  output_context_anchor_id?: string | null;
  raw_event_ids: string[];
  error?: string | null;
  chain_summary?: string | null;
  parallel_group_id?: string | null;
};

export type TraceContextSegment = {
  anchor_id: string;
  sequence: number;
  kind: TraceContextKind;
  agent_name?: string | null;
  agent_role?: TraceAgentRole | null;
  depth: number;
  title: string;
  content: unknown;
  metadata: Record<string, unknown>;
  tool_call_names: string[];
  related_node_id?: string | null;
  collapsed_by_default: boolean;
};

export type TraceTodoItem = {
  id?: string | null;
  content: string;
  status: "pending" | "in_progress" | "completed";
};

export type TraceTodoSnapshot = {
  anchor_id: string;
  agent_name?: string | null;
  items: TraceTodoItem[];
  active_item?: string | null;
};

export type TraceRunSummary = {
  trace_id: string;
  workspace_id: string;
  thread_id: string;
  session_name: string;
  workspace_path: string;
  endpoint: string;
  status: TraceStatus;
  started_at: string;
  ended_at?: string | null;
  duration_ms?: number | null;
  event_count: number;
  path: string;
  error?: string | null;
};

export type TraceDetail = {
  run: TraceRunSummary;
  events: TraceLogEvent[];
  nodes: TraceNode[];
  context: TraceContextSegment[];
  todos: TraceTodoSnapshot[];
};

export type ToolStatus = {
  key: string;
  name: string;
  status: "running" | "done" | "failed";
  parentKey?: string;
  subagentName?: string;
  // P1 扩展（仅 task 工具有值）：供 stageFlow 生成阶段/章节焦点（D6/D7）
  subagentType?: string; // storybuilding / general-purpose
  iteration?: number | null; // storybuilding 轮次 / 调用序
  // FR-006 扩展：步骤耗时（ms 时间戳，展开态步骤列表用）
  startedAt?: number;
  endedAt?: number;
};

// HITL 选项化：ask_user 的结构化选项（label + 一句话解释）
export type AskUserOption = {
  label: string;
  description: string;
};

export type ExecutionPhase =
  | "idle"
  | "booting"
  | "thinking"
  | "asking"
  | "answered"
  | "delivering"
  | "failed"
  | "stopped";

// 思考段（FR-003，REQ-20261010-182114）：一轮 LLM 调用的 reasoning 流，
// 按 tool 事件边界切段；随消息持久化（DEC-003），历史可回看。
export type ThoughtSegment = {
  id: number;
  text: string;
  // 开段时快照的活跃子任务人话名（由 tools 状态推断，FR-003）
  label?: string;
  startedAt: number;
  endedAt?: number;
  status: "streaming" | "done" | "interrupted";
};

export type ChatMessage = {
  role: "assistant" | "user";
  content: string;
  tools?: ToolStatus[];
  // 思考流分段（DEC-005：思考段与工具行穿插的时间线；恢复/回看共用）
  thoughts?: ThoughtSegment[];
  // 行级时间线（REQ-20261018-235653）：行事件流的落点；有值时走 RowTimeline 渲染。
  // 旧消息无此字段（FR-007 双轨）。
  rows?: TimelineRow[];
  contentFormat?: "text" | "markdown";
  // 三步管线阶段徽章（REQ-20261015-161200）：assistant 消息
  // 创建时从 contentStore.outlinePipeline.phase 盖章（worldview/outline/chapters；
  // 历史消息可能是 storydesign/storystructure）
  agent?: string;
  // D2: 关联本次提交的 trace run（一次提交 = 一条 assistant message = 一个 trace）
  traceId?: string;
  // D2/P7: 本条消息的执行态（queued=同会话排队中 / streaming=进行中 / completed /
  // failed / stopped / answered=拍板卡已被回答，本轮以用户决策收尾，后续输出归新一轮消息）
  status?: "streaming" | "queued" | "completed" | "failed" | "stopped" | "answered";
  // 会话消息台账键（REQ-20261019-222403 FR-005/DEC-009）：用户消息的稳定 id，
  // 服务端日志按 id 幂等入队；排队消息断连后按此 id 补跑。
  messageId?: string;
  // 排队消息断连待补跑标记（DEC-009）：下一次提交/重试时自动按序补发。
  queuedInterrupted?: boolean;
  // T17: 体验阶段（驱动 ExecutionView 子视图切换）。
  // 由 derivePhaseFromMessage 从 status/tools/awaitingInput 实时派生，
  // executionStore 在每次 message 更新后写入。
  executionPhase?: ExecutionPhase;
  // HITL: 子代理 ask_user 中断，等待用户回答（resume 提交后清除）
  // DD4: kind 区分反馈类型（choice=访谈 / image_review=图像评审）
  awaitingInput?: {
    kind?: string; // "choice" | "image_review" | "review"，缺省视为 choice（向后兼容）
    question: string;
    options?: AskUserOption[] | null;
    multi_select?: boolean;
    source?: string;
    // review 停等点（REQ-20261026-191719 TD-003）：batch=正文批次 / style=文风卡 / stage=段产物审
    review_kind?: "batch" | "style" | "stage";
    // image_review 透传（DD4）
    round?: number;
    versions?: unknown[];
    // 卡点产生时间（ISO）：本地持久化恢复时按 2h 阈值降级只读（REQ-20260927-170627 FR-005）
    askedAt?: string;
  };
  // 自动模式违纪代答记录（REQ-20261026-191719 FR-007）：SSE auto_answer 帧
  // 逐条累积，消息流内渲染灰条（事后可回查）
  autoAnswers?: { question: string; answer: string; at: string }[];
};

export type CheckpointToolCall = {
  name: string;
  id: string;
};

export type CheckpointMessage = {
  role: "system" | "human" | "ai" | "tool";
  content: string;
  tool_calls?: CheckpointToolCall[];
  name?: string;
};

export type CheckpointState = {
  thread_id: string;
  messages: CheckpointMessage[];
};

export type InitResponse = {
  workspaces: WorkspaceSummary[];
  styles: Style[];
};

export type CharacterGenerateRequest = {
  thread_id: string;
  prompt?: string;
  content?: string;
  text?: string;
  name?: string;
  role?: string;
  description?: string;
};

export type CharacterGenerateResponse = {
  mode: string;
  thread_id: string;
  workspace_id: string;
  session_name: string;
  workspace_path: string;
  name: string;
  identity: string;
  appearance: string;
  personality: string;
  current_state: string;
  relationships: string;
  markdown: string;
};

export type WorkspaceBootstrapResponse = {
  threads: ThreadSummary[];
  storyline: WorkspaceStorylineContent | null;
  characters: WorkspaceCharacterContent | null;
  worldview: WorkspaceWorldviewContent | null;
  objects: WorkspaceObjectContent | null;
  outline: WorkspaceOutlineContent | null;
};
