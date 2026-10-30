/**
 * stageSessionStore —— 阶段制会话管理（REQ-20261018-175526 + REQ-20261019-232617）。
 *
 * 段间切换人工闸（232617 FR-002）：切段只由本 store 的确认动作发起——
 * confirmTransition/jumpToStage 先调服务端人工段指针 API，再归档旧会话、
 * 开新会话；打字发送不再隐式切换（AC-002），产物落盘只亮「就绪卡片」。
 *
 * 核心不变量：
 *   当前段产物齐全（pipeline.ready）且会话绑定的段 = 当前段 → 挂就绪卡片
 *   「X 已齐全，进入 Y」，用户点卡片才写指针、归档旧会话、开新会话；
 *   会话绑定段 ≠ 管线段（跨窗口/恢复路径）→ 同一卡片作恢复入口。
 *
 * 就绪通知（FR-001/DEC-002）：ready 未就绪→就绪 的边沿触发一次系统通知
 * （应用后台可见）；启动首见不算边沿（长就绪的书打开不弹）。
 *
 * 阶段信号源（DEC-013）：contentStore.outlinePipeline——SSE pipeline 事件即时写 +
 * outline 轮询兜底写，两个通道收敛到同一 state，本 store 只做对比。
 *
 * 阶段归属补绑三件套（REQ-20261019-232635，修「点主题看不到历史」竞态）：
 * - ensureThreadStageBinding：首轮建会话绑定，管线未载时拉取兜底（FR-001）；
 * - confirmTransition 归档补绑：旧会话无标签按 fromStage 补贴（FR-002）；
 * - remediateUnboundThreads：存量受害会话按创建时间锚点归位「主题」（FR-003）。
 */
import { create } from "zustand";
import { toast } from "sonner";
import type { PipelineStatus, ThreadSummary } from "@/lib/types";
import {
  createThread as createThreadRequest,
  fetchWorkspaceOutline,
  fetchWritingMode,
  updateWritingStage,
  advanceWritingStage,
  updateWritingMode,
} from "@/lib/api";
import { notifyStageReady } from "@/lib/notify";
import { finalizeAnsweredMessage } from "@/lib/hitl-finalize";
import { PIPELINE_STEPS, stageLabel } from "@/lib/pipeline-steps";
import {
  readStageMetas,
  saveThreadStageMeta,
  type StageThreadMeta,
} from "@/lib/session-persist";

export type { StageThreadMeta };
import { useWorkspaceStore } from "./workspace";
import { useContentStore } from "./content";
import { useExecutionStore } from "./execution";

/** 挂起的阶段切换（完成卡片数据源）。 */
export interface StageTransition {
  fromStage: string;
  toStage: string;
  /** 跳段申请来源（后退切换才有）：卡片标注「应 X 阶段申请」 */
  sourceStage?: string;
}

/** 历史浏览态：stage 指定阶段列表；threadId 非空 = 只读查看该会话。 */
export interface StageHistoryView {
  stage: string;
  threadId: string | null;
}

function stageOrder(stage: string): number {
  return PIPELINE_STEPS.findIndex((s) => s.phase === stage);
}

/** 就绪边沿记录（FR-001 AC-004）：workspaceId → 上次信号的 ready 值。 */
const readyEdge: Map<string, boolean> = new Map();

/** 就绪边沿 → 系统通知一次；启动首见（undefined）不算边沿。 */
function maybeNotifyReady(pipeline: PipelineStatus, workspaceId: string): void {
  const now = pipeline.ready?.ready === true;
  const prev = readyEdge.get(workspaceId);
  readyEdge.set(workspaceId, now);
  const next = pipeline.ready?.next ?? null;
  if (now && prev === false && next) {
    void notifyStageReady(stageLabel(pipeline.phase) ?? "当前段", stageLabel(next) ?? "下一段");
  }
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** 历史显示名：「主题 · 10-18 15:02」，跳段往返带「（正文申请）」。 */
export function stageThreadDisplayName(meta: StageThreadMeta): string {
  const label = stageLabel(meta.stage) ?? "早期";
  const d = new Date(meta.boundAt);
  const time = Number.isNaN(d.getTime())
    ? ""
    : ` · ${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const source = meta.sourceStage ? `（${stageLabel(meta.sourceStage) ?? "早期"}申请）` : "";
  return `${label}${time}${source}`;
}

/** 新会话的后端 session_name（列表对账用；界面显示名走 stageThreadDisplayName）。 */
function newThreadName(stage: string): string {
  return `${stageLabel(stage) ?? "创作"}对话`;
}

interface StageSessionState {
  /** thread_id → 阶段归属（内存镜像 + session-persist 落盘） */
  threadStageMeta: Map<string, StageThreadMeta>;
  pendingTransition: StageTransition | null;
  /** 切换/重开的网络往返防重标志（review P2）：进行中直接拒绝并发建会话 */
  transitioning: boolean;
  historyView: StageHistoryView | null;

  // ── 创作双模式（REQ-20261026-191719 FR-008）──────────────────────
  /** 段级模式快照（服务端真相；null=未加载，一律按 interactive 处理） */
  stageModes: Record<string, string> | null;
  /** 可开自动的段（volume/chapter/text；题材/总纲锁死逐步） */
  autoEligibleStages: string[];
  /** 正文批次章数（DEC-007：审查卡现场指定后回写） */
  batchSize: number;
  /** 上一批正文实际消耗（FR-003/DEC-014：批次审查卡显示；null=占位） */
  lastBatchUsage: { chapters: number; totalTokens: number } | null;
  /** 读模式快照（切书/管线载入时调；失败静默按 interactive） */
  hydrateModes: (workspaceId: string) => Promise<void>;
  /** 模式开关（唯一写通道）：写服务端并本地对齐；失败 toast 不改本地 */
  toggleStageMode: (workspaceId: string, stage: string) => Promise<void>;
  /**
   * 段审确认：服务端推进指针，跨段必须先开新会话；同段才保留原审查续跑。
   * 失败返回 false，保留旧对话供重试，不向旧会话发送新阶段消息。
   */
  confirmStageReviewAdvance: (workspaceId: string, traceId: string, reviewId?: string) => Promise<boolean>;

  /** 启动/切书时恢复该工作区全部会话的阶段归属（FR-012）。 */
  hydrateForWorkspace: (workspaceId: string) => Promise<void>;
  bindThreadStage: (workspaceId: string, threadId: string, stage: string, sourceStage?: string) => void;
  /** 首轮建会话的阶段绑定（REQ-20261019-232635 FR-001）：管线未载时拉取兜底再绑，竞态不漏绑；失败静默。 */
  ensureThreadStageBinding: (workspaceId: string, threadId: string) => Promise<void>;
  /** 存量竞态受害会话归位（REQ-20261019-232635 FR-003）：早于该书最早有标签会话的无标签会话补绑「主题」。 */
  remediateUnboundThreads: (pipeline: PipelineStatus | null) => void;
  /** 不变量检测：就绪信号 / 管线段 vs 会话绑定段。由 home 的 effect 在信号变化时调。 */
  syncWithPipeline: (pipeline: PipelineStatus | null) => void;
  /** 就绪卡片主按钮：写人工段指针、归档旧会话、建新会话、清空对话区（FR-002）。 */
  confirmTransition: () => Promise<boolean>;
  /** 阶段条跳段/回退：写人工段指针并开目标段新会话（FR-004 统一入口）。 */
  jumpToStage: (stage: string) => Promise<void>;
  /** 重开逃生门：同阶段新会话（FR-007）。 */
  restartThread: () => Promise<void>;
  openStageHistory: (stage: string) => void;
  openHistoryThread: (threadId: string) => void;
  closeHistory: () => void;
}

export const useStageSessionStore = create<StageSessionState>((set, get) => ({
  threadStageMeta: new Map(),
  pendingTransition: null,
  transitioning: false,
  historyView: null,
  stageModes: null,
  autoEligibleStages: [],
  batchSize: 5,
  lastBatchUsage: null,

  hydrateModes: async (workspaceId) => {
    if (!workspaceId) return;
    try {
      const info = await fetchWritingMode(workspaceId);
      set({
        stageModes: info.stage_modes,
        autoEligibleStages: info.auto_eligible ?? [],
        batchSize: info.batch_size ?? 5,
      });
    } catch {
      // 失败语义（FR-008）：模式不可知时一律按 interactive 呈现，不阻塞创作
      set({ stageModes: null });
    }
  },

  toggleStageMode: async (workspaceId, stage) => {
    const current = get().stageModes?.[stage] ?? "interactive";
    const next = current === "auto" ? "interactive" : "auto";
    try {
      const info = await updateWritingMode(workspaceId, stage, next);
      set({
        stageModes: info.stage_modes,
        autoEligibleStages: info.auto_eligible ?? [],
        ...(stage === "text" ? { batchSize: info.batch_size ?? get().batchSize } : {}),
      });
    } catch (toggleError) {
      toast.error(toggleError instanceof Error ? toggleError.message : "模式切换失败，请重试。");
    }
  },

  confirmStageReviewAdvance: async (workspaceId, traceId, reviewId) => {
    const ws = useWorkspaceStore.getState();
    const activeThreadId = ws.activeThreadId;
    if (get().transitioning || useExecutionStore.getState().loading
      || ws.activeWorkspaceId !== workspaceId || !activeThreadId) return false;
    const currentMeta = get().threadStageMeta.get(activeThreadId);
    const fromStage = currentMeta?.stage ?? useContentStore.getState().outlinePipeline?.phase;
    const stillCurrent = () => {
      const current = useWorkspaceStore.getState();
      return current.activeWorkspaceId === workspaceId && current.activeThreadId === activeThreadId;
    };
    set({ transitioning: true });
    try {
      // 提前补绑旧段，保证推进成功、建会话失败后的重试仍能识别跨段。
      if (!currentMeta && fromStage) get().bindThreadStage(workspaceId, activeThreadId, fromStage);
      const status = await advanceWritingStage(workspaceId, traceId, reviewId);
      if (!stillCurrent()) return false;
      if (!status || status.kind !== "five" || !stageLabel(status.phase)) {
        throw new Error("未收到有效的目标阶段，请重试。");
      }
      useContentStore.getState().setOutlinePipeline(status);
      readyEdge.set(workspaceId, status.ready?.ready === true);
      if (status.phase === fromStage) return true;
      set({ pendingTransition: { fromStage: fromStage ?? "", toStage: status.phase } });
      const thread = await createThreadRequest(workspaceId, newThreadName(status.phase));
      get().bindThreadStage(workspaceId, thread.thread_id, status.phase);
      if (!stillCurrent()) return false;
      useExecutionStore.getState().setMessages((messages) => messages.map((message) =>
        message.awaitingInput?.review_kind === "stage" && message.traceId === traceId
          && (!reviewId || message.awaitingInput.review_id === reviewId)
          ? finalizeAnsweredMessage(message) : message,
      ));
      useExecutionStore.getState().switchThread(thread.thread_id);
      ws.setThreads((current) => [thread, ...current.filter((t) => t.thread_id !== thread.thread_id)]);
      set({ pendingTransition: null, historyView: null, lastBatchUsage: null });
      return true;
    } catch (advanceError) {
      toast.error(advanceError instanceof Error ? advanceError.message : "段间推进失败，请重试。");
      return false;
    } finally {
      set({ transitioning: false });
    }
  },

  hydrateForWorkspace: async (workspaceId) => {
    if (!workspaceId) return;
    // 切书即换上下文：浏览态与挂起卡片不得跨书携带；就绪边沿重置（换书首见不算边沿）
    set({ historyView: null, pendingTransition: null, stageModes: null, lastBatchUsage: null });
    readyEdge.delete(workspaceId);
    void get().hydrateModes(workspaceId);
    const metas = await readStageMetas(workspaceId);
    set((state) => {
      const next = new Map(state.threadStageMeta);
      for (const [threadId, meta] of Object.entries(metas)) next.set(threadId, meta);
      return { threadStageMeta: next };
    });
    // FR-012：hydrate 与管线信号是两个异步通道，到达顺序无保证——
    // 读入后主动重算一次不变量，挂起卡片不因 meta 晚到而丢失（review P2 修复）
    get().syncWithPipeline(useContentStore.getState().outlinePipeline);
  },

  bindThreadStage: (workspaceId, threadId, stage, sourceStage) => {
    const meta: StageThreadMeta = { stage, sourceStage, boundAt: new Date().toISOString() };
    set((state) => {
      const next = new Map(state.threadStageMeta);
      next.set(threadId, meta);
      return { threadStageMeta: next };
    });
    void saveThreadStageMeta(workspaceId, threadId, meta);
  },

  ensureThreadStageBinding: async (workspaceId, threadId) => {
    try {
      let pipeline = useContentStore.getState().outlinePipeline;
      if (!pipeline) {
        // 新建书竞态（FR-001）：建书流程清空管线状态且不重拉，首轮建会话时
        // 主动补拉一次——相位写回 store（阶段条即时可用）后再绑定
        const outline = await fetchWorkspaceOutline(workspaceId);
        pipeline = outline.pipeline ?? null;
        if (pipeline) useContentStore.getState().setOutlinePipeline(pipeline);
      }
      if (pipeline?.kind === "five" && pipeline.phase) {
        get().bindThreadStage(workspaceId, threadId, pipeline.phase);
      }
    } catch {
      /* 失败语义：退「早期」分组，不阻塞首轮对话 */
    }
  },

  remediateUnboundThreads: (pipeline) => {
    if (!pipeline || pipeline.kind !== "five") return;
    const ws = useWorkspaceStore.getState();
    const workspaceId = ws.activeWorkspaceId;
    if (!workspaceId) return;
    const metas = get().threadStageMeta;
    // 锚点 = 该书最早一条有标签会话的创建时间；无锚点（该书还没有任何有标签
    // 会话）时判定依据缺失，保守不动（FR-003 边界）
    let anchor = Number.POSITIVE_INFINITY;
    for (const t of ws.threads) {
      if (!metas.get(t.thread_id)) continue;
      const at = Date.parse(t.created_at);
      if (!Number.isNaN(at) && at < anchor) anchor = at;
    }
    if (!Number.isFinite(anchor)) return;
    // 首轮竞态漏绑的会话创建必早于锚点；晚于/等于锚点（含时间戳相同）一律
    // 保守跳过——宁可漏救不误伤（风险条目处置约束）
    let patched: Map<string, StageThreadMeta> | null = null;
    for (const t of ws.threads) {
      if (metas.get(t.thread_id)) continue;
      const at = Date.parse(t.created_at);
      if (Number.isNaN(at) || at >= anchor) continue;
      const meta: StageThreadMeta = { stage: PIPELINE_STEPS[0].phase, boundAt: t.created_at };
      (patched ??= new Map(metas)).set(t.thread_id, meta);
      void saveThreadStageMeta(workspaceId, t.thread_id, meta);
    }
    if (patched) set({ threadStageMeta: patched });
  },

  syncWithPipeline: (pipeline) => {
    const ws = useWorkspaceStore.getState();
    const activeThreadId = ws.activeThreadId;
    // 非 five（legacy / image / null）：阶段制不适用，清掉可能残留的挂起卡片
    if (!pipeline || pipeline.kind !== "five") {
      if (get().pendingTransition) set({ pendingTransition: null });
      return;
    }
    if (ws.activeWorkspaceId) maybeNotifyReady(pipeline, ws.activeWorkspaceId);
    // 存量归位（REQ-20261019-232635 FR-003）：首轮竞态漏绑的会话在此自动补绑。
    // 本函数是管线信号收敛点（hydrate 主动重算 + home 的 SSE/轮询 effect 都会
    // 到达），幂等可重入；放在 activeThreadId 早退之前，未选中会话也能归位
    get().remediateUnboundThreads(pipeline);
    if (!activeThreadId) return;
    const meta = get().threadStageMeta.get(activeThreadId);
    if (meta && meta.stage === pipeline.phase) {
      // 就绪卡片（FR-001）：当前段产物齐全 → 挂「X 已齐全，进入 Y」等确认。
      // 就绪收回（产物改回草稿）时卡片同步消失，再次就绪重新挂起（AC-004）。
      const readyNext = pipeline.ready?.ready === true ? pipeline.ready.next : null;
      if (readyNext) {
        set({ pendingTransition: { fromStage: pipeline.phase, toStage: readyNext } });
      } else if (get().pendingTransition) {
        set({ pendingTransition: null });
      }
      return;
    }
    // run 进行中阶段闸冻结（后端保证 phase 不变）——防御性跳过，结束后信号会再触发
    if (useExecutionStore.getState().loading) return;
    if (meta) {
      // 绑定段 ≠ 管线段（恢复路径：跨窗口切换后打开 / 确认后建会话失败的兜底）
      // → 同一卡片作恢复入口；后退标注来源
      const sourceStage = stageOrder(pipeline.phase) < stageOrder(meta.stage) ? meta.stage : undefined;
      set({ pendingTransition: { fromStage: meta.stage, toStage: pipeline.phase, sourceStage } });
      return;
    }
    // 未绑定阶段的存量会话（五段制上线前创建）：相位仍停在主题段不触发；
    // 已推进到后续段 → 按「上一阶段完成」挂卡片走同一切换路径（迁移补救——
    // 否则老会话永远不换段，主题历史会一直跟着后续阶段走）
    if (pipeline.phase === PIPELINE_STEPS[0].phase) return;
    const prevIdx = Math.max(stageOrder(pipeline.phase) - 1, 0);
    set({ pendingTransition: { fromStage: PIPELINE_STEPS[prevIdx].phase, toStage: pipeline.phase } });
  },

  confirmTransition: async () => {
    const pending = get().pendingTransition;
    if (!pending || get().transitioning) return !pending;
    const ws = useWorkspaceStore.getState();
    const workspaceId = ws.activeWorkspaceId;
    if (!workspaceId) return false;
    // 防重（review P2）：createThread 网络往返期间连点/双回车不得并发建两个会话
    set({ transitioning: true });
    try {
      // FR-002：先写人工段指针（服务端唯一切段通道），再开新会话。幂等——
      // 指针已在目标段时重复写无副作用（恢复路径二次点击/跨窗口）。
      const status = await updateWritingStage(workspaceId, pending.toStage);
      // 拿 API 返回即时对齐相位，避免下一次 sync 在轮询信号到达前误挂恢复卡片
      if (status) useContentStore.getState().setOutlinePipeline(status);
      readyEdge.set(workspaceId, status?.ready?.ready === true);
      const thread = await createThreadRequest(workspaceId, newThreadName(pending.toStage));
      // sourceStage 一并落盘（FR-006）：跳段新会话的历史命名带「（正文申请）」
      get().bindThreadStage(workspaceId, thread.thread_id, pending.toStage, pending.sourceStage);
      // 归档补绑（REQ-20261019-232635 FR-002）：旧会话因首轮竞态无标签时按切换
      // 来源阶段补贴，历史归组不再漏；正常路径已有标签，不重绑
      if (ws.activeThreadId && !get().threadStageMeta.has(ws.activeThreadId)) {
        get().bindThreadStage(workspaceId, ws.activeThreadId, pending.fromStage);
      }
      // 同步完成消息换页（存旧 + 载新 + 落位），不依赖 React effect 时序
      // （review P1 竞态修复：确认切换「先写指针再建会话」从此是确定性顺序）
      useExecutionStore.getState().switchThread(thread.thread_id);
      ws.setThreads((current) => [thread, ...current.filter((t) => t.thread_id !== thread.thread_id)]);
      set({ pendingTransition: null });
      return true;
    } catch (createError) {
      // FR-004 失败语义：不清挂起、不清旧对话，提示重试（指针已写时下一点击幂等补会话）
      toast.error(createError instanceof Error ? createError.message : "无法开启新阶段对话。");
      return false;
    } finally {
      set({ transitioning: false });
    }
  },

  jumpToStage: async (stage) => {
    const ws = useWorkspaceStore.getState();
    const workspaceId = ws.activeWorkspaceId;
    if (!workspaceId || useExecutionStore.getState().loading || get().transitioning) return;
    const pipeline = useContentStore.getState().outlinePipeline;
    const currentMeta = ws.activeThreadId ? get().threadStageMeta.get(ws.activeThreadId) : undefined;
    if (pipeline?.kind === "five" && pipeline.phase === stage
      && (!currentMeta || currentMeta.stage === stage)) return;
    set({ transitioning: true });
    try {
      if (ws.activeThreadId && !currentMeta && pipeline?.kind === "five") {
        get().bindThreadStage(workspaceId, ws.activeThreadId, pipeline.phase);
      }
      // FR-004：阶段条跳段/回退与确认切换同走人工段指针（唯一入口）
      const status = await updateWritingStage(workspaceId, stage);
      if (status) useContentStore.getState().setOutlinePipeline(status);
      readyEdge.set(workspaceId, status?.ready?.ready === true);
      const thread = await createThreadRequest(workspaceId, newThreadName(stage));
      get().bindThreadStage(workspaceId, thread.thread_id, stage);
      useExecutionStore.getState().switchThread(thread.thread_id);
      ws.setThreads((current) => [thread, ...current.filter((t) => t.thread_id !== thread.thread_id)]);
      set({ pendingTransition: null, historyView: null });
    } catch (jumpError) {
      // 失败语义：指针与旧会话保持当前状态，提示重试
      toast.error(jumpError instanceof Error ? jumpError.message : "无法切换阶段。");
    } finally {
      set({ transitioning: false });
    }
  },

  restartThread: async () => {
    const ws = useWorkspaceStore.getState();
    const workspaceId = ws.activeWorkspaceId;
    if (!workspaceId || useExecutionStore.getState().loading || get().transitioning) return;
    // FR-007「阶段不变」：优先沿用当前会话的绑定阶段（挂起卡片存在时管线相位
    // 已是新阶段，直接取会把「重开本阶段」错开成「进入下一阶段」）；
    // 图片域/无管线时兜底开普通新会话（stage="image"，历史归「早期」）
    const pipeline = useContentStore.getState().outlinePipeline;
    const currentMeta = ws.activeThreadId ? get().threadStageMeta.get(ws.activeThreadId) : undefined;
    const stage = currentMeta?.stage ?? (pipeline?.kind === "five" ? pipeline.phase : "image");
    set({ transitioning: true });
    try {
      const thread = await createThreadRequest(workspaceId, newThreadName(stage));
      get().bindThreadStage(workspaceId, thread.thread_id, stage);
      useExecutionStore.getState().switchThread(thread.thread_id);
      ws.setThreads((current) => [thread, ...current.filter((t) => t.thread_id !== thread.thread_id)]);
      set({ historyView: null });
    } catch (createError) {
      // FR-007 失败语义：旧会话保持当前，不出现两个「当前」
      toast.error(createError instanceof Error ? createError.message : "无法重新开始。");
    } finally {
      set({ transitioning: false });
    }
  },

  openStageHistory: (stage) => set({ historyView: { stage, threadId: null } }),

  openHistoryThread: (threadId) => {
    const view = get().historyView;
    if (!view) return;
    set({ historyView: { ...view, threadId } });
  },

  closeHistory: () => set({ historyView: null }),
}));

/**
 * 按阶段归组历史会话（FR-008）：历史=已归档会话，当前活动会话不属于任何
 * 阶段历史（排除，review P2——否则它排第一位且可删，删除后「当前」悬空）；
 * 无归属的归「早期」由调用方渲染。
 */
/** 测试钩子：清就绪边沿记录（生产不调用）。 */
export function _resetReadyEdgeForTests(): void {
  readyEdge.clear();
}

export function threadsForStage(
  threads: ThreadSummary[],
  stage: string,
  meta: Map<string, StageThreadMeta>,
  excludeThreadId?: string,
) {
  const matched: { thread: ThreadSummary; meta: StageThreadMeta }[] = [];
  const early: ThreadSummary[] = [];
  for (const thread of threads) {
    if (thread.thread_id === excludeThreadId) continue;
    const m = meta.get(thread.thread_id);
    if (m) {
      if (m.stage === stage) matched.push({ thread, meta: m });
    } else {
      early.push(thread);
    }
  }
  // 时间倒序（绑定时间近的在前）
  matched.sort((a, b) => Date.parse(b.meta.boundAt) - Date.parse(a.meta.boundAt));
  return { matched, early };
}
