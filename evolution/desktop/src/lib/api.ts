import { invoke } from "@tauri-apps/api/core";
import type {
  TraceDetailLite,
  TraceListItem,
  TraceListResponse,
  ActiveRun,
  TraceLogEvent,
  TraceContextSegment,
  ArtifactRevisionContentResponse,
  ArtifactRevisionListResponse,
  TraceIntegrityStatus,
  TraceWorkload,
  WorkloadProfilesResponse,
} from "@/lib/types";

// 统一类型从 lib/types.ts 引用（trace 移植后对齐）
export type {
  TraceDetailLite,
  TraceListItem,
  TraceListResponse,
  ActiveRun,
};

/**
 * Evolution 桌面端 API 客户端（桌面化改造 2026-07-07）。
 *
 * 复用写作 desktop 的 Rust 中继架构（apiFetch + RelayResponse + onUnauthorized），
 * 业务函数从零写（evolution 领域）。
 *
 * 关键差异：evolution 后端通过 nginx 子路径 /evolution-api 反代，
 * 所以所有请求 path 前加 "/evolution-api" 前缀。
 * 登录走 executor 的 /api（SSO），不加 evolution-api 前缀。
 */

/// Rust http_request command 的返回结构（对应 src-tauri/src/http.rs HttpResponse）。
interface HttpRelayResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
}

/**
 * 401 跳转回调：由路由层注入（api.ts 不直接耦合 react-router）。
 * 去抖：500ms 窗口内多次 401 只触发一次，避免并发请求同时 401 导致反复跳转闪烁。
 */
let onUnauthorized: (() => void) | null = null;
let _unauthorizedFiring: boolean = false;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

function triggerUnauthorized() {
  if (!onUnauthorized || _unauthorizedFiring) return;
  _unauthorizedFiring = true;
  onUnauthorized();
  // 500ms 去抖窗口：窗口内到达的其它 401 不再重复触发跳转
  setTimeout(() => { _unauthorizedFiring = false; }, 500);
}

/**
 * 仿 Response 对象：让上层业务代码用 .ok/.status/.json() 无需改动。
 */
class RelayResponse {
  readonly status: number;
  readonly headers: Map<string, string>;
  private readonly bodyText: string;

  constructor(relay: HttpRelayResponse) {
    this.status = relay.status;
    this.headers = new Map(Object.entries(relay.headers));
    this.bodyText = relay.body;
  }

  get ok(): boolean {
    return this.status >= 200 && this.status < 300;
  }

  async json(): Promise<any> {
    return JSON.parse(this.bodyText);
  }

  async text(): Promise<string> {
    return this.bodyText;
  }
}

// evolution API 前缀：
// - 生产：/evolution-api（nginx 子路径反代，同域 cookie 共享 SSO）
// - 本地 dev：空前缀（直连 evolution:7789，无 nginx）
const EVO_PREFIX = import.meta.env.DEV ? "" : "/evolution-api";

/**
 * 统一请求封装（走 Rust 中继）：
 * - executor 接口（登录/鉴权）：path 不加前缀（如 /api/auth/login）
 * - evolution 接口：path 加 /evolution-api 前缀（如 /evolution-api/api/config/llm）
 *
 * 用 evoFetch 调 evolution，apiFetch 调 executor。
 */
async function apiFetch(input: string, init: RequestInit = {}): Promise<RelayResponse> {
  return _fetch(input, init);
}

/** evolution 接口专用（自动加 /evolution-api 前缀）。 */
async function evoFetch(path: string, init: RequestInit = {}): Promise<RelayResponse> {
  // path 形如 "/api/config/llm"，加前缀后 "/evolution-api/api/config/llm"
  const full = path.startsWith("/") ? `${EVO_PREFIX}${path}` : `${EVO_PREFIX}/${path}`;
  return _fetch(full, init);
}

/**
 * SSE 流式 path（stream_request command 用，与 evoFetch 同前缀规则）。
 * 观测大盘实时化（REQ-20260920-193428 FR-005）。
 */
export function evoStreamPath(path: string): string {
  return path.startsWith("/") ? `${EVO_PREFIX}${path}` : `${EVO_PREFIX}/${path}`;
}

/**
 * 503（认证服务不可达）静默重试：最多 3 次，指数退避 1s/2s/4s。
 * 重试期间不跳登录、不闪烁，只有重试耗尽才把 503 响应返回给上层。
 */
const _RETRY_503_MAX = 3;
const _RETRY_503_BASE_MS = 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function _fetch(input: string, init: RequestInit): Promise<RelayResponse> {
  let bodyValue: unknown = undefined;
  if (init.body) {
    const raw = typeof init.body === "string" ? init.body : String(init.body);
    try {
      bodyValue = JSON.parse(raw);
    } catch {
      bodyValue = raw;
    }
  }

  const headers: Record<string, string> = {};
  if (init.headers) {
    const h = init.headers as Record<string, string>;
    for (const k of Object.keys(h)) {
      headers[k] = h[k];
    }
  }

  const reqArgs = {
    path: input,
    method: init.method || "GET",
    headers: Object.keys(headers).length ? headers : null,
    body: bodyValue ?? null,
    stream: false,
  };

  // 503 重试循环：executor 不可达时静默重试，不跳登录
  for (let attempt = 0; ; attempt++) {
    const relay = await invoke<HttpRelayResponse>("http_request", { request: reqArgs });

    if (relay.status === 503 && attempt < _RETRY_503_MAX) {
      await sleep(_RETRY_503_BASE_MS * Math.pow(2, attempt)); // 1s/2s/4s
      continue;
    }

    // 401 → 触发跳登录（去抖：500ms 窗口内多次只触发一次）
    if (relay.status === 401) {
      triggerUnauthorized();
    }

    return new RelayResponse(relay);
  }
}

/** JSON 解析辅助：非 2xx 抛错，2xx 返回 json。 */
async function apiJson<T>(input: string, init: RequestInit, fetcher = apiFetch): Promise<T> {
  const resp = await fetcher(input, init);
  if (!resp.ok) {
    const text = (await resp.text()).trim();
    let message = text;

    if (text) {
      try {
        const payload = JSON.parse(text) as { detail?: unknown; message?: unknown };
        if (typeof payload.detail === "string") {
          message = payload.detail;
        } else if (typeof payload.message === "string") {
          message = payload.message;
        }
      } catch {
        // 非 JSON 响应（例如网关错误）保留原文，便于定位服务异常。
      }
    }

    throw new Error(message || `请求失败（HTTP ${resp.status}）`);
  }
  return resp.json();
}

/** evolution JSON 辅助（加前缀）。 */
async function evoJson<T>(path: string, init: RequestInit): Promise<T> {
  return apiJson<T>(path, init, evoFetch);
}

// ════════════════════════════════════════════════════════════
//  认证（走 executor /api，SSO）
// ════════════════════════════════════════════════════════════

export interface AuthMe {
  user_id: string;
  username: string;
  is_admin: boolean;
  is_super_admin: boolean;
  has_api_key: boolean;
}

export async function login(username: string, password: string): Promise<AuthMe> {
  return apiJson<AuthMe>("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export async function logout(): Promise<{ ok: boolean }> {
  return apiJson<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
}

/** 探测登录态（非抛错版，路由守卫用）。 */
export async function fetchMeOrNull(): Promise<AuthMe | null> {
  try {
    const resp = await apiFetch("/api/auth/me");
    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

// ════════════════════════════════════════════════════════════
//  LLM 配置（走 evolution /evolution-api/api/config）
//  scope 分家（2026-07-18）：'evolution'=评估 / 'executor'=写作，各自独立激活
// ════════════════════════════════════════════════════════════

/** LLM 配置归属 scope：进化 Agent 评估用 / executor 写作用。 */
export type LlmConfigScope = "evolution" | "executor";

/** 配置列表项（不回显 key 明文，附 key_hint 尾 4 位脱敏）。 */
export interface LlmConfigItem {
  id: number;
  name: string;
  base_url: string;
  model: string;
  has_key: boolean;
  key_hint: string | null;
  is_active: boolean;
  scope: LlmConfigScope;
  created_at: string;
  updated_at: string;
}

export interface LlmConfigTestResult {
  ok: boolean;
  latency_ms: number;
  error: string | null;
}

/** 读取指定 scope 的激活配置安全视图。 */

export async function listLlmConfigs(scope: LlmConfigScope): Promise<LlmConfigItem[]> {
  return evoJson<LlmConfigItem[]>(`/api/config/llm/list?scope=${scope}`, { method: "GET" });
}

/** 新建配置（按 scope 归属，该 scope 首条自动激活）。 */
export async function createLlmConfig(
  scope: LlmConfigScope,
  payload: {
    name: string;
    api_key: string;
    base_url: string;
    model: string;
  },
): Promise<LlmConfigItem> {
  return evoJson<LlmConfigItem>(`/api/config/llm?scope=${scope}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** 更新配置。api_key 传空串=不改 key。按 id 操作，scope 由 id 隐含。 */
export async function updateLlmConfig(
  id: number,
  payload: {
    name?: string;
    api_key?: string;
    base_url?: string;
    model?: string;
  },
): Promise<LlmConfigItem> {
  return evoJson<LlmConfigItem>(`/api/config/llm/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** 删除配置。删激活项会自动激活同 scope 剩余中 id 最小的一条。 */
export async function deleteLlmConfig(id: number): Promise<{ ok: boolean }> {
  return evoJson<{ ok: boolean }>(`/api/config/llm/${id}`, {
    method: "DELETE",
  });
}

/** 设为激活（scope 内唯一，scope 由 id 隐含）。 */
export async function activateLlmConfig(id: number): Promise<LlmConfigItem> {
  return evoJson<LlmConfigItem>(`/api/config/llm/${id}/activate`, {
    method: "POST",
  });
}

/**
 * 测试连通性。两条路径二选一：
 * - 测已存配置：传 { id }，后端读库解密。
 * - 测草稿：传 { api_key, base_url, model }。
 * 同时传时 id 优先。测试逻辑与 scope 正交（T3），故不带 scope 参数。
 */
export async function testLlmConfig(payload: {
  id?: number;
  api_key?: string;
  base_url?: string;
  model?: string;
}): Promise<LlmConfigTestResult> {
  return evoJson<LlmConfigTestResult>("/api/config/llm/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getActiveRuns(): Promise<ActiveRun[]> {
  return evoJson<ActiveRun[]>("/api/active-runs", { method: "GET" });
}

// ── trace 列表 + 详情 ──

export async function getTraces(params?: {
  status?: string;
  run_purpose?: string;
  workload?: TraceWorkload;
  integrity_status?: TraceIntegrityStatus;
  owner?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}): Promise<TraceListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.run_purpose) qs.set("run_purpose", params.run_purpose);
  if (params?.workload) qs.set("workload", params.workload);
  if (params?.integrity_status) qs.set("integrity_status", params.integrity_status);
  if (params?.owner) qs.set("owner", params.owner);
  if (params?.since) qs.set("since", params.since);
  if (params?.until) qs.set("until", params.until);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  const q = qs.toString();
  return evoJson<TraceListResponse>(`/api/traces${q ? "?" + q : ""}`, { method: "GET" });
}

// ── 人工确认进证据编纂（REQ-20260802-211032）──
// 产品负责人对"用户主动停止但有价值"的 cancelled+user_stop trace 发起确认。

export async function getWorkloadProfiles(hours = 720): Promise<WorkloadProfilesResponse> {
  return evoJson<WorkloadProfilesResponse>(`/api/analysis/profiles?hours=${hours}`, { method: "GET" });
}

export async function getTraceArtifactRevisions(
  traceId: string,
): Promise<ArtifactRevisionListResponse> {
  return evoJson<ArtifactRevisionListResponse>(
    `/api/traces/${encodeURIComponent(traceId)}/artifact-revisions`,
    { method: "GET" },
  );
}

export async function getArtifactRevisionContent(
  revisionId: string,
): Promise<ArtifactRevisionContentResponse> {
  return evoJson<ArtifactRevisionContentResponse>(
    `/api/artifacts/revisions/${encodeURIComponent(revisionId)}/content`,
    { method: "GET" },
  );
}

/** 用户缓存列表（trace 历史页用户筛选下拉用） */

export async function getTraceDetail(traceId: string): Promise<TraceDetailLite> {
  return evoJson<TraceDetailLite>(`/api/traces/${encodeURIComponent(traceId)}`, { method: "GET" });
}

/** 按需从 executor 增量同步运行中 trace，并返回最新详情。 */
export async function refreshExecutorTrace(traceId: string): Promise<TraceDetailLite> {
  return evoJson<TraceDetailLite>(
    `/api/traces/${encodeURIComponent(traceId)}/refresh`,
    { method: "POST" },
  );
}

/**
 * 按 event_id 批量拉取原始事件（抽屉懒加载，Phase 2）。
 * 前端从 node.raw_event_ids 拿到事件 id 列表，调本接口拉取。
 */
export async function getTraceEvents(
  traceId: string,
  eventIds: string[],
): Promise<TraceLogEvent[]> {
  const ids = eventIds.join(",");
  return evoJson<TraceLogEvent[]>(`/api/traces/${traceId}/events?event_ids=${encodeURIComponent(ids)}`, {
    method: "GET",
  });
}

/**
 * 按 anchor_id 拉取单个 context segment（抽屉懒加载，Phase 2）。
 */
export async function getTraceContext(
  traceId: string,
  anchorId: string,
): Promise<TraceContextSegment> {
  return evoJson<TraceContextSegment>(
    `/api/traces/${traceId}/context?anchor_id=${encodeURIComponent(anchorId)}`,
    { method: "GET" },
  );
}

// ── trace 稳定性重构（Pull 模式新接口，设计 20260720_203000）──

/** trace 当前被哪个活跃 session 跑（详情页停止按钮反查用）。 */
export interface ActiveSession {
  session_type: "evolve" | "test" | null;
  session_id: string | null;
  /** 后端算好的 stop 端点路径（如 /api/evolve/sessions/xxx/stop），前端直接 POST。 */
  stop_endpoint: string | null;
}

export async function getActiveSession(traceId: string): Promise<ActiveSession> {
  return evoJson<ActiveSession>(`/api/traces/${traceId}/active-session`, { method: "GET" });
}

/** 用户手动收敛 interrupted trace 为 failed/completed。 */
export async function resolveTrace(
  traceId: string,
  targetStatus: "failed" | "completed",
  note?: string,
): Promise<{ status: string; resolved_to: string; trace_id: string }> {
  return evoJson(`/api/traces/${traceId}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_status: targetStatus, note: note ?? null }),
  });
}

/** Trace 完整性结构化诊断（FR-003 / AC-004）：具体缺口 + 影响下游 + 恢复动作。 */
export interface IntegrityCheck {
  check: string;       // 失败检查名
  stage: string;       // 影响阶段
  impact: string;      // 对下游的影响
  recovery: string;    // 可执行恢复动作
}

export interface IntegrityDiagnosis {
  trace_id: string;
  integrity_status: string;          // verified / incomplete / conflict / legacy
  evidence_status: string;           // complete / incomplete / ineligible_source / unknown
  evidence_gaps: string[];
  recoverable: boolean;              // 是否可通过重跑恢复
  missing_checks: IntegrityCheck[];
  affected_downstreams: string[];    // 受影响下游
}

export async function getTraceIntegrity(traceId: string): Promise<IntegrityDiagnosis> {
  return evoJson<IntegrityDiagnosis>(`/api/traces/${traceId}/integrity`, { method: "GET" });
}

/**
 * 按 session_type 分发停止调用（trace 详情页停止按钮用）。
 *
 * trace 详情页点"停止"时，先调 getActiveSession 拿到 {session_type, session_id}，
 * 再用本函数按 type 分发到对应的 stop 接口。用户不需要知道 session 类型。
 */
export async function stopActiveSession(session: {
  session_type: "evolve" | "test" | null;
  session_id: string | null;
}): Promise<{ ok: boolean; error?: string }> {
  if (!session.session_type || !session.session_id) {
    return { ok: false, error: "无活跃 session 可停止" };
  }
  try {
    if (session.session_type === "evolve") {
      await stopEvolve(session.session_id);
    } else if (session.session_type === "test") {
      await stopTest(session.session_id);
    } else {
      return { ok: false, error: `未知 session_type: ${session.session_type}` };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "停止失败" };
  }
}


export interface EvalFrame {
  type: "step" | "log" | "start" | "end" | "error";
  [key: string]: unknown;
  /** 事件 sequence（前端去重用，轮询重试不会重复渲染）。 */
  _seq?: number;
}

// ════════════════════════════════════════════════════════════
//  进化（evolve）
// ════════════════════════════════════════════════════════════

export interface EvolveSession {
  id: number;
  session_id: string;
  case_id: string;
  status: string; // running|done|failed|pending_review|published|discarded
  phase: string | null;
  baseline_trace: string | null;
  candidate_trace: string | null;
  baseline_score: number | null;
  candidate_score: number | null;
  report_json: string | null;
  created_at: string;
  updated_at: string | null;
  eval_ref: string | null;
  report?: any;
  // 审查视图内联字段（get_session 详情接口返回，列表接口不含）
  design_doc?: DesignDoc | null;
  change_log?: ChangeLog | null;
  eval_snapshot?: EvalSnapshot | null;
}

// ── 审查视图数据类型（D1：get_session 内联）──────────────────────

export interface DesignChange {
  target: string;
  change_desc: string;
  reason: string;
  evidence_ref?: string[]; // 引用 EvalFinding.id（f01/f02…）
  expected_up?: string;
  expected_down?: string;
  edit?: any;
}

export interface DesignDoc {
  meta: {
    designed_at: string;
    changes_count: number;
    changes: DesignChange[];
  };
  body: string;
}

export interface AppliedChange {
  target: string;
  action: string;
  result: "ok" | "failed";
  detail: string;
  design_ref?: number; // 对应 DesignChange 的序号（1-based）
}

export interface ChangeLog {
  meta: {
    executed_at: string;
    applied_count: number;
    applied: AppliedChange[];
    validation: { passed: boolean; config_valid?: boolean; import_ok?: boolean; errors?: string[] };
  };
  body: string;
}

export interface EvalFinding {
  id?: string;
  dimension: string;
  severity: string;
  evidence_type: string;
  finding: string;
  evidence: string;
}

export interface EvalSnapshot {
  eval_id?: string;
  trace_id?: string;
  findings: EvalFinding[] | null;
  scores: Record<string, any> | null;
}

/** 启动单体进化（阶段 D：按评估卷宗启动，永久绑定） */

export async function stopEvolve(sessionId: string): Promise<{ status: string; session_id: string }> {
  return evoJson(`/api/evolve/sessions/${sessionId}/stop`, { method: "POST" });
}

/**
 * 按 sequence 游标拉取进化 session 的事件帧（trace 重构 20260720_154825）。
 * 返回从 run_meta 派生的 message_updated/phase/log/step/proposal/finalizing 帧。
 *
 * 重构变更：移除 model_stream token 流；新增 message_updated 通知帧（消息已落库，
 * 前端调 loadMessages 拉权威存储）。
 */
export interface EvolveFrame {
  type:
    | "message_updated"
    | "phase"
    | "log"
    | "step"
    | "proposal"
    | "finalizing"
    | "start"
    | "end"
    | "error";
  [key: string]: unknown;
  _seq?: number;
}

export interface EvolveEventsSinceResponse {
  frames: EvolveFrame[];
  max_seq: number;
  has_more: boolean;
  session_status: string; // running / conversing / finalizing / pending_review / published / ...
}

export async function getEvolveSessionEventsSince(
  sessionId: string,
  sinceSeq: number,
  limit = 500,
): Promise<EvolveEventsSinceResponse> {
  return evoJson<EvolveEventsSinceResponse>(
    `/api/evolve/sessions/${sessionId}/events/since?since_seq=${sinceSeq}&limit=${limit}`,
    { method: "GET" },
  );
}

export async function getEvolveSessions(limit = 50): Promise<{ sessions: EvolveSession[]; total: number }> {
  return evoJson(`/api/evolve/sessions?limit=${limit}`, { method: "GET" });
}

export async function getEvolveSession(sessionId: string): Promise<EvolveSession> {
  return evoJson<EvolveSession>(`/api/evolve/sessions/${sessionId}`, { method: "GET" });
}

export interface PublishEvolveResponse {
  status: "activated";
  snapshot_version: number;
  source_commit: string;
  release_id: string;
  snapshot_trace_id?: string;
}

export async function publishEvolve(sessionId: string): Promise<PublishEvolveResponse> {
  return evoJson(`/api/evolve/sessions/${sessionId}/publish`, { method: "POST" });
}

export async function discardEvolve(sessionId: string): Promise<{ status: string; reset_to: string }> {
  return evoJson(`/api/evolve/sessions/${sessionId}/discard`, { method: "POST" });
}

// ── 对话式共创工作台（Phase 4，决策 T2/T10）──────────────────────

/** 进化对话消息（evolve_messages 表，决策 T6） */
export interface EvolveMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string; // markdown
  tool_events?: any[] | null; // assistant 消息触发的工具调用列表
  related_points?: string[] | null; // 涉及的进化点 id（双向高亮联动）
  seq: number;
  created_at: string;
}

/** 进化点 option（备选方案，决策 T） */
export interface EvolvePointOption {
  description: string;
  pros: string[];
  cons: string[];
  expected_impact: string;
}

/** 进化点（evolve_points 表，决策 T7/B/T） */
export interface EvolvePoint {
  id: string;
  session_id: string;
  seq: number;
  target: string;
  problem: string;
  options: EvolvePointOption[];
  recommendation?: string | null;
  note?: string | null;
  status: "proposed" | "accepted" | "rejected";
  chosen_option?: number | null; // 0-based，accepted 时
  user_note?: string | null;
  accepted_at?: string | null;
  design_ref?: number | null;
  created_at: string;
}

/** 架构蓝图 API 返回（决策 Q） */
export interface EvolveSystemPrompt {
  blueprint: string; // markdown
  version: string;
}

export async function getEvolveSystemPrompt(): Promise<EvolveSystemPrompt> {
  return evoJson<EvolveSystemPrompt>(`/api/evolve/system-prompt`, { method: "GET" });
}

/** 对话式启动进化（决策 T2，inspect round + 转 conversing） */
/** 启动对话式进化（阶段 D：按评估卷宗启动，永久绑定） */
export async function startEvolveConverse(
  benchmarkBatchId?: string | null,
): Promise<{ session_id: string; trace_id: string; status: string }> {
  // 自由启动（DEC-004）：无必填业务输入；benchmark_batch_id 可选附带弱点视图
  return evoJson(`/api/evolve/start-converse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(
      benchmarkBatchId ? { benchmark_batch_id: benchmarkBatchId } : {},
    ),
  });
}

export async function getEvolveMessages(
  sessionId: string,
  afterSeq?: number,
): Promise<{ messages: EvolveMessage[] }> {
  const qs = afterSeq !== undefined ? `?after_seq=${afterSeq}` : "";
  return evoJson(`/api/evolve/sessions/${sessionId}/messages${qs}`, { method: "GET" });
}

export async function sendEvolveMessage(
  sessionId: string,
  content: string,
): Promise<{ message_id: string; seq: number; session_id: string; status: string }> {
  return evoJson(`/api/evolve/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export async function getEvolvePoints(
  sessionId: string,
): Promise<{ points: EvolvePoint[]; accepted_count: number }> {
  return evoJson(`/api/evolve/sessions/${sessionId}/points`, { method: "GET" });
}

export async function finalizeEvolve(
  sessionId: string,
): Promise<{ session_id: string; status: string; accepted_count: number }> {
  return evoJson(`/api/evolve/sessions/${sessionId}/finalize`, { method: "POST" });
}

// ════════════════════════════════════════════════════════════
//  单次测试（tests）
// ════════════════════════════════════════════════════════════

export interface ManualTest {
  test_id: string;
  case_id: string;
  version_type: string; // working | snapshot
  version_id: number | null;
  trace_id: string | null;
  task_id: string | null;
  status: string; // pending|running|done|failed|cancelled
  error: string | null;
  retry_of: string | null;
  origin_layer: string | null;
  created_at: string;
  // FR-003 / DEC-003：对象 trace 可用性语义（后端加法字段，旧客户端忽略不影响测试状态）。
  // available=可查看 / preparing=Trace 准备中 / unavailable=Trace 不可用，可重跑 / none=无 trace_id
  trace_availability?: "available" | "preparing" | "unavailable" | "none";
}

export interface TestAgentOption {
  type: string; // working | snapshot
  label: string;
  version?: number;
  source_commit?: string;
  change_summary?: string;
}

export async function getTests(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<{ tests: ManualTest[]; total: number; page: number; page_size: number }> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  qs.set("page", String(params?.page ?? 1));
  qs.set("page_size", String(params?.page_size ?? 20));
  return evoJson(`/api/tests?${qs.toString()}`, { method: "GET" });
}

export async function startTest(payload: {
  case_id: string;
  version_type: string;
  version_id?: number | null;
}): Promise<{ test_id: string; status: string }> {
  return evoJson(`/api/tests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getTestAgents(): Promise<{ agents: TestAgentOption[] }> {
  return evoJson(`/api/tests/agents`, { method: "GET" });
}

export async function retryTest(testId: string): Promise<{ test_id: string; status: string }> {
  return evoJson(`/api/tests/${testId}/retry`, { method: "POST" });
}

export async function stopTest(testId: string): Promise<{ status: string; test_id: string }> {
  return evoJson(`/api/tests/${testId}/stop`, { method: "POST" });
}

export async function deleteTest(testId: string): Promise<{ status: string; deleted: string }> {
  const resp = await evoFetch(`/api/tests/${testId}`, { method: "DELETE" });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

// ════════════════════════════════════════════════════════════
//  Harness 要素（snapshots/harness-elements）
// ════════════════════════════════════════════════════════════

export interface Snapshot {
  version: number;
  parent_version: number | null;
  commit_hash: string | null;
  executable: boolean;
  change_summary: string | null;
  status: string; // production | retired
  created_at: string;
}

export interface HarnessElementView {
  name: string;
  kind: string; // meta | subagent
  prompt: { body: string };
  skills: { path: string; name: string; description: string | null; content: string | null; load_error: string | null }[];
  middlewares: {
    hook: string | null; // 旧桌面客户端兼容字段；新客户端使用 hooks
    hooks: string[];
    group: string | null;
    class_name: string;
    params: Record<string, any>;
    source_path: string | null;
    description: string | null;
    optional: boolean;
  }[];
}

/** Tool 作用域——诚实反映 harness 里 tool 的真实归属（非全局即 agent） */
export type ToolScope =
  | { kind: "global" }                  // 全局注入（如进 MemoryRetriever 单例）
  | { kind: "middleware"; via: string } // 经某 middleware 暴露
  | { kind: "agent"; agent: string }    // 仅某 agent 间接受益
  | { kind: "memory" }                  // 记忆系统策略要素
  | { kind: "unknown" };                // TOOL_SCOPE_MAP 未登记，前端显示提醒

/** tools/ 目录下一个可进化 tool 文件 */
export interface ToolInfo {
  path: string;                  // 如 "tools/goal.py"
  name: string;                  // 文件名去后缀，如 "goal"
  description: string | null;    // 模块 docstring 首句
  scope: ToolScope;              // 作用域标注
  load_error: string | null;     // 解析失败时填
}

export interface HarnessElementsView {
  source_commit: string | null;
  has_source: boolean;
  agents: HarnessElementView[];
  tools: ToolInfo[]; // 顶层平级——tools/ 是全局平铺的，不属于任何 agent
  subagent_relations: { from: string; to: string; role: string }[];
}

export async function getSnapshots(): Promise<Snapshot[]> {
  return evoJson<Snapshot[]>(`/api/snapshots`, { method: "GET" });
}

export async function getHarnessElements(version: number): Promise<HarnessElementsView> {
  return evoJson<HarnessElementsView>(`/api/snapshots/${version}/harness-elements`, { method: "GET" });
}

// ── 记忆子系统要素（NWM 6 要素，独立于按 agent 分组的 HarnessElementsView） ──

/** 记忆协同链中的角色（与后端 versioning.constants.MEMORY_ROLE_ORDER 对齐） */
export type MemoryFileRole = "extract" | "store" | "retrieve" | "recall";

/** 记忆要素物理类型 */
export type MemoryElementType = "prompt" | "middleware" | "tool";

/** 单个记忆要素：NWM 协同链的一个环节 */
export interface MemoryElementView {
  name: string;
  path: string; // 相对 harness 包根，如 tools/query_builder.py
  type: MemoryElementType;
  file_role: MemoryFileRole;
  description: string;
  tags: string[]; // 恒为 ["memory"]
}

/** GET /api/snapshots/{version}/harness-elements/memory 响应 */
export interface MemoryElementsView {
  version: number;
  has_source: boolean;
  elements: MemoryElementView[]; // 已按 抽取→存储→检索→回填 排序
}

export async function getMemoryElements(version: number): Promise<MemoryElementsView> {
  return evoJson<MemoryElementsView>(`/api/snapshots/${version}/harness-elements/memory`, { method: "GET" });
}

// ── 版本源码全文（懒加载，供 Memory Tab 点击展开看要素源码） ──

/** GET /api/snapshots/{version}/source?path= 响应：单文件源码全文 */
export interface SnapshotSource {
  path: string;
  content: string;
}

/**
 * 读指定版本指定文件的源码全文。
 * path 相对 harness 包根（如 tools/narrative_schema.py）。
 * 后端按 version→commit 映射后 git show，老版本文件不存在返回 404。
 */
export async function getSnapshotSource(version: number, path: string): Promise<SnapshotSource> {
  return evoJson<SnapshotSource>(
    `/api/snapshots/${version}/source?path=${encodeURIComponent(path)}`,
    { method: "GET" },
  );
}

// ── 版本详情（含升级 diff + 改动意图，来自 GET /api/versions/{version}） ──

/** 行级 diff 的一个 hunk：equal/insert/delete 三种，无 replace（后端已拆为 del+ins） */
export interface Hunk {
  type: "equal" | "insert" | "delete";
  lines: string[];
}

/** prompt diff：hunks 序列 + 增删行数摘要 */
export interface PromptDiff {
  hunks: Hunk[];
  summary: { added: number; removed: number };
}

/** skills diff：路径集合差 */
export interface SkillsDiff {
  added: string[];
  removed: string[];
  unchanged_count: number;
}

/** middleware（processor）的单条变更 */
export interface ProcessorChange {
  key: { hook: string; group: string };
  change_type: "added" | "removed" | "modified";
  class_change: { old: string | null; new: string | null };
  params_change: { old: Record<string, any> | null; new: Record<string, any> | null };
}

/** 单个 agent 的三要素 diff。whole_agent 仅整 agent 增删时存在，常规修改缺失 */
export interface AgentDiff {
  prompt?: PromptDiff | null;
  skills?: SkillsDiff | null;
  processors?: ProcessorChange[];
  whole_agent?: "added" | "removed";
}

/** design_doc 改动意图的单条。五字段恒 string，缺失填 "" */
export interface IntentItem {
  target: string;
  change_desc: string;
  reason: string;
  expected_up: string;
  expected_down: string;
}

/** version_changes 表的投影：按 agent 聚合的客观 diff + 版本级主观意图 */
export interface VersionChanges {
  agents: { agent: string; diff: AgentDiff }[];
  intent: IntentItem[] | null;
}

/** GET /api/versions/{version} 响应（仅 harness 页需要的字段） */
export interface VersionDetail {
  version: number;
  parent_version: number | null;
  is_bootstrap: boolean;
  change_summary: string | null;
  changes: VersionChanges;
}

export async function getVersionDetail(version: number): Promise<VersionDetail> {
  return evoJson<VersionDetail>(`/api/versions/${version}`, { method: "GET" });
}

// ── 版本谱系列表（GET /api/versions）──
// 注意：后端端点仍含旧 adapt 残留字段（reward/source_round/critic_verdict），
// 此类型只取 registry 谱系字段，忽略 adapt 残留。完整 diff 待 version_changes 写入层修复后另做。

/** 版本谱系单条（只用 registry 谱系字段） */
export interface VersionListItem {
  version: number;
  parent_version: number | null;
  status: string; // production | retired
  change_summary: string | null;
  created_at: string;
  source_session: string | null;
}

/** GET /api/versions 响应 */
export interface VersionsListResponse {
  items: VersionListItem[];
  total: number;
  production_version: number;
  limit: number;
  offset: number;
}

export async function getVersions(): Promise<VersionsListResponse> {
  return evoJson<VersionsListResponse>(`/api/versions?limit=200`, { method: "GET" });
}

// ── 评测版本下拉（GET /api/benchmark/versions，Platform 账本源）──
// Phase A 后 registry.json 冻结退役，评测可选版本以 Platform 账本流水为准；
// 账本版本号与「架构代号」（如 v9 单故事专家）可能错位，note 会标注实际内容。

/** 账本版本单条（下拉项） */
export interface BenchmarkVersionItem {
  version: number;
  status: string; // production | retired
  change_summary: string;
  commit: string;
  created_at: string;
}

/** GET /api/benchmark/versions 响应 */
export interface BenchmarkVersionsResponse {
  items: BenchmarkVersionItem[];
  production_version: number | null;
  total: number;
}

export async function getBenchmarkVersions(): Promise<BenchmarkVersionsResponse> {
  return evoJson<BenchmarkVersionsResponse>(`/api/benchmark/versions`, { method: "GET" });
}

// ════════════════════════════════════════════════════════════
//  数据集（dataset）— golden/growing 评估集
// ════════════════════════════════════════════════════════════

export interface DatasetCase {
  case_id: string;
  title: string;
  layer: "golden" | "growing";
  source_trace_id: string | null;
  demand_revision: string | null;
  promoted_at: string | null;
  created_by: string;
  has_reference: boolean;
}

export interface GoldenRevision {
  revision: string;
  locked: boolean;
  intact: boolean;
  case_count: number;
  cases: string[];
}

/** 列出数据集 case（按 layer 过滤，不传=全部）。 */
export async function getDatasetCases(
  layer?: "golden" | "growing",
): Promise<{ cases: DatasetCase[]; total: number }> {
  const q = layer ? `?layer=${layer}` : "";
  return evoJson(`/api/dataset/cases${q}`, { method: "GET" });
}

/** 单 case 内容（demand.md + reference.md 全文 + 元数据）。 */
export async function getCaseContent(
  caseId: string,
  layer?: "golden" | "growing",
): Promise<{
  case_id: string;
  title: string;
  layer: string;
  demand_md: string;
  reference_md: string | null;
  source_trace_id: string | null;
  demand_revision: string | null;
  promoted_at: string | null;
  created_by: string;
  status: string;
}> {
  const q = layer ? `?layer=${layer}` : "";
  return evoJson(`/api/dataset/cases/${caseId}${q}`, { method: "GET" });
}

/** 当前 golden 集锁定的 revision + 完整性状态。 */
export async function getGoldenRevision(): Promise<GoldenRevision> {
  return evoJson<GoldenRevision>("/api/dataset/golden-revision", { method: "GET" });
}

// ════════════════════════════════════════════════════════════
//  管理后台（走 executor /api/admin/*，需 super_admin）
//  与 evolution 域不同——这些接口在 executor 上，用 apiFetch（无 /evolution-api 前缀）。
// ════════════════════════════════════════════════════════════

export interface AdminUser {
  user_id: string;
  username: string;
  is_admin: boolean;
  is_super_admin: boolean;
  disabled: boolean;
  has_api_key: boolean;
  credits_balance: number;
  workspace_count: number;
  created_at: string;
}

export interface InviteCode {
  code: string;
  created_at: string;
  is_admin_code: boolean;
  granted_credits: number;
  used: boolean;
  used_by: string | null;
  used_at: string | null;
  revoked_at: string | null;
}

export interface CreditTransaction {
  tx_id: string;
  user_id: string;
  type: string;
  amount: number;
  balance_after: number;
  ref_thread_id: string | null;
  ref_hold_id: string | null;
  note: string | null;
  created_by: string | null;
  created_at: string;
}

export interface CreditConfigItem {
  value: string;
  description: string;
  updated_at: string;
}

// ── 用户管理 ──────────────────────────────────────────────────

export async function fetchUsers(): Promise<AdminUser[]> {
  return apiJson<AdminUser[]>("/api/admin/users", { method: "GET" });
}

export async function updateUser(
  userId: string,
  payload: { disabled?: boolean },
): Promise<Record<string, unknown>> {
  return apiJson(`/api/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function resetPassword(
  userId: string,
): Promise<{ status: string; temp_password: string }> {
  return apiJson(`/api/admin/users/${userId}/reset-password`, { method: "POST" });
}

export async function adjustCredits(
  userId: string,
  amount: number,
  note: string,
): Promise<{ status: string; balance: number }> {
  return apiJson(`/api/admin/users/${userId}/credits`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount, note }),
  });
}

// ── 邀请码管理 ────────────────────────────────────────────────

export async function fetchInviteCodes(): Promise<InviteCode[]> {
  return apiJson<InviteCode[]>("/api/admin/invite-codes", { method: "GET" });
}

export async function createInviteCodes(
  count: number,
  grantedCredits: number,
): Promise<string[]> {
  return apiJson<string[]>("/api/admin/invite-codes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ count, granted_credits: grantedCredits }),
  });
}

export async function revokeInviteCode(code: string): Promise<Record<string, unknown>> {
  return apiJson(`/api/admin/invite-codes/${code}`, { method: "DELETE" });
}

// ── 积分流水 ──────────────────────────────────────────────────

export async function fetchAllTransactions(limit = 100): Promise<CreditTransaction[]> {
  return apiJson<CreditTransaction[]>(`/api/admin/credits/transactions?limit=${limit}`, {
    method: "GET",
  });
}

// ── 积分配置（暗调旋钮）──────────────────────────────────────

export async function fetchCreditsConfig(): Promise<Record<string, CreditConfigItem>> {
  return apiJson<Record<string, CreditConfigItem>>("/api/admin/credits/config", {
    method: "GET",
  });
}

export async function updateCreditsConfig(
  key: string,
  value: string,
): Promise<Record<string, unknown>> {
  return apiJson(`/api/admin/credits/config/${key}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
}

// ════════════════════════════════════════════════════════════
//  评测（benchmark v3，REQ-20260919-172934）
// ════════════════════════════════════════════════════════════

/** 批次摘要（列表项）。 */
export interface BenchmarkBatchSummary {
  batch_id: string;
  status: string; // running | done | partial | failed | cancelled
  progress: { total: number; done: number; failed: number; cancelled?: number; active: number };
  harness_version: number | null;
  golden_revision: string | null;
  rubric_version: string | null;
  judge_fp: string | null;
  concurrency: number | null;
  triggered_at: string | null;
  stop_reason?: "user_stop" | "auto_fail" | null;
}

/** 弱点报告（FR-005）。 */
export interface BenchmarkReport {
  batch_id: string;
  status: string; // ok | not_found | no_scored_data
  message?: string;
  progress?: { total: number; done: number; failed: number; active: number };
  fingerprints?: Record<string, string | number | null>;
  calibration: string;
  anchor_status: string;
  dimensions?: { dimension: string; mean: number | null; n: number }[];
  tag_hits?: { tag: string; hits: number }[];
  rule_delivery_failed?: number;
  low_cases?: {
    case_id: string;
    seed: number | null;
    overall: number | null;
    scores: Record<string, number>;
    tags: Record<string, string[]>;
    rule_delivery_passed: boolean | null;
  }[];
  failed_rows?: { case_id: string; seed: number | null; error: string | null }[];
}

/** 版本对比（FR-004，CI 三态）。 */
export interface BenchmarkCompare {
  comparable: boolean;
  problems?: string[];
  batch_a?: string;
  batch_b?: string;
  manifest_fp_a?: string | null;
  manifest_fp_b?: string | null;
  total?: {
    verdict: string; // win | tie | lose | insufficient
    reason?: string;
    n_candidate: number;
    n_production: number;
    mean_candidate: number;
    mean_production: number;
    ci_95_low: number;
    ci_95_high: number;
    delta_mean: number;
    sufficient_power: boolean;
  };
  dimensions?: Record<string, Record<string, { mean: number | null; n: number }>>;
}

/** 触发评测批次（FR-003；并发度/judge 选择为 REQ-20260920-104714 增强）。 */
export async function runBenchmark(payload: {
  version?: number;
  versions?: number[];
  seeds?: number;
  concurrency?: 1 | 3 | 5;
  judge_config_id?: number;
}): Promise<{ batch_id: string; status: string; progress: Record<string, number>; golden_revision: string }> {
  return evoJson("/api/benchmark/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** golden 升级后重跑最近 K 个版本（评测页「一键重跑」用，FR-004/DEC-009）。 */
export async function rerunGolden(
  k = 3,
): Promise<{ batch_id: string; status: string; progress: Record<string, number>; golden_revision: string }> {
  return evoJson("/api/benchmark/rerun-golden", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ k }),
  });
}

/** 停止批次（REQ-20260920-192126/FR-001：立即停 + 幂等，僵尸批次同样可清）。 */
export async function stopBenchmark(
  batchId: string,
): Promise<{ batch_id: string; status: string; stop_reason: string | null; progress: Record<string, number> }> {
  return evoJson(`/api/benchmark/batches/${batchId}/stop`, { method: "POST" });
}

/** 评分标准全文（只读展示，FR-002；结构对齐后端 rubric_v3 常量）。 */
export interface BenchmarkRubric {
  rubric_version: string;
  calibration_status: string;
  anchor_status: string;
  low_score_threshold: number;
  dimensions: {
    key: string;
    question: string;
    anchors: Record<"1" | "3" | "5", string>;
    defect_tags: string[];
  }[];
  rule_delivery: { key: string; description: string };
}

/** 当前评分标准（单一事实源在后端，防前端硬编码漂移）。 */
export async function getBenchmarkRubric(): Promise<BenchmarkRubric> {
  return evoJson<BenchmarkRubric>("/api/benchmark/rubric", { method: "GET" });
}

/** judge 候选（FR-003/DEC-010：eval+evolution，排除 executor 生产模型）。 */
export interface JudgeCandidate {
  config_id: number;
  name: string;
  model: string;
  base_url: string;
  scope: "eval" | "evolution";
  is_active: boolean;
  has_key: boolean;
  same_family_as_executor: boolean;
}

export interface JudgeDefault {
  scope: string;
  model: string;
  fingerprint: string;
  degraded: boolean;
}

export async function listJudgeCandidates(): Promise<{
  judges: JudgeCandidate[];
  default: JudgeDefault;
}> {
  return evoJson("/api/benchmark/judges", { method: "GET" });
}

/** golden 受控新增结果（FR-004/AC-007）。 */
export interface GoldenCaseCreateResult {
  case_id: string;
  golden_revision: string;
  git_commit: string;
  git_push_warning: string | null;
}

/** 受控新增 golden case（写文件 + 登记 + git 提交 + 重锁，FR-004）。 */
export async function createGoldenCase(demandMd: string): Promise<GoldenCaseCreateResult> {
  return evoJson("/api/dataset/golden/cases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ demand_md: demandMd }),
  });
}

/** 最近批次列表。 */
export async function listBenchmarkBatches(limit = 20): Promise<{ batches: BenchmarkBatchSummary[] }> {
  return evoJson(`/api/benchmark/batches?limit=${limit}`, { method: "GET" });
}

/** 弱点报告。 */
export async function getBenchmarkReport(batchId: string): Promise<BenchmarkReport> {
  return evoJson(`/api/benchmark/batches/${batchId}/report`, { method: "GET" });
}

/** 行评分明细（scores_json 结构化结果，键随 rubric 版本动态）。 */
export interface BenchmarkRunScores {
  rubric_version?: string;
  scores?: Record<string, number>;
  tags?: Record<string, string[]>;
  reasons?: Record<string, string>;
  overall?: number;
  rule_delivery?: { key?: string; passed?: boolean | null; problems?: string[] };
}

/** 批次单行（case×seed）明细（REQ-20260921-114943 FR-001/FR-004）。 */
export interface BenchmarkRunRow {
  id: number;
  case_id: string;
  seed: number | null;
  harness_version: number;
  status: string; // done | failed | pending | running | evaluating | cancelled
  retries: number;
  error: string | null;
  trace_id: string | null;
  rubric_version: string | null;
  scores: BenchmarkRunScores | null;
  ran_at: string | null;
  finished_at: string | null;
}

export interface BenchmarkRunsResponse {
  batch_id: string;
  items: BenchmarkRunRow[];
  total: number;
}

/** 批次全行明细（case 明细区数据源，DEC-004/008：全量行状态可见）。 */
export async function listBenchmarkRuns(batchId: string): Promise<BenchmarkRunsResponse> {
  return evoJson<BenchmarkRunsResponse>(
    `/api/benchmark/batches/${batchId}/runs`,
    { method: "GET" },
  );
}

/** 三件套交付索引（FR-002/DEC-002：与评分输入同口径的最新修订）。 */
export interface BenchmarkDeliveryFile {
  logical_key: string;
  content_hash: string | null;
  artifact_revision_id: string | null;
  size_bytes: number | null;
  expires_at: string | null;
  available: boolean;
}

export interface BenchmarkDeliveriesResponse {
  trace_id: string;
  /** 正文读取权限（DEC-003：超管门槛，非超管前端降级占位） */
  can_read_content: boolean;
  groups: { display: string; files: BenchmarkDeliveryFile[] }[];
}

export async function getBenchmarkDeliveries(
  traceId: string,
): Promise<BenchmarkDeliveriesResponse> {
  return evoJson<BenchmarkDeliveriesResponse>(
    `/api/benchmark/traces/${encodeURIComponent(traceId)}/deliveries`,
    { method: "GET" },
  );
}

/** 两批次 CI 三态对比。 */
export async function compareBatches(batchA: string, batchB: string): Promise<BenchmarkCompare> {
  return evoJson("/api/benchmark/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch_a: batchA, batch_b: batchB }),
  });
}
