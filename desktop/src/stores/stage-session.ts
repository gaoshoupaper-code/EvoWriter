/**
 * stageSessionStore —— 阶段制会话管理（REQ-20261018-175526）。
 *
 * 用户心智里没有「会话」：对话由阶段自动开关。核心不变量：
 *   当前会话绑定阶段 ≠ 管线阶段（kind=five、非生成中）→ 挂起完成卡片，
 *   用户点卡片或直接发消息（隐式切换）才归档旧会话、开新会话。
 *
 * 该不变量统一驱动三种场景（DEC-003/006/013）：
 * - 自然推进（主题→总纲…）：后退不发生，无来源标注；
 * - 章纲↔正文往返 / 下游申请改上游（跳段后退）：卡片标注「应 X 阶段申请」；
 * - 重启/切书恢复：挂起卡片由不变量自动重建，无需独立持久化（FR-012）。
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
import { createThread as createThreadRequest, fetchWorkspaceOutline } from "@/lib/api";
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

  /** 启动/切书时恢复该工作区全部会话的阶段归属（FR-012）。 */
  hydrateForWorkspace: (workspaceId: string) => Promise<void>;
  bindThreadStage: (workspaceId: string, threadId: string, stage: string, sourceStage?: string) => void;
  /** 首轮建会话的阶段绑定（REQ-20261019-232635 FR-001）：管线未载时拉取兜底再绑，竞态不漏绑；失败静默。 */
  ensureThreadStageBinding: (workspaceId: string, threadId: string) => Promise<void>;
  /** 存量竞态受害会话归位（REQ-20261019-232635 FR-003）：早于该书最早有标签会话的无标签会话补绑「主题」。 */
  remediateUnboundThreads: (pipeline: PipelineStatus | null) => void;
  /** 不变量检测：管线阶段 vs 当前会话绑定阶段。由 home 的 effect 在信号变化时调。 */
  syncWithPipeline: (pipeline: PipelineStatus | null) => void;
  /** 完成卡片主按钮 / 隐式切换：归档旧会话、建新会话、清空对话区。 */
  confirmTransition: () => Promise<boolean>;
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

  hydrateForWorkspace: async (workspaceId) => {
    if (!workspaceId) return;
    // 切书即换上下文：浏览态与挂起卡片不得跨书携带
    set({ historyView: null, pendingTransition: null });
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
    // 存量归位（REQ-20261019-232635 FR-003）：首轮竞态漏绑的会话在此自动补绑。
    // 本函数是管线信号收敛点（hydrate 主动重算 + home 的 SSE/轮询 effect 都会
    // 到达），幂等可重入；放在 activeThreadId 早退之前，未选中会话也能归位
    get().remediateUnboundThreads(pipeline);
    if (!activeThreadId) return;
    const meta = get().threadStageMeta.get(activeThreadId);
    if (meta && meta.stage === pipeline.phase) {
      if (get().pendingTransition) set({ pendingTransition: null });
      return;
    }
    // run 进行中阶段闸冻结（后端保证 phase 不变）——防御性跳过，结束后信号会再触发
    if (useExecutionStore.getState().loading) return;
    if (meta) {
      // 后退切换 = 跳段申请（章纲↔正文往返 / 下游申请改上游），卡片标注来源
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
      const thread = await createThreadRequest(workspaceId, newThreadName(pending.toStage));
      // sourceStage 一并落盘（FR-006）：跳段新会话的历史命名带「（正文申请）」
      get().bindThreadStage(workspaceId, thread.thread_id, pending.toStage, pending.sourceStage);
      // 归档补绑（REQ-20261019-232635 FR-002）：旧会话因首轮竞态无标签时按切换
      // 来源阶段补贴，历史归组不再漏；正常路径已有标签，不重绑
      if (ws.activeThreadId && !get().threadStageMeta.has(ws.activeThreadId)) {
        get().bindThreadStage(workspaceId, ws.activeThreadId, pending.fromStage);
      }
      // 同步完成消息换页（存旧 + 载新 + 落位），不依赖 React effect 时序
      // （review P1 竞态修复：隐式切换「先建会话再发消息」从此是确定性顺序）
      useExecutionStore.getState().switchThread(thread.thread_id);
      ws.setThreads((current) => [thread, ...current.filter((t) => t.thread_id !== thread.thread_id)]);
      set({ pendingTransition: null });
      return true;
    } catch (createError) {
      // FR-004 失败语义：不清挂起、不清旧对话，提示重试
      toast.error(createError instanceof Error ? createError.message : "无法开启新阶段对话。");
      return false;
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
