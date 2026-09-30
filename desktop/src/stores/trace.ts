/**
 * traceStore —— Trace 列表 / 详情 / 历史回放（从 home.tsx 迁移）
 *
 * 职责：trace runs 列表加载、trace detail 加载/增量更新、历史回放、trace 删除。
 *
 * 被 executionStore 通过 ExecutionDeps 间接调用（setTraceRuns/setTraceDetail 等）。
 */
import { create } from "zustand";
import { toast } from "sonner";
import type { TraceDetail, TraceRunSummary } from "@/lib/types";
import { fetchThreadTraces, fetchTraceDetail } from "@/lib/api";

interface TraceState {
  traceRuns: TraceRunSummary[];
  activeTraceId: string;
  liveTraceId: string;
  traceDetail: TraceDetail | null;
  historyDetails: Map<string, TraceDetail>;
  traceLoading: boolean;

  // actions
  loadTraceRuns: (threadId: string) => Promise<void>;
  loadTraceDetail: (threadId: string, traceId: string) => Promise<void>;
  loadHistoryDetails: (threadId: string, traceIds: string[]) => Promise<void>;
  clearTrace: () => void;
  resetForThread: () => void;

  // executionStore 注入用的直接 setter（通过 deps 暴露）
  setTraceRuns: (updater: TraceRunSummary[] | ((current: TraceRunSummary[]) => TraceRunSummary[])) => void;
  setTraceDetail: (updater: TraceDetail | null | ((current: TraceDetail | null) => TraceDetail | null)) => void;
  setActiveTraceId: (id: string) => void;
  setLiveTraceId: (id: string) => void;
}

export const useTraceStore = create<TraceState>((set, get) => ({
  traceRuns: [],
  activeTraceId: "",
  liveTraceId: "",
  traceDetail: null,
  historyDetails: new Map(),
  traceLoading: false,

  loadTraceRuns: async (threadId) => {
    if (!threadId) {
      set({ traceRuns: [], activeTraceId: "", liveTraceId: "", traceDetail: null });
      return;
    }
    set({ traceLoading: true });
    try {
      const data = await fetchThreadTraces(threadId);
      set((state) => ({
        traceRuns: data,
        traceDetail: null,
        activeTraceId: data.some((run) => run.trace_id === state.activeTraceId) ? state.activeTraceId : data[0]?.trace_id || "",
      }));
    } catch (traceError) {
      set({ traceRuns: [], activeTraceId: "", traceDetail: null });
      toast.error(traceError instanceof Error ? traceError.message : "无法加载 Trace 列表。");
    } finally {
      set({ traceLoading: false });
    }
  },

  loadTraceDetail: async (threadId, traceId) => {
    if (!threadId || !traceId) {
      set({ traceDetail: null });
      return;
    }
    // live trace 不重新加载
    if (get().liveTraceId === traceId) return;

    set({ traceLoading: true });
    try {
      const detail = await fetchTraceDetail(threadId, traceId);
      set({ traceDetail: detail });
    } catch (traceError) {
      set({ traceDetail: null });
      toast.error(traceError instanceof Error ? traceError.message : "无法加载 Trace 详情。");
    } finally {
      set({ traceLoading: false });
    }
  },

  loadHistoryDetails: async (threadId, traceIds) => {
    if (!threadId || traceIds.length === 0) {
      set({ historyDetails: new Map() });
      return;
    }
    const map = new Map<string, TraceDetail>();
    for (const id of traceIds) {
      try {
        map.set(id, await fetchTraceDetail(threadId, id));
      } catch {
        // 单个 trace 加载失败不阻塞其他
      }
    }
    set({ historyDetails: map });
  },


  clearTrace: () => set({ traceRuns: [], activeTraceId: "", liveTraceId: "", traceDetail: null }),
  resetForThread: () => set({ traceRuns: [], activeTraceId: "", liveTraceId: "", traceDetail: null }),

  setTraceRuns: (updater) =>
    set((state) => ({
      traceRuns: typeof updater === "function" ? (updater as (c: TraceRunSummary[]) => TraceRunSummary[])(state.traceRuns) : updater,
    })),
  setTraceDetail: (updater) =>
    set((state) => ({
      traceDetail: typeof updater === "function" ? (updater as (c: TraceDetail | null) => TraceDetail | null)(state.traceDetail) : updater,
    })),
  setActiveTraceId: (id) => set({ activeTraceId: id }),
  setLiveTraceId: (id) => set({ liveTraceId: id }),
}));
