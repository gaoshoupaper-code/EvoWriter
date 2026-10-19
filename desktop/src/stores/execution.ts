/**
 * executionStore —— 创作对话执行编排（从 home.tsx 迁移）
 *
 * 职责：消息列表、SSE 流式消费、提交/停止/重试、会话消息存取。
 *
 * 跨 store 依赖：
 * - workspaceStore：activeThreadId / activeWorkspaceId / activeThread / createThread / updateThread
 * - traceStore：traceRuns / setTraceRuns / setTraceDetail / setActiveTraceId / setLiveTraceId
 *
 * 设计约束（来自设计文档 20260710_140000）：
 * - 高频更新（model_stream/thoughts）按 reader chunk 批量同步
 * - executionPhase 存 message（与 status 并存）
 * - 思考流分段随 message 持久化（DEC-003，REQ-20261010-182114），
 *   纯函数分段逻辑见 lib/thoughts.ts
 */
import { create } from "zustand";
import { toast } from "sonner";
import type { AskUserOption, ChatMessage, ScreenplayResponse, StreamEvent, ThoughtSegment, TimelineRow, ToolStatus, TraceLogEvent, TraceRunSummary } from "@/lib/types";
import { appendReasoning, closeStreamingThought, currentTaskLabel } from "@/lib/thoughts";
import { applyRowEvent, closeStreamingRows, concatTextRows } from "@/lib/rows";
import { streamRequest } from "@/lib/stream";
import { watchPipelineAfterDisconnect } from "@/lib/usePanelPolling";
import { appendLiveTraceEvent } from "@/lib/trace";
import { derivePhaseFromMessage } from "@/lib/execution-phase";
import {
  flushThreadPersist,
  restoreThreadMessages,
  scheduleThreadPersist,
} from "@/lib/session-persist";
import {
  API_BASE_URL,
  apiFetch,
  createThread as createThreadRequest,
  fetchThreadConversation,
  fetchThreadRunState,
  updateThread as updateThreadRequest,
  trackCopy,
  trackRegenerate,
  type ThreadRunState,
} from "@/lib/api";
import { reconcileMessagesWithRunState } from "@/lib/run-reconcile";
import { finalizeAnsweredMessage } from "@/lib/hitl-finalize";

// ── 心跳超时（与原 home.tsx 一致）──
const HEARTBEAT_TIMEOUT_MS = 45_000;

// ── 并发流管理（REQ-20261019-222403 FR-006：同会话排队串行，客户端放开多流）──
// loading 升级为引用计数语义：任一写作/图像流在跑即 true；停止取消全部在途流
// （排队中的流随停止清队一起收口，服务端按 thread 清台账）。
let activeStreamCount = 0;
const activeReaders = new Set<{ cancel: () => Promise<void> }>();
let messageIdSeq = 0;

function beginStream(set: (partial: Partial<ExecutionState>) => void, kind: "writing" | "image") {
  activeStreamCount += 1;
  set({ loading: true, activeStreamKind: kind });
}

function endStream(set: (partial: Partial<ExecutionState>) => void) {
  activeStreamCount = Math.max(0, activeStreamCount - 1);
  if (activeStreamCount === 0) set({ loading: false, activeStreamKind: "" });
}

/** 用户消息台账键（FR-005）：断连补跑按同一 id 重发，服务端幂等入队。 */
function genMessageId(): string {
  const cryptoRef = (globalThis as { crypto?: Crypto }).crypto;
  if (cryptoRef?.randomUUID) return `u-${cryptoRef.randomUUID()}`;
  messageIdSeq += 1;
  return `u-${Date.now()}-${messageIdSeq}`;
}

const initialAssistantMessage: ChatMessage = {
  role: "assistant",
  content: "先选择一个工作目录，再开启或恢复创作会话。",
};

// 欢迎消息按工作区状态二选一（DEC-007）：有工作区 → 邀约式新文案；无工作区 → 保留选目录指引。
// 模块加载时 deps 未注入，store 初始值固定用 initialAssistantMessage；运行中注入/切换走本函数。
function makeWelcomeMessage(hasWorkspace: boolean): ChatMessage {
  return {
    role: "assistant",
    content: hasWorkspace ? "我们开始新的故事吧～" : "先选择一个工作目录，再开启或恢复创作会话。",
  };
}

// ── SSE 事件字段提取（从 home.tsx:86-133 迁移）──

function getToolName(event: StreamEvent) {
  return String(event.data.tool ?? "").trim() || "未知工具";
}

function getToolCallId(event: StreamEvent) {
  return String(event.data.call_id ?? "").trim();
}

function getToolParentKey(event: StreamEvent) {
  return String(event.data.parent_task_id ?? "").trim() || undefined;
}

function getSubagentName(event: StreamEvent) {
  const name = String(event.data.subagent_name ?? "").trim();
  return name || undefined;
}

function buildToolKey(toolName: string, callId: string, index: number) {
  return callId || `${toolName}-${index + 1}`;
}

function numOrNull(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() && !Number.isNaN(Number(value))) return Number(value);
  return null;
}

function getTaskFocus(event: StreamEvent) {
  const data = event.data;
  return {
    subagentType: typeof data.subagent_type === "string" ? data.subagent_type : undefined,
    iteration: numOrNull(data.iteration),
  };
}

// ── 工具状态机（从 home.tsx:135-253 迁移）──

function upsertRunningTool(tools: ToolStatus[] | undefined, event: StreamEvent) {
  const toolName = getToolName(event);
  const callId = getToolCallId(event);
  const parentKey = getToolParentKey(event);
  const subagentName = getSubagentName(event);
  const focus = getTaskFocus(event);
  const now = Date.now();
  const nextTools = [...(tools ?? [])];

  if (callId) {
    const existingIndex = nextTools.findIndex((tool) => tool.key === callId);
    if (existingIndex >= 0) {
      // 刷新已有条目：保留原 startedAt（耗时口径不重置）
      nextTools[existingIndex] = { ...nextTools[existingIndex], name: toolName, status: "running", startedAt: nextTools[existingIndex].startedAt ?? now, ...focus };
      return nextTools;
    }
    nextTools.push({ key: callId, name: toolName, status: "running", parentKey, subagentName, startedAt: now, ...focus });
    return nextTools;
  }

  nextTools.push({ key: buildToolKey(toolName, "", nextTools.length), name: toolName, status: "running", parentKey, subagentName, startedAt: now, ...focus });
  return nextTools;
}

function markToolComplete(tools: ToolStatus[] | undefined, event: StreamEvent) {
  const toolName = getToolName(event);
  const eventCallId = getToolCallId(event);
  const nextTools = [...(tools ?? [])];
  const now = Date.now();
  const markDone = (tool: ToolStatus): ToolStatus => ({ ...tool, status: "done", endedAt: now });

  if (eventCallId) {
    for (let i = 0; i < nextTools.length; i++) {
      if (nextTools[i].key === eventCallId && nextTools[i].status === "running") {
        nextTools[i] = markDone(nextTools[i]);
        return nextTools;
      }
    }
  }
  for (let i = nextTools.length - 1; i >= 0; i--) {
    if (nextTools[i].name === toolName && nextTools[i].status === "running") {
      nextTools[i] = markDone(nextTools[i]);
      return nextTools;
    }
  }
  for (let i = nextTools.length - 1; i >= 0; i--) {
    if (nextTools[i].name === toolName) {
      nextTools[i] = markDone(nextTools[i]);
      return nextTools;
    }
  }
  nextTools.push({ key: buildToolKey(toolName, "", nextTools.length), name: toolName, status: "done", startedAt: now, endedAt: now });
  return nextTools;
}

function markToolFailed(tools: ToolStatus[] | undefined, event: StreamEvent) {
  const toolName = getToolName(event);
  const eventCallId = getToolCallId(event);
  const nextTools = [...(tools ?? [])];
  const now = Date.now();
  const markFailed = (tool: ToolStatus): ToolStatus => ({ ...tool, status: "failed", endedAt: now });

  if (eventCallId) {
    for (let i = 0; i < nextTools.length; i++) {
      if (nextTools[i].key === eventCallId && nextTools[i].status === "running") {
        nextTools[i] = markFailed(nextTools[i]);
        return nextTools;
      }
    }
  }
  for (let i = nextTools.length - 1; i >= 0; i--) {
    if (nextTools[i].name === toolName && nextTools[i].status === "running") {
      nextTools[i] = markFailed(nextTools[i]);
      return nextTools;
    }
  }
  for (let i = nextTools.length - 1; i >= 0; i--) {
    if (nextTools[i].name === toolName) {
      nextTools[i] = markFailed(nextTools[i]);
      return nextTools;
    }
  }
  nextTools.push({ key: buildToolKey(toolName, "", nextTools.length), name: toolName, status: "failed", startedAt: now, endedAt: now });
  return nextTools;
}

// ── trace run 辅助（从 home.tsx:255-316 迁移）──

function runFromTraceEvent(event: TraceLogEvent): TraceRunSummary {
  const input = event.input;
  if (!input || typeof input !== "object") {
    throw new Error("Trace run_start event is missing input metadata.");
  }
  const metadata = input as Record<string, unknown>;
  return {
    trace_id: event.trace_id,
    workspace_id: String(metadata.workspace_id ?? ""),
    thread_id: String(metadata.thread_id ?? ""),
    session_name: String(metadata.session_name ?? ""),
    workspace_path: "",
    endpoint: String(metadata.endpoint ?? "unknown"),
    status: event.status,
    started_at: event.timestamp,
    ended_at: null,
    duration_ms: null,
    event_count: event.sequence,
    path: "",
    error: null,
  };
}

function fallbackRunFromTraceEvent(event: TraceLogEvent, threadId: string, workspaceId: string, threadSessionName: string, threadWorkspacePath: string): TraceRunSummary {
  return {
    trace_id: event.trace_id,
    workspace_id: workspaceId,
    thread_id: threadId,
    session_name: threadSessionName,
    workspace_path: threadWorkspacePath,
    endpoint: "screenplay.generate.stream",
    status: event.status,
    started_at: event.timestamp,
    ended_at: null,
    duration_ms: null,
    event_count: event.sequence,
    path: "",
    error: event.error ?? null,
  };
}

function upsertTraceRun(runs: TraceRunSummary[], run: TraceRunSummary) {
  return [run, ...runs.filter((item) => item.trace_id !== run.trace_id)];
}

function updateTraceRunFromEvent(run: TraceRunSummary, event: TraceLogEvent): TraceRunSummary {
  if (event.type === "run_end" || event.type === "run_error") {
    return { ...run, status: event.status, ended_at: event.timestamp, duration_ms: event.duration_ms ?? run.duration_ms, event_count: event.sequence, error: event.error ?? run.error };
  }
  return { ...run, status: event.status === "failed" ? "failed" : run.status, event_count: Math.max(run.event_count, event.sequence) };
}

// ── 消息辅助 ──

function updateAssistantMessage(
  messages: ChatMessage[],
  assistantIdx: number,
  updater: (message: ChatMessage) => ChatMessage,
) {
  const assistant = messages[assistantIdx];
  if (!assistant || assistant.role !== "assistant") {
    throw new Error("Assistant message is missing.");
  }
  const next = [...messages];
  const updated = updater(assistant);
  // T17: 每次更新 assistant message 后自动派生 executionPhase
  next[assistantIdx] = { ...updated, executionPhase: derivePhaseFromMessage(updated) };
  return next;
}

function getSessionTitle(input: string) {
  return Array.from(input.trim()).slice(0, 15).join("");
}

// ── 跨 store 依赖接口（避免循环 import）──
// executionStore 需要读写 workspace/trace store 的部分状态，
// 用 getter 注入而不是直接 import，避免模块级循环依赖。

export interface ExecutionDeps {
  // workspace getters
  getActiveThreadId: () => string;
  getActiveWorkspaceId: () => string;
  getActiveThreadSessionName: () => string;
  getActiveThreadWorkspacePath: () => string;
  getActiveWorkspaceDomain: () => string;
  // workspace actions
  setActiveThreadId: (id: string) => void;
  setThreads: (updater: (current: any[]) => any[]) => void;
  addThread: (thread: any) => void;
  // trace actions
  getTraceRuns: () => any[];
  getActiveTraceId: () => string;
  // liveTraceId = 当前正在跑的 trace（区别于 activeTraceId = 面板里选中查看的那条）。
  // 停止/重试必须用 liveTraceId，否则用户在看历史 trace 时点停止，会停成已结束的旧 trace。
  getLiveTraceId: () => string;
  setTraceRuns: (updater: any) => void;
  setTraceDetail: (updater: any) => void;
  setActiveTraceId: (id: string) => void;
  setLiveTraceId: (id: string) => void;
}

// 全局依赖注入（由 home.tsx 在初始化时注入）
let deps: ExecutionDeps | null = null;
export function setExecutionDeps(d: ExecutionDeps) { deps = d; }
function requireDeps(): ExecutionDeps {
  if (!deps) throw new Error("ExecutionDeps not injected. Call setExecutionDeps first.");
  return deps;
}

// ── Store 类型 ──

interface ExecutionState {
  // ── 持久态（写 message）──
  messages: ChatMessage[];
  prompt: string;
  loading: boolean;
  result: ScreenplayResponse | null;

  // ── 瞬态（运行中，不持久）──
  hasHistory: boolean; // T18: 当前会话是否已有历史交互（驱动记忆感开场白）
  activeStreamKind: "" | "writing" | "image"; // FR-007: 当前流类型（图片流思考态选专属文案池）

  // ── 内部 ref 等价物 ──
  streamReader: { read: () => Promise<{ done: boolean; value: Uint8Array | undefined }>; cancel: () => Promise<void> } | null;
  threadMessages: Map<string, ChatMessage[]>;

  // ── actions ──
  setPrompt: (prompt: string) => void;
  setMessages: (updater: ChatMessage[] | ((current: ChatMessage[]) => ChatMessage[])) => void;
  switchThread: (threadId: string) => void;
  loadThreadMessages: (threadId: string) => void;
  resetMessages: () => void;
  clearThreadMessages: () => void;
  submit: (promptText: string) => Promise<void>;
  resume: (resumeText: string) => Promise<void>;
  stop: () => void;
  retry: () => void;
  submitImage: (opts: { prompt: string } | { resume: import("@/lib/api").ImageReviewResume }) => Promise<void>;
}

export const useExecutionStore = create<ExecutionState>((set, get) => ({
  messages: [initialAssistantMessage],
  prompt: "",
  loading: false,
  result: null,
  hasHistory: false,
  activeStreamKind: "",
  streamReader: null,
  threadMessages: new Map(),

  setPrompt: (prompt) => set({ prompt }),

  setMessages: (updater) =>
    set((state) => ({
      messages: typeof updater === "function" ? (updater as (c: ChatMessage[]) => ChatMessage[])(state.messages) : updater,
    })),

  switchThread: (threadId) => {
    const { threadMessages, messages } = get();
    const d = requireDeps();
    // 保存旧 thread 的消息
    const prevId = d.getActiveThreadId();
    if (prevId && prevId !== threadId) {
      threadMessages.set(prevId, messages);
    }
    // 加载新 thread 的消息
    const saved = threadMessages.get(threadId);
    set({ messages: saved || [makeWelcomeMessage(!!d.getActiveWorkspaceId())] });
    d.setActiveThreadId(threadId);
  },

  loadThreadMessages: (threadId) => {
    const { threadMessages } = get();
    const saved = threadMessages.get(threadId);
    const d = requireDeps();
    if (saved) {
      set({ messages: saved });
      return;
    }
    // 内存 miss（刷新/重启后首次进入该会话）→ 本地恢复（FR-002）。
    // 先清空避免闪现上一会话内容；恢复含中断态/卡点降级管线。
    // 空数组不写盘（subscribe 侧约定），本地无记录时回退欢迎语。
    set({ messages: [] });
    void restoreThreadMessages(d.getActiveWorkspaceId(), threadId).then((restored) => {
      // 恢复期间用户可能又切走：只写回仍是当前会话的结果；
      // 恢复期间新 run 已开始（隐式切换先建会话再提交的窗口）则放弃覆盖，
      // 防止异步恢复的旧列表盖掉在途提交的消息（review P1 竞态修复）
      if (d.getActiveThreadId() !== threadId || get().loading) return;
      if (restored) {
        set({ messages: restored });
        // D-对账：本地恢复后回查服务端 run 真相，纠正断线期间分叉的
        // 「正在执行...」占位（interrupt 帧断连丢失，两端状态机各走各的）
        void reconcileRestoredRunState(set, get, threadId);
        return;
      }
      // FR-005（REQ-20261019-222403）：本地无记录（换设备/清缓存）→ 服务端
      // 会话消息日志兜底恢复；再没有才回退欢迎语。
      void restoreFromServerConversation(threadId).then((fromServer) => {
        if (d.getActiveThreadId() !== threadId || get().loading) return;
        set({ messages: fromServer || [makeWelcomeMessage(!!d.getActiveWorkspaceId())] });
        if (fromServer) void reconcileRestoredRunState(set, get, threadId);
      });
    });
  },

  resetMessages: () => {
    const d = requireDeps();
    set({ messages: [makeWelcomeMessage(!!d.getActiveWorkspaceId())] });
  },

  clearThreadMessages: () => {
    get().threadMessages.clear();
  },

  submit: async (promptText) => {
    await performSubmit(set, get, promptText);
  },

  resume: async (resumeText) => {
    await performSubmit(set, get, resumeText);
  },

  stop: () => {
    handleStopGeneration(set, get);
  },

  retry: () => {
    handleRetry(set, get);
  },

  submitImage: async (opts) => {
    await performImageStream(set, get, opts);
  },
}));

// ── 本地持久化调度（FR-002/FR-004，REQ-20261001-170627）──
//
// 订阅 messages/loading 变化：
// - 流式进行中（loading）→ 节流写中间内容（中断后已生成部分可恢复）；
// - 非流式期间消息变化（终态落地/用户消息/卡点/恢复完成）→ 立即写终版。
// 写入永远以当次快照入串行链，终版不会被在途中间版覆盖。
// 空数组跳过（恢复流程的清空瞬间、切换间隙），避免空记录覆盖既有数据。
let uninstallExecutionPersist: (() => void) | null = null;

/** 安装持久化订阅（home.tsx deps 注入后调用一次，幂等）。 */
export function installExecutionPersist() {
  if (uninstallExecutionPersist) return;
  const d = requireDeps();
  uninstallExecutionPersist = useExecutionStore.subscribe((state, prev) => {
    // 流结束（loading true→false）时 messages 引用往往不再变化，
    // 但终版必须此刻落盘（FR-002：终态写入终版，不能只靠节流延迟）
    const messagesChanged = state.messages !== prev.messages;
    const streamEnded = prev.loading && !state.loading;
    if (!messagesChanged && !streamEnded) return;
    if (!state.messages.length) return;
    const threadId = d.getActiveThreadId();
    const workspaceId = d.getActiveWorkspaceId();
    if (!threadId || !workspaceId) return;
    if (state.loading) {
      scheduleThreadPersist(workspaceId, threadId, state.messages);
    } else {
      void flushThreadPersist(state.messages, workspaceId, threadId);
    }
  });
}

/** 测试专用：卸载持久化订阅，保证用例间隔离。 */
export function _resetExecutionPersistForTests() {
  uninstallExecutionPersist?.();
  uninstallExecutionPersist = null;
}

// ── 恢复对账（D-对账）──

/**
 * 本地恢复后回查服务端最新 run 状态，纠正断线期间分叉的陈旧占位消息。
 *
 * 时序：SSE interrupt 帧丢失 → 服务端保持 awaiting_input 等 resume、
 * 客户端占位停在「正在执行...」→ 重启恢复时以此收敛到服务端真相
 * （拍板卡重建 / 终态落定 / 无记录落 stopped）。
 */
async function reconcileRestoredRunState(
  set: (partial: Partial<ExecutionState> | ((s: ExecutionState) => Partial<ExecutionState>)) => void,
  get: () => ExecutionState,
  threadId: string,
) {
  const d = requireDeps();
  // 预检：尾部消息不陈旧（已有终态/卡点）就不发请求
  const snapshot = get().messages;
  const last = snapshot[snapshot.length - 1];
  if (!last || last.role !== "assistant" || last.status || last.awaitingInput) return;
  let runState: ThreadRunState;
  try {
    runState = await fetchThreadRunState(threadId);
  } catch {
    return; // 离线/接口异常：保持本地恢复形态，不比现状差
  }
  // 竞态保护：对账往返期间切换会话或开始新 run 则放弃（纯函数内部还会再校验尾部）
  if (d.getActiveThreadId() !== threadId || get().loading) return;
  const next = reconcileMessagesWithRunState(get().messages, runState);
  if (!next) return;
  set({ messages: next });
}

// ── 核心实现（从 home.tsx:1099-1586 迁移）──

// streamRequest 返回的 reader 类型
type StreamReader = { read: () => Promise<{ done: boolean; value: Uint8Array | undefined }>; cancel: () => Promise<void> };

type SubmitOptions = {
  /** 对话气泡里展示的用户消息（默认用 promptText）。 */
  userDisplay?: string;
  /** 用户消息台账键：断连补跑时按同一 id 重发（服务端幂等入队，DEC-009）。 */
  messageId?: string;
  /** 排队补跑的内层重发（跳过 paused 预处理，防递归）。 */
  isResend?: boolean;
};

/** 排队中断待补跑的用户/助手消息对。 */
type PausedPair = { userIndex: number; content: string; messageId?: string };

function collectPausedPairs(messages: ChatMessage[]): PausedPair[] {
  const pairs: PausedPair[] = [];
  for (let i = 0; i < messages.length; i += 1) {
    if (messages[i].role !== "user") continue;
    const assistant = messages[i + 1];
    if (assistant?.role === "assistant" && assistant.queuedInterrupted) {
      pairs.push({ userIndex: i, content: messages[i].content, messageId: messages[i].messageId });
      i += 1;
    }
  }
  return pairs;
}

/** DEC-009 排队补跑：断连时排队未发的消息按序重发，旧占位对移除、原话原 id 重新入队。 */
async function resendPausedMessages(
  set: (partial: Partial<ExecutionState> | ((s: ExecutionState) => Partial<ExecutionState>)) => void,
  get: () => ExecutionState,
) {
  for (const pair of collectPausedPairs(get().messages)) {
    set((state) => ({
      messages: state.messages.filter((_, idx) => idx !== pair.userIndex && idx !== pair.userIndex + 1),
    }));
    await performSubmit(set, get, pair.content, {
      messageId: pair.messageId,
      isResend: true,
      userDisplay: pair.content,
    });
  }
}

/** FR-005：本地缓存缺失时从服务端会话消息日志恢复界面历史。 */
async function restoreFromServerConversation(threadId: string): Promise<ChatMessage[] | null> {
  try {
    const entries = await fetchThreadConversation(threadId);
    if (!entries.length) return null;
    return entries.map((entry): ChatMessage => {
      if (entry.role === "user") {
        return { role: "user", content: entry.content, messageId: entry.id };
      }
      const message: ChatMessage = {
        role: "assistant",
        content: entry.content || "",
        traceId: entry.trace_id ?? undefined,
      };
      if (entry.status === "interrupted") {
        // DEC-004：半截内容显示保留 + 标注中断（FailedView 自带重试入口）
        message.status = "failed";
        message.content = entry.content
          ? `${entry.content}\n\n⚠️ 连接中断，已生成的内容已保存，可以重试。`
          : "⚠️ 连接中断（未收到完成信号），可以重试。";
      } else if (entry.status === "completed" || entry.status === "failed" || entry.status === "stopped") {
        message.status = entry.status;
      }
      // running / queued / awaiting 不落终态：交给恢复对账按服务端 run 真相收敛
      return message;
    });
  } catch {
    return null; // 离线/接口异常：回退欢迎语，不比现状差
  }
}

async function performSubmit(
  set: (partial: Partial<ExecutionState> | ((s: ExecutionState) => Partial<ExecutionState>)) => void,
  get: () => ExecutionState,
  promptText: string,
  opts: SubmitOptions = {},
) {
  const trimmedPrompt = promptText.trim();
  if (!trimmedPrompt) return;
  // 图像流没有排队通道：生成中拦截文字提交（写作流已放开并发排队，FR-006）。
  if (get().activeStreamKind === "image") {
    toast.error("图像生成中，请稍后再发送文字消息。");
    return;
  }
  // DEC-009：先把排队中断未发的消息按序补跑（isResend 内层不再递归预处理）。
  if (!opts.isResend) await resendPausedMessages(set, get);
  const d = requireDeps();

  const messages = get().messages;
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const isResume = !!lastAssistant?.awaitingInput;
  const resumeTraceId = isResume ? lastAssistant?.traceId ?? null : null;

  // D4：resume 时定格上一条拍板卡消息——清 awaitingInput、提问沉淀为正文行、
  // 终态 answered（时间线与提问可回看，不随卡蒸发）
  const prevAwaitingInputIdx = isResume ? messages.lastIndexOf(lastAssistant!) : -1;
  if (prevAwaitingInputIdx >= 0) {
    set((state) => {
      const target = state.messages[prevAwaitingInputIdx];
      if (!target || target.role !== "assistant" || !target.awaitingInput) return {};
      const next = [...state.messages];
      next[prevAwaitingInputIdx] = finalizeAnsweredMessage(target);
      return { messages: next };
    });
  }

  // P0 修复（REQ-20261018-175526）：不以 activeThreadId 为前提——新书没有会话时
  // 首轮提交走下方自动建线程分支（对话即入口），无工作区才拦截。
  const activeWorkspaceId = d.getActiveWorkspaceId();
  if (!activeWorkspaceId) {
    toast.error("请先选择或创建一个工作目录。");
    return;
  }

  beginStream(set, "writing");
  set({ result: null });

  if (resumeTraceId) {
    d.setLiveTraceId(resumeTraceId);
    d.setTraceDetail((current: any) => {
      if (current?.run.trace_id === resumeTraceId) return current;
      const run = d.getTraceRuns().find((r: any) => r.trace_id === resumeTraceId);
      return run ? { run, events: [], nodes: [], context: [], todos: [] } : current;
    });
  } else {
    d.setLiveTraceId("");
    d.setTraceDetail(() => null);
  }

  // 保存当前会话消息
  const activeThreadId = d.getActiveThreadId();
  if (activeThreadId) {
    get().threadMessages.set(activeThreadId, get().messages);
  }

  // T18: 记忆感——检测当前会话是否已有历史交互（至少一轮 user→assistant 完成）
  // 用于 BootingView 选择记忆文案（"好的，接着上次的故事..."）
  const hasHistory = get().messages.some(
    (m) => m.role === "user",
  ) && get().messages.some(
    (m) => m.role === "assistant" && (m.status === "completed" || m.status === "failed" || m.status === "stopped"),
  );

  const assistantIdx = get().messages.length + 1;
  // 双管线泳道徽章（REQ-20261011-142529 DEC-020 第 7 条）：assistant 消息
  // 创建时从 contentStore 盖章（outline 轮询维护的当前相位）
  let pipelineAgent: string | undefined;
  try {
    const { useContentStore } = await import("./content");
    pipelineAgent = useContentStore.getState().outlinePipeline?.phase || undefined;
  } catch {
    pipelineAgent = undefined;
  }
  // FR-005 台账键：用户消息带稳定 id 随请求发出（断连补跑按 id 幂等重发）
  const messageId = opts.messageId ?? genMessageId();
  set((state) => ({
    messages: [
      ...state.messages,
      { role: "user", content: trimmedPrompt, messageId },
      {
        role: "assistant",
        content: "正在执行...",
        contentFormat: "markdown",
        tools: [],
        traceId: resumeTraceId ?? undefined,
        agent: pipelineAgent,
      },
    ],
    prompt: "",
    hasHistory,
  }));

  set({ streamReader: null });

  // 思考流分段（FR-003，REQ-20261010-182114）：帧批内累积，按 reader chunk
  // 与 streamedText 同步 flush 进 message.thoughts（随消息持久化，DEC-003）。
  // 声明在 try 外：catch 的各失败分支也要把 streaming 段标 interrupted（DEC-007）。
  let thoughtSegments: ThoughtSegment[] = [];
  let thoughtsDirty = false;
  // 关闭末尾 streaming 段；状态变化时置脏（无 streaming 段为幂等 no-op）
  const closeThoughts = (status: "done" | "interrupted") => {
    const closed = closeStreamingThought(thoughtSegments, status);
    if (closed !== thoughtSegments) {
      thoughtSegments = closed;
      thoughtsDirty = true;
    }
  };
  // 行级时间线（REQ-20261018-235653，FR-001/FR-006）：row 事件流的本地累积，
  // 与 thoughts 同一节拍 flush 进 message.rows。rowMode 一旦开启（收到首个 row 事件），
  // 正文/思考以行为真相源——旧 model_stream/reasoning_stream 增量不再消费（DEC-006 双发下
  // 避免子代理文本混入 content，FR-005），content 改由正文行拼接派生。
  let rowSegments: TimelineRow[] = [];
  let rowsDirty = false;
  let rowMode = false;
  const closeRows = (status: "interrupted" | "complete" | "failed") => {
    const closed = closeStreamingRows(rowSegments, status);
    if (closed !== rowSegments) {
      rowSegments = closed;
      rowsDirty = true;
    }
  };

  // 断连收尾文案（FR-006/DEC-009）：排队轮断连 → 标 queuedInterrupted 待补跑；
  // 执行轮断连 → 保留半截内容 + 可重试提示（FR-004）。
  const breakNote = (message: ChatMessage, note: string): string =>
    message.status === "queued"
      ? "排队中连接中断，发送新消息时会自动补发。"
      : !message.content || message.content === "正在执行..."
        ? note
        : `${message.content}\n\n${note}`;
  const queuedBreakPatch = (message: ChatMessage) =>
    message.status === "queued" ? { queuedInterrupted: true as const } : {};

  let reader: StreamReader | null = null;
  try {
    let userMessageThreadId = activeThreadId;
    const displayText = opts.userDisplay ?? trimmedPrompt;
    let shouldNameThread = d.getActiveThreadSessionName().startsWith("会话 ") ?? false;

    if (!userMessageThreadId) {
      const thread = await createThreadRequest(activeWorkspaceId, getSessionTitle(displayText));
      userMessageThreadId = thread.thread_id;
      shouldNameThread = false;
      get().threadMessages.set(thread.thread_id, get().messages);
      d.setThreads((current: any[]) => [thread, ...current.filter((item: any) => item.thread_id !== thread.thread_id)]);
      d.setActiveThreadId(thread.thread_id);
      // 阶段制（REQ-20261018-175526 FR-004；REQ-20261019-232635 FR-001）：首轮
      // 自动创建的会话绑定当前管线阶段——后续阶段变化据此触发完成卡片。绑定交给
      // stage-session 侧 ensure：管线未载（新建书竞态）时主动拉取兜底，竞态不再
      // 漏绑；失败仍退化为「早期」分组，不阻塞首轮对话。
      void import("./stage-session")
        .then(({ useStageSessionStore }) =>
          useStageSessionStore.getState().ensureThreadStageBinding(activeWorkspaceId, thread.thread_id),
        )
        .catch(() => {
          /* 阶段绑定是增强信息，失败不阻塞首轮对话 */
        });
    }

    const nextSessionName = shouldNameThread ? getSessionTitle(displayText) : "";
    if (nextSessionName) {
      updateThreadRequest(userMessageThreadId, nextSessionName)
        .then((thread) => {
          d.setThreads((current: any[]) => current.map((item: any) => (item.thread_id === thread.thread_id ? thread : item)));
        })
        .catch((renameError) => {
          toast.error(renameError instanceof Error ? renameError.message : "无法更新会话名称。");
        });
    }

    reader = (await streamRequest("/api/screenplay/generate/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: isResume
        ? { thread_id: userMessageThreadId, resume: trimmedPrompt, trace_id: resumeTraceId ?? undefined, message_id: messageId }
        : { thread_id: userMessageThreadId, prompt: trimmedPrompt, message_id: messageId },
    })) as StreamReader;
    activeReaders.add(reader);
    set({ streamReader: reader });

    const decoder = new TextDecoder();
    let buffer = "";
    let streamedText = "";
    let hasModelOutput = false;
    // FR-001（REQ-20261009-002227）：收到过 interrupt 后流关闭是服务端正常关流
    // （提问后 return，等用户选方案再 resume），断流兜底不得误报——见循环后分支。
    let gotInterrupt = false;
    let finalData: ScreenplayResponse | null = null;

    while (true) {
      let heartbeatTimer: ReturnType<typeof setTimeout> | undefined;
      const heartbeatTimeout = new Promise<never>((_, reject) => {
        heartbeatTimer = setTimeout(() => reject(new Error("HEARTBEAT_TIMEOUT")), HEARTBEAT_TIMEOUT_MS);
      });

      let done = false;
      let value: Uint8Array | undefined;
      try {
        const chunk = (await Promise.race([reader.read(), heartbeatTimeout])) as { done: boolean; value: Uint8Array | undefined };
        done = chunk.done;
        value = chunk.value;
      } catch {
        reader.cancel().catch(() => {});
        throw new Error("HEARTBEAT_TIMEOUT");
      } finally {
        if (heartbeatTimer) clearTimeout(heartbeatTimer);
      }

      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      // 按完整 SSE 帧（\n\n 分隔）解析，与 performImageStream 对齐。
      // 大帧（interrupt 提案 / final / tool_output）超过一个 TCP 分段时，
      // event: 行与 data: 行会落在不同 chunk——旧实现按行切分且 eventType 每
      // chunk 重置，data: 行因 eventType 为空被静默丢帧（线上 interrupt 从未
      // 到达 UI 的根因，2026-10-09 彻查）。
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";

      for (const frame of frames) {
        const lines = frame.split("\n");
        let eventType = "";
        let eventData = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) eventType = line.slice(7).trim();
          else if (line.startsWith("data: ")) eventData += line.slice(6);
        }
        if (!eventType || !eventData) continue; // 心跳注释帧（: ping）等无数据帧
        try {
          const event = { type: eventType as StreamEvent["type"], data: JSON.parse(eventData) as Record<string, unknown> };

          if (event.type === "model_stream") {
            if (!rowMode) streamedText += String(event.data.content ?? "");
          } else if (event.type === "reasoning_stream") {
            // FR-003：reasoning token 续写末段或开新段；label 快照自当前 tools。
            // 行模式（FR-001）下思考走行事件，旧增量不消费——双发去重（DEC-006）。
            if (!rowMode) {
              const label = currentTaskLabel(get().messages[assistantIdx]?.tools);
              thoughtSegments = appendReasoning(thoughtSegments, String(event.data.content ?? ""), label);
              thoughtsDirty = true;
            }
          } else if (event.type === "row") {
            // 行事件（FR-001）：首个 row 事件起 rows 成为消息真相源
            rowMode = true;
            rowSegments = applyRowEvent(rowSegments, event.data);
            rowsDirty = true;
          } else if (event.type === "trace_event") {
            const traceEvent = event.data as TraceLogEvent;
            if (traceEvent.type === "run_start") {
              set((state) => ({
                messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({ ...message, traceId: traceEvent.trace_id })),
              }));
            }
            d.setTraceRuns((current: any) => {
              if (traceEvent.type === "run_start") return upsertTraceRun(current, runFromTraceEvent(traceEvent));
              return current.map((run: any) => (run.trace_id === traceEvent.trace_id ? updateTraceRunFromEvent(run, traceEvent) : run));
            });
            d.setTraceDetail((current: any) => {
              const fallbackRun = current?.run.trace_id === traceEvent.trace_id
                ? current.run
                : d.getTraceRuns().find((run: any) => run.trace_id === traceEvent.trace_id) ??
                  fallbackRunFromTraceEvent(traceEvent, userMessageThreadId, activeWorkspaceId, d.getActiveThreadSessionName(), d.getActiveThreadWorkspacePath());
              return appendLiveTraceEvent(current, traceEvent, fallbackRun);
            });
            d.setActiveTraceId(traceEvent.trace_id);
            d.setLiveTraceId(traceEvent.trace_id);
            if (traceEvent.type === "run_end" || traceEvent.type === "run_error") {
              d.setLiveTraceId("");
            }
          } else if (event.type === "trace_snapshot") {
            const detail = event.data as any;
            d.setTraceDetail(() => detail);
            d.setTraceRuns((current: any) => upsertTraceRun(current, detail.run));
            d.setActiveTraceId(detail.run.trace_id);
            d.setLiveTraceId(detail.run.status === "running" ? detail.run.trace_id : "");
          } else if (event.type === "model_output") {
            hasModelOutput = true;
          } else if (event.type === "status") {
            // 会话编排状态（REQ-20261019-222403）：排队/开跑/截断提示/清队/台账失败
            const s = String((event.data as { status?: string }).status ?? "");
            if (s === "queued") {
              set((state) => ({
                messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
                  ...message,
                  status: "queued",
                  content: "排队中…前面的生成完成后自动开始",
                })),
              }));
            } else if (s === "started") {
              set((state) => ({
                messages: updateAssistantMessage(state.messages, assistantIdx, (message) =>
                  message.status === "queued"
                    ? { ...message, status: undefined, content: "正在执行..." }
                    : message,
                ),
              }));
            } else if (s === "history_truncated") {
              // FR-007：一次性提示（聊天记录仍完整，仅模型上下文裁剪）
              const dropped = Number((event.data as { dropped_turns?: number }).dropped_turns ?? 0);
              toast.info(`对话历史过长：已把最早的 ${dropped} 轮移出上下文（聊天记录仍完整保留）`);
            } else if (s === "cancelled") {
              // FR-006 停止清队：排队轮未开始即作废（服务端台账同步 cancelled）
              set((state) => ({
                messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
                  ...message,
                  status: "stopped",
                  content: "已停止（排队中，未开始生成）。",
                })),
              }));
              break;
            } else if (s === "log_failed") {
              const note = `⚠️ ${String((event.data as { error?: string }).error ?? "会话日志写入失败，本轮未发送")}`;
              toast.error(note);
              set((state) => ({
                messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
                  ...message,
                  status: "failed",
                  content: note,
                })),
              }));
              break;
            }
          } else if (event.type === "tool_call") {
            set((state) => ({
              messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({ ...message, tools: upsertRunningTool(message.tools, event) })),
            }));
            // FR-003：tool 事件是思考段边界——当前段收作 done，下个 reasoning 开新段
            closeThoughts("done");
          } else if (event.type === "tool_output") {
            set((state) => ({
              messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({ ...message, tools: markToolComplete(message.tools, event) })),
            }));
            closeThoughts("done");
          } else if (event.type === "tool_error") {
            set((state) => ({
              messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({ ...message, tools: markToolFailed(message.tools, event) })),
            }));
            closeThoughts("done");
          } else if (event.type === "final") {
            finalData = event.data as ScreenplayResponse;
          } else if (event.type === "credit_exhausted") {
            const msg = (event.data as { message?: string })?.message ?? "积分耗尽";
            d.setLiveTraceId("");
            closeThoughts("interrupted");
            closeRows("interrupted");
            toast.error(msg);
            const note = `⚠️ ${msg}\n\n已创作的内容已保存，补充积分后可继续创作。`;
            set((state) => ({
              messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
                ...message,
                status: "failed",
                thoughts: thoughtSegments,
                ...(rowSegments.length ? { rows: rowSegments } : {}),
                content: !message.content || message.content === "正在执行..." ? note : `${message.content}\n\n${note}`,
                contentFormat: "markdown",
              })),
            }));
            break;
          } else if (event.type === "pipeline") {
            // DEC-013（REQ-20261018-175526）：阶段事件即时写入 contentStore，
            // 阶段条/浮窗/切换检测共用同源 state；outline 轮询是产物文件与断连兜底通道。
            // 畸形载荷（缺 kind/phase）直接丢弃——阶段条/浮窗/挂起卡片保持最后已知状态。
            const p = event.data as Partial<import("@/lib/types").PipelineStatus> | null;
            if (p && typeof p.kind === "string" && typeof p.phase === "string") {
              try {
                const { useContentStore } = await import("./content");
                useContentStore.getState().setOutlinePipeline(p as import("@/lib/types").PipelineStatus);
              } catch {
                /* 观测事件不阻断流 */
              }
            }
          } else if (event.type === "interrupt") {
            const iv = event.data as {
              kind?: string; question?: string; options?: AskUserOption[] | null; multi_select?: boolean; source?: string;
              round?: number; versions?: unknown[];
            };
            gotInterrupt = true;
            const interruptKind = iv.kind ?? "choice";
            // DEC-007：提问中断——末段思考标 interrupted（时间线保留到中断点）
            closeThoughts("interrupted");
            closeRows("interrupted");
            set((state) => ({
              messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
                ...message,
                // FR-004（REQ-20261018-235653）：行模式下正文不被提问覆盖——
                // 已输出行原样保留，提问由 HITL 卡展示；旧管线维持提问写正文的既有行为。
                content: rowMode
                  ? message.content
                  : interruptKind === "image_review"
                    ? `第 ${iv.round ?? "?"} 轮图像评审：3 版 6 图已生成，请打分`
                    : iv.question || "等待你的输入",
                ...(rowSegments.length ? { rows: rowSegments } : {}),
                thoughts: thoughtSegments,
                awaitingInput: {
                  kind: interruptKind, question: iv.question || "", options: iv.options ?? null,
                  multi_select: iv.multi_select ?? false, source: iv.source, round: iv.round, versions: iv.versions,
                  askedAt: new Date().toISOString(),
                },
              })),
            }));
          }
        } catch {
          // partial JSON
        }
      }

      set((state) => ({
        messages: updateAssistantMessage(state.messages, assistantIdx, (message) => {
          // FR-001（REQ-20260909-002227）：收到 interrupt 后正文归 interrupt 分支管
          // （提案问题），流式叙述文本不得把它盖回去；思考段照常写入。
          let next = message;
          if (thoughtsDirty) next = { ...next, thoughts: thoughtSegments };
          if (rowsDirty) next = { ...next, rows: rowSegments };
          if (rowMode) {
            // 行模式：content 由正文行拼接派生（含中间正文，FR-002/FR-003），
            // 待答期间（awaitingInput）不动 content
            if (!next.awaitingInput) {
              next = { ...next, content: concatTextRows(rowSegments), contentFormat: "markdown" };
            }
          } else if (!next.awaitingInput && streamedText) {
            next = { ...next, content: streamedText, contentFormat: "markdown" };
          }
          return next;
        }),
      }));
      thoughtsDirty = false;
      rowsDirty = false;
    }

    if (finalData) {
      set({ result: finalData });
      d.setThreads((current: any[]) =>
        current.map((thread: any) => (thread.thread_id === finalData!.thread_id ? { ...thread, updated_at: new Date().toISOString() } : thread)),
      );
      closeThoughts("done");
      closeRows("complete");
      const fallback = `已生成《${finalData.session_name}》的故事材料，工作目录是 ${finalData.workspace_path}`;
      set((state) => ({
        messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
          ...message,
          status: "completed",
          thoughts: thoughtSegments,
          ...(rowSegments.length ? { rows: rowSegments } : {}),
          // 行模式：正文行拼接即最终正文（FR-003 交错展示，不垫底）
          content: (rowMode ? concatTextRows(rowSegments) : streamedText) || fallback,
          contentFormat: "markdown",
        })),
      }));
    } else {
      // FR-002 断流兜底：SSE 正常关闭但未收到 final——落 failed 终态，不永挂思考态。
      // 幂等：credit_exhausted 等分支 break 前已设终态，这里不覆盖。
      // FR-001（REQ-20261009-002227）：收到过 interrupt 的关流是服务端正常结束
      // （提问后关流等 resume），保持 awaitingInput，不误报断连、不落 failed。
      const current = get().messages[assistantIdx];
      // queued 是非终态（排队中），断流同样要收口（FR-006/DEC-009：标待补跑）
      const rowTerminal = !!(current && current.status && current.status !== "queued");
      if (current && current.role === "assistant" && !rowTerminal && !gotInterrupt) {
        const brokenMessage = "⚠️ 连接中断（未收到完成信号），已生成的内容已保存，可以重试。";
        d.setLiveTraceId("");
        closeThoughts("interrupted");
        closeRows("interrupted");
        toast.error(brokenMessage);
        // FR-004 失败语义：未收到 final 的关流，服务端可能仍在跑——低频兜底观察
        watchPipelineAfterDisconnect(activeWorkspaceId);
        set((state) => ({
          messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
            ...message,
            status: "failed",
            thoughts: thoughtSegments,
            ...(rowSegments.length ? { rows: rowSegments } : {}),
            content: breakNote(message, brokenMessage),
            contentFormat: "markdown",
            ...queuedBreakPatch(message),
          })),
        }));
      }
    }
  } catch (submitError) {
    const errMsg = submitError instanceof Error ? submitError.message : "";
    if (errMsg.includes("积分") || errMsg.includes("403") || errMsg.includes("冻结")) {
      d.setLiveTraceId("");
      closeThoughts("interrupted");
      closeRows("interrupted");
      toast.error("积分余额不足，账户已冻结。请联系管理员补充积分。");
      const creditNote = "⚠️ 积分余额不足，无法开始创作。请联系管理员补充积分。";
      set((state) => ({
        messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
          ...message, status: "failed", thoughts: thoughtSegments,
          ...(rowSegments.length ? { rows: rowSegments } : {}),
          content: !message.content || message.content === "正在执行..." ? creditNote : `${message.content}\n\n${creditNote}`, contentFormat: "markdown",
        })),
      }));
      if (isResume) throw submitError;
      return;
    }
    if (submitError instanceof DOMException && submitError.name === "AbortError") {
      d.setLiveTraceId("");
      closeThoughts("interrupted");
      closeRows("interrupted");
      set((state) => ({
        messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
          ...message, status: "stopped", thoughts: thoughtSegments,
          ...(rowSegments.length ? { rows: rowSegments } : {}),
          content: !message.content || message.content === "正在执行..." ? "已手动停止。" : `${message.content}\n\n已手动停止。`, contentFormat: "markdown",
        })),
      }));
      if (isResume) throw submitError;
      return;
    }

    if (submitError instanceof Error && submitError.message === "HEARTBEAT_TIMEOUT") {
      const heartbeatMessage = `连接已断开（超过 ${HEARTBEAT_TIMEOUT_MS / 1000} 秒未收到数据），请重试。`;
      d.setLiveTraceId("");
      closeThoughts("interrupted");
      closeRows("interrupted");
      toast.error(heartbeatMessage);
      // FR-004 失败语义：断连后服务端可能仍在跑——低频轮询兜底观察阶段推进
      watchPipelineAfterDisconnect(activeWorkspaceId);
      set((state) => ({
        messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
          ...message, status: "failed", thoughts: thoughtSegments,
          ...(rowSegments.length ? { rows: rowSegments } : {}),
          content: breakNote(message, `⚠️ ${heartbeatMessage}`), contentFormat: "markdown",
          ...queuedBreakPatch(message),
        })),
      }));
      if (isResume) throw submitError;
      return;
    }

    closeThoughts("interrupted");
    closeRows("interrupted");
    toast.error(submitError instanceof Error ? submitError.message : "Unexpected request failure.");
    set((state) => ({
      messages: updateAssistantMessage(state.messages, assistantIdx, (message) => ({
        ...message, status: "failed", thoughts: thoughtSegments,
        ...(rowSegments.length ? { rows: rowSegments } : {}),
        content: breakNote(message,
          submitError instanceof Error ? submitError.message : "请求失败了。检查后端是否启动后，可以在同一个会话里重试。"),
        contentFormat: "markdown",
        ...queuedBreakPatch(message),
      })),
    }));
    if (isResume) throw submitError;
  } finally {
    if (reader) activeReaders.delete(reader);
    endStream(set);
    set({ streamReader: null });
  }
}

function handleStopGeneration(set: any, get: () => ExecutionState) {
  const d = requireDeps();
  const activeThreadId = d.getActiveThreadId();
  const liveTraceId = d.getLiveTraceId();

  if (activeThreadId) {
    // FR-006 停止=清空队列：始终发 stop（无 live trace 时 trace_id 传空，
    // 服务端幂等——正主由 trace 级取消处理，门外排队者由清队分支收口）。
    apiFetch(`${API_BASE_URL}/api/screenplay/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: activeThreadId, trace_id: liveTraceId || "" }),
    }).catch(() => {});
  }
  get().streamReader?.cancel().catch(() => {});
  // 并发多流（排队）：本地全部取消，行内各自收口（排队行收到 cancelled 帧或
  // 断流兜底落 stopped）
  for (const r of Array.from(activeReaders)) {
    void r.cancel().catch(() => {});
  }
}

function handleRetry(set: any, get: () => ExecutionState) {
  const d = requireDeps();
  if (get().loading) return; // FR-003：生成中禁用重试（排队/执行中都不重发）
  const liveTraceId = d.getLiveTraceId();
  if (liveTraceId) {
    trackRegenerate(liveTraceId);
  }
  const messages = get().messages;
  // 尾部是排队中断对：补跑即重试（resendPausedMessages 会按序补发全部，
  // 避免同话术双发）
  const lastUserIdx = messages.map((m) => m.role).lastIndexOf("user");
  const pairAssistant = messages[lastUserIdx + 1];
  if (pairAssistant?.role === "assistant" && pairAssistant.queuedInterrupted) {
    void resendPausedMessages(set, get);
    return;
  }
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  if (lastUser?.content) {
    void performSubmit(set, get, lastUser.content);
  }
}

async function performImageStream(
  set: (partial: Partial<ExecutionState> | ((s: ExecutionState) => Partial<ExecutionState>)) => void,
  get: () => ExecutionState,
  opts: { prompt: string } | { resume: import("@/lib/api").ImageReviewResume },
) {
  const d = requireDeps();
  const activeThreadId = d.getActiveThreadId();
  if (!activeThreadId || get().loading) return;
  const isResume = "resume" in opts;

  if (isResume) {
    const lastAssistant = [...get().messages].reverse().find((m) => m.role === "assistant");
    if (!lastAssistant?.awaitingInput) return;
    set((state) => {
      const idx = state.messages.findIndex((m) => m === lastAssistant);
      if (idx < 0) return {};
      const next = [...state.messages];
      next[idx] = { ...lastAssistant, awaitingInput: undefined };
      return { messages: next };
    });
  }

  beginStream(set, "image");
  set({ streamReader: null });

  if (!isResume) {
    set((state) => ({ messages: [...state.messages, { role: "user", content: opts.prompt, contentFormat: "text" }] }));
  }
  set((state) => ({
    messages: [...state.messages, { role: "assistant", content: isResume ? "正在优化..." : "正在生成...", contentFormat: "markdown", tools: [] }],
  }));

  let reader: StreamReader | null = null;
  try {
    const body = isResume ? { thread_id: activeThreadId, prompt: "", resume: opts.resume } : { thread_id: activeThreadId, prompt: opts.prompt };
    reader = (await streamRequest("/api/image/generate/stream", {
      method: "POST", headers: { "Content-Type": "application/json" }, body,
    })) as StreamReader;
    activeReaders.add(reader);
    set({ streamReader: reader });

    const decoder = new TextDecoder();
    let buffer = "";
    let streamedText = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";

      for (const evt of events) {
        const lines = evt.split("\n");
        let eventType = "";
        let eventData = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) eventType = line.slice(7);
          else if (line.startsWith("data: ")) eventData += line.slice(6);
        }
        if (!eventType) continue;
        try {
          const event = { type: eventType, data: JSON.parse(eventData) };
          if (event.type === "model_stream") {
            streamedText += (event.data as { content?: string }).content ?? "";
            set((state) => ({
              messages: updateAssistantMessage(state.messages, state.messages.length - 1, (m) => ({ ...m, content: streamedText, contentFormat: "markdown" })),
            }));
          } else if (event.type === "interrupt") {
            const iv = event.data as { kind?: string; round?: number; versions?: unknown[]; question?: string; options?: AskUserOption[] | null; multi_select?: boolean; source?: string };
            const interruptKind = iv.kind ?? "choice";
            set((state) => ({
              messages: updateAssistantMessage(state.messages, state.messages.length - 1, (m) => ({
                ...m,
                content: interruptKind === "image_review" ? `第 ${iv.round ?? "?"} 轮图像评审：请打分` : iv.question || "等待输入",
                awaitingInput: {
                  kind: interruptKind, question: iv.question || "", options: iv.options ?? null,
                  multi_select: iv.multi_select ?? false, source: iv.source, round: iv.round, versions: iv.versions,
                  askedAt: new Date().toISOString(),
                } as typeof m.awaitingInput,
              })),
            }));
            return;
          } else if (event.type === "final") {
            const data = event.data as { content?: string };
            set((state) => ({
              messages: updateAssistantMessage(state.messages, state.messages.length - 1, (m) => ({
                ...m, status: "completed", content: data.content ?? "完成", contentFormat: "markdown",
              })),
            }));
            return;
          } else if (event.type === "error") {
            const errText = (event.data as { error?: string }).error ?? "image stream 出错";
            toast.error(errText);
            set((state) => ({
              messages: updateAssistantMessage(state.messages, state.messages.length - 1, (m) => ({
                ...m, status: "failed", content: `⚠️ ${errText}`, contentFormat: "markdown",
              })),
            }));
            return;
          }
        } catch {
          // partial JSON
        }
      }
    }

    // FR-003 图片流断流兜底：循环正常结束（无 final/interrupt/error 提前 return）→
    // 落 failed 终态，不停留在「正在生成...」占位（booting 永挂变体）。
    const brokenMessage = "⚠️ 连接中断（未收到完成信号），可以重试。";
    toast.error(brokenMessage);
    set((state) => ({
      messages: updateAssistantMessage(state.messages, state.messages.length - 1, (m) => ({
        ...m,
        status: "failed",
        content: m.content === "正在生成..." || m.content === "正在优化..."
          ? brokenMessage
          : `${m.content}\n\n${brokenMessage}`,
        contentFormat: "markdown",
      })),
    }));
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "image stream 失败");
  } finally {
    if (reader) activeReaders.delete(reader);
    endStream(set);
    set({ streamReader: null });
  }
}
