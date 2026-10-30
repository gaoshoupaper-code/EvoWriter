/**
 * stageSessionStore 单测（REQ-20261018-175526 FR-004/005/007/012 逻辑层）。
 * 覆盖：
 * - 阶段变化不变量：推进无标注、后退标注申请来源、同阶段清除、生成中不弹、
 *   非 five 清除、无绑定在主题段不弹、无绑定已推进段走迁移补救（上一阶段完成卡）
 * - confirmTransition：成功建会话绑定新阶段并切换；失败保留挂起（FR-004 失败语义）
 * - restartThread：同阶段新会话；loading 中拒绝（FR-007）
 * - 阶段归属持久化往返 + 删除联动（FR-012/FR-008）
 * - 历史归组与显示名（FR-008）
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const stores = new Map<string, Map<string, unknown>>();
vi.mock("@tauri-apps/plugin-store", () => ({
  load: async (path: string) => {
    if (!stores.has(path)) stores.set(path, new Map());
    const map = stores.get(path)!;
    return {
      set: async (key: string, value: unknown) => { map.set(key, JSON.parse(JSON.stringify(value))); },
      get: async (key: string) => {
        const v = map.get(key);
        return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
      },
      delete: async (key: string) => map.delete(key),
      entries: async () => Array.from(map.entries()).map(([k, v]) => [k, JSON.parse(JSON.stringify(v))] as [string, unknown]),
      save: async () => {},
    };
  },
}));

const createThreadMock = vi.fn();
const fetchOutlineMock = vi.fn();
const updateStageMock = vi.fn();
const advanceStageMock = vi.fn();
vi.mock("@/lib/api", () => ({
  createThread: (...args: unknown[]) => createThreadMock(...args),
  fetchWorkspaceOutline: (...args: unknown[]) => fetchOutlineMock(...args),
  updateWritingStage: (...args: unknown[]) => updateStageMock(...args),
  advanceWritingStage: (...args: unknown[]) => advanceStageMock(...args),
}));

import { useStageSessionStore, stageThreadDisplayName, threadsForStage, _resetReadyEdgeForTests } from "@/stores/stage-session";
import { useWorkspaceStore } from "@/stores/workspace";
import { setExecutionDeps, useExecutionStore } from "@/stores/execution";
import { useContentStore } from "@/stores/content";
import {
  _resetPersistForTests,
  deleteThreadRecord,
  initSessionPersist,
  readStageMetas,
} from "@/lib/session-persist";
import type { ThreadSummary } from "@/lib/types";

const WS = "ws-1";

function thread(id: string): ThreadSummary {
  return { thread_id: id, workspace_id: WS, session_name: `会话 ${id}`, workspace_path: "/tmp", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" };
}

function resetStores() {
  useStageSessionStore.setState({ threadStageMeta: new Map(), pendingTransition: null, historyView: null, transitioning: false });
  useWorkspaceStore.setState({ activeWorkspaceId: WS, activeThreadId: "", threads: [] });
  useExecutionStore.setState({ loading: false, messages: [], threadMessages: new Map() });
  useContentStore.setState({ outlinePipeline: null });
}

beforeEach(() => {
  stores.clear();
  createThreadMock.mockReset();
  advanceStageMock.mockReset();
  _resetReadyEdgeForTests();
  notifyStageReadyMock.mockClear();
  // 人工段指针写入（232617 FR-002）：默认按入参回显 pipeline_status
  updateStageMock.mockReset();
  updateStageMock.mockImplementation((_ws: string, stage: string) =>
    Promise.resolve({ kind: "five", phase: stage, next_unit: "", awaiting: {} }),
  );
  _resetPersistForTests();
  initSessionPersist({ getUsername: () => "tester" });
  resetStores();
  // confirmTransition/restartThread 走 execution.switchThread 同步换页（生产由 home 注入）
  setExecutionDeps({
    getActiveThreadId: () => useWorkspaceStore.getState().activeThreadId,
    getActiveWorkspaceId: () => useWorkspaceStore.getState().activeWorkspaceId,
    getActiveThreadSessionName: () => "",
    getActiveThreadWorkspacePath: () => "",
    getActiveWorkspaceDomain: () => "writing",
    setActiveThreadId: (id) => useWorkspaceStore.getState().setActiveThreadId(id),
    setThreads: (updater) => useWorkspaceStore.getState().setThreads(updater as any),
    addThread: (t) => useWorkspaceStore.getState().setThreads((cur) => [t, ...cur.filter((x) => x.thread_id !== t.thread_id)]),
    getTraceRuns: () => [],
    getActiveTraceId: () => "",
    getLiveTraceId: () => "",
    setTraceRuns: () => {},
    setTraceDetail: () => {},
    setActiveTraceId: () => {},
    setLiveTraceId: () => {},
  });
});

describe("syncWithPipeline 阶段变化不变量（FR-004）", () => {
  it("推进（theme→outline）挂起卡片，无申请标注", () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toEqual({
      fromStage: "theme", toStage: "outline", sourceStage: undefined,
    });
  });

  it("后退（text→chapter）标注申请来源（章纲↔正文往返 / 下游申请，FR-004/FR-006）", () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "text");
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    s.syncWithPipeline({ kind: "five", phase: "chapter", next_unit: "章纲", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toEqual({
      fromStage: "text", toStage: "chapter", sourceStage: "text",
    });
  });

  it("阶段相同清除挂起；生成中不弹；非 five 清除；无绑定不弹", () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    useWorkspaceStore.setState({ activeThreadId: "t1" });

    s.syncWithPipeline({ kind: "five", phase: "theme", next_unit: "题材", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();

    useExecutionStore.setState({ loading: true });
    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
    useExecutionStore.setState({ loading: false });

    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).not.toBeNull();
    s.syncWithPipeline({ kind: "legacy", phase: "legacy", next_unit: "", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();

    // 无绑定（存量会话迁移补救）：相位停在主题段不触发；已推进到后续段
    // 按「上一阶段完成」挂卡片走同一切换路径
    useWorkspaceStore.setState({ activeThreadId: "t-old" });
    s.syncWithPipeline({ kind: "five", phase: "theme", next_unit: "题材", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
    s.syncWithPipeline({ kind: "five", phase: "text", next_unit: "正文", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toEqual({
      fromStage: "chapter", toStage: "text", sourceStage: undefined,
    });
  });
});

describe("段审推进必须隔离会话", () => {
  function prepare(stage = "volume", bind = true) {
    useWorkspaceStore.setState({ activeThreadId: "t1", threads: [thread("t1")] });
    useContentStore.setState({ outlinePipeline: { kind: "five", phase: stage, next_unit: stage, awaiting: {} } });
    if (bind) useStageSessionStore.getState().bindThreadStage(WS, "t1", stage);
    useExecutionStore.setState({ messages: [
      { role: "user", content: "卷纲旧消息" },
      { role: "assistant", content: "卷纲已完成", traceId: "trace-volume", awaitingInput: {
        kind: "review", review_kind: "stage", review_id: "review-volume",
        question: "请审查卷纲", options: null, multi_select: false,
      } },
    ] });
    advanceStageMock.mockResolvedValue({ kind: "five", phase: "chapter", awaiting: {} });
    createThreadMock.mockResolvedValue(thread("t2"));
  }

  it("卷纲确认开章纲新对话，旧消息和旧阶段保存在历史", async () => {
    prepare();
    const oldMeta = useStageSessionStore.getState().threadStageMeta.get("t1");
    expect(await useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume")).toBe(true);
    expect(advanceStageMock).toHaveBeenCalledWith(WS, "trace-volume", "review-volume");
    expect(createThreadMock).toHaveBeenCalledWith(WS, "章纲对话");
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t2");
    expect(useContentStore.getState().outlinePipeline?.phase).toBe("chapter");
    expect(useStageSessionStore.getState().threadStageMeta.get("t1")).toBe(oldMeta);
    expect(useStageSessionStore.getState().threadStageMeta.get("t2")?.stage).toBe("chapter");
    expect(useExecutionStore.getState().messages.some((m) => m.content.includes("卷纲"))).toBe(false);
    const archived = useExecutionStore.getState().threadMessages.get("t1")!;
    expect(archived[0].content).toBe("卷纲旧消息");
    expect(archived[1].awaitingInput).toBeUndefined();
    expect(archived[1].status).toBe("answered");
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
  });

  it("章纲与正文往返、下一卷推进，每次各开新会话", async () => {
    prepare();
    let i = 1;
    createThreadMock.mockImplementation(async () => thread(`t${++i}`));
    for (const phase of ["chapter", "text", "chapter", "volume"]) {
      const oldId = useWorkspaceStore.getState().activeThreadId;
      advanceStageMock.mockResolvedValue({ kind: "five", phase, awaiting: {} });
      expect(await useStageSessionStore.getState().confirmStageReviewAdvance(WS, `trace-${i}`, `review-${i}`)).toBe(true);
      expect(useWorkspaceStore.getState().activeThreadId).not.toBe(oldId);
      expect(useStageSessionStore.getState().threadStageMeta.get(`t${i}`)?.stage).toBe(phase);
    }
    expect(createThreadMock).toHaveBeenCalledTimes(4);
  });

  it("同段确认不建会话，保留停等供正常resume", async () => {
    prepare("chapter");
    const messages = useExecutionStore.getState().messages;
    expect(await useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume")).toBe(true);
    expect(createThreadMock).not.toHaveBeenCalled();
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t1");
    expect(useExecutionStore.getState().messages).toBe(messages);
  });

  it("新会话失败保留旧对话，重试仍隔离且不重绑旧段", async () => {
    prepare("volume", false);
    const messages = useExecutionStore.getState().messages;
    createThreadMock.mockRejectedValueOnce(new Error("network down"));
    expect(await useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume")).toBe(false);
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t1");
    expect(useExecutionStore.getState().messages).toBe(messages);
    expect(useStageSessionStore.getState().threadStageMeta.get("t1")?.stage).toBe("volume");
    expect(useStageSessionStore.getState().pendingTransition?.toStage).toBe("chapter");
    expect(await useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume")).toBe(true);
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t2");
  });

  it("推进失败不建会话、不动消息", async () => {
    prepare();
    const messages = useExecutionStore.getState().messages;
    advanceStageMock.mockRejectedValue(new Error("review blocked"));
    expect(await useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume")).toBe(false);
    expect(createThreadMock).not.toHaveBeenCalled();
    expect(useExecutionStore.getState().messages).toBe(messages);
    expect(useStageSessionStore.getState().transitioning).toBe(false);
  });

  it("连点确认不重复推进或建会话", async () => {
    prepare();
    let resolve!: (t: ThreadSummary) => void;
    createThreadMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    const first = useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume");
    await vi.waitFor(() => expect(createThreadMock).toHaveBeenCalledTimes(1));
    expect(await useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume")).toBe(false);
    expect(advanceStageMock).toHaveBeenCalledTimes(1);
    resolve(thread("t2"));
    expect(await first).toBe(true);
  });

  it("网络返回前切书，不覆盖另一作品或继续生成", async () => {
    prepare();
    let resolve!: (status: unknown) => void;
    advanceStageMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    const first = useStageSessionStore.getState().confirmStageReviewAdvance(WS, "trace-volume", "review-volume");
    useWorkspaceStore.setState({ activeWorkspaceId: "ws-2", activeThreadId: "other" });
    resolve({ kind: "five", phase: "chapter", awaiting: {} });
    expect(await first).toBe(false);
    expect(useWorkspaceStore.getState().activeThreadId).toBe("other");
    expect(createThreadMock).not.toHaveBeenCalled();
    expect(useContentStore.getState().outlinePipeline?.phase).toBe("volume");
  });
});

describe("confirmTransition（FR-004/FR-005）", () => {
  it("成功：建新会话绑定新阶段、切换当前、清挂起", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    useWorkspaceStore.setState({ activeThreadId: "t1", threads: [thread("t1")] });
    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });

    createThreadMock.mockResolvedValue(thread("t2"));
    const ok = await s.confirmTransition();
    expect(ok).toBe(true);
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t2");
    expect(useWorkspaceStore.getState().threads.map((t) => t.thread_id)).toEqual(["t2", "t1"]);
    expect(useStageSessionStore.getState().threadStageMeta.get("t2")?.stage).toBe("outline");
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
  });

  it("失败：保留挂起与旧会话（FR-004 失败语义）", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });

    createThreadMock.mockRejectedValue(new Error("network down"));
    const ok = await s.confirmTransition();
    expect(ok).toBe(false);
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t1");
    expect(useStageSessionStore.getState().pendingTransition).not.toBeNull();
  });

  it("后退切换落盘 sourceStage（FR-006/AC-007 端到端）", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "text");
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    s.syncWithPipeline({ kind: "five", phase: "chapter", next_unit: "章纲", awaiting: {} });

    createThreadMock.mockResolvedValue(thread("t2"));
    await s.confirmTransition();
    expect(useStageSessionStore.getState().threadStageMeta.get("t2")).toMatchObject({ stage: "chapter", sourceStage: "text" });
  });

  it("防重：切换进行中并发调用直接拒绝（review P2）", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });

    let resolveCreate!: (v: ThreadSummary) => void;
    createThreadMock.mockReturnValue(new Promise((r) => { resolveCreate = r; }));
    const first = s.confirmTransition();
    // 第一次未完成期间再次进入：不得再次 createThread
    const second = await s.confirmTransition();
    expect(second).toBe(false);
    expect(createThreadMock).toHaveBeenCalledTimes(1);
    resolveCreate(thread("t2"));
    expect(await first).toBe(true);
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t2");
  });

  it("AC-005：章纲↔正文往返 N 次产生 N 个新会话，各绑定正确阶段", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t0", "text");
    useWorkspaceStore.setState({ activeThreadId: "t0" });
    let seq = 0;
    createThreadMock.mockImplementation(async () => thread(`n${++seq}`));

    const seq_stages = ["chapter", "text", "chapter"] as const;
    for (const stage of seq_stages) {
      s.syncWithPipeline({ kind: "five", phase: stage, next_unit: stage, awaiting: {} });
      expect(useStageSessionStore.getState().pendingTransition).not.toBeNull();
      const ok = await s.confirmTransition();
      expect(ok).toBe(true);
    }
    const meta = useStageSessionStore.getState().threadStageMeta;
    expect(useWorkspaceStore.getState().activeThreadId).toBe("n3");
    expect(meta.get("n1")?.stage).toBe("chapter");
    expect(meta.get("n2")?.stage).toBe("text");
    expect(meta.get("n3")?.stage).toBe("chapter");
  });

  it("AC-005 边界：同阶段多轮 sync 不建新会话", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    for (let i = 0; i < 5; i++) {
      s.syncWithPipeline({ kind: "five", phase: "theme", next_unit: "题材", awaiting: {} });
    }
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
    expect(createThreadMock).not.toHaveBeenCalled();
  });
});

describe("restartThread（FR-007）", () => {
  it("同阶段开新会话并切换", async () => {
    useContentStore.setState({ outlinePipeline: { kind: "five", phase: "volume", next_unit: "卷纲", awaiting: {} } });
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    createThreadMock.mockResolvedValue(thread("t9"));
    await useStageSessionStore.getState().restartThread();
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t9");
    expect(useStageSessionStore.getState().threadStageMeta.get("t9")?.stage).toBe("volume");
  });

  it("生成中拒绝（防误归档流式中的对话）", async () => {
    useExecutionStore.setState({ loading: true });
    useContentStore.setState({ outlinePipeline: { kind: "five", phase: "volume", next_unit: "卷纲", awaiting: {} } });
    await useStageSessionStore.getState().restartThread();
    expect(createThreadMock).not.toHaveBeenCalled();
  });
});

describe("阶段归属持久化（FR-012/FR-008）", () => {
  it("写入后 hydrate 恢复；删除会话联动清理", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    s.bindThreadStage(WS, "t2", "outline", "text");
    // 等落盘
    await vi.waitFor(async () => expect(Object.keys(await readStageMetas(WS))).toHaveLength(2));

    // 模拟重启：内存清空后 hydrate
    useStageSessionStore.setState({ threadStageMeta: new Map() });
    await useStageSessionStore.getState().hydrateForWorkspace(WS);
    const meta = useStageSessionStore.getState().threadStageMeta;
    expect(meta.get("t1")?.stage).toBe("theme");
    expect(meta.get("t2")?.sourceStage).toBe("text");

    await deleteThreadRecord(WS, "t1");
    const after = await readStageMetas(WS);
    expect(after["t1"]).toBeUndefined();
    expect(after["t2"]).toBeDefined();
  });

  it("hydrate 清除浏览态与挂起卡片（跨书不携带）", async () => {
    useStageSessionStore.setState({ historyView: { stage: "theme", threadId: "x" }, pendingTransition: { fromStage: "theme", toStage: "outline" } });
    await useStageSessionStore.getState().hydrateForWorkspace("ws-2");
    expect(useStageSessionStore.getState().historyView).toBeNull();
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
  });

  it("AC-013 重启恢复：hydrate 后管线信号重建挂起卡片（FR-012 不变量）", async () => {
    // 模拟「阶段已变、用户没点卡片就关了客户端」：盘上有 theme 绑定，管线已是 outline
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    await vi.waitFor(async () => expect(Object.keys(await readStageMetas(WS))).toHaveLength(1));

    useStageSessionStore.setState({ threadStageMeta: new Map(), pendingTransition: null });
    useWorkspaceStore.setState({ activeThreadId: "t1" });
    await useStageSessionStore.getState().hydrateForWorkspace(WS);

    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition).toEqual({
      fromStage: "theme", toStage: "outline", sourceStage: undefined,
    });
  });
});

describe("历史归组与显示名（FR-008）", () => {
  it("threadsForStage 按 meta 命中归组，时间倒序，未绑定归早期，当前活动会话被排除", () => {
    const meta = new Map([
      ["t1", { stage: "text", boundAt: "2026-10-18T10:00:00" }],
      ["t2", { stage: "text", boundAt: "2026-10-18T12:00:00" }],
      ["t3", { stage: "chapter", boundAt: "2026-10-17T10:00:00" }],
    ]);
    const { matched, early } = threadsForStage([thread("t1"), thread("t2"), thread("t4")], "text", meta, "t2");
    expect(matched.map((m) => m.thread.thread_id)).toEqual(["t1"]);
    expect(early.map((t) => t.thread_id)).toEqual(["t4"]);
  });

  it("显示名：阶段+时间；跳段往返带来源申请标记（FR-006）", () => {
    const plain = stageThreadDisplayName({ stage: "theme", boundAt: "2026-10-18T15:02:00" });
    expect(plain).toBe("主题 · 10-18 15:02");
    const sourced = stageThreadDisplayName({ stage: "outline", sourceStage: "text", boundAt: "2026-10-18T15:02:00" });
    expect(sourced).toBe("总纲 · 10-18 15:02（正文申请）");
  });
});

describe("ensureThreadStageBinding 首轮竞态兜底（REQ-20261019-232635 FR-001）", () => {
  it("管线已载：直接绑定当前阶段，不发起拉取", async () => {
    useContentStore.setState({ outlinePipeline: { kind: "five", phase: "theme", next_unit: "题材", awaiting: {} } });
    await useStageSessionStore.getState().ensureThreadStageBinding(WS, "t1");
    expect(useStageSessionStore.getState().threadStageMeta.get("t1")?.stage).toBe("theme");
    expect(fetchOutlineMock).not.toHaveBeenCalled();
  });

  it("AC-001 管线未载（新建书竞态）：拉 outline 兜底，绑定 theme 并把相位写回 store", async () => {
    fetchOutlineMock.mockResolvedValue({
      kind: "five",
      pipeline: { kind: "five", phase: "theme", next_unit: "题材", awaiting: {} },
      files: [],
    });
    await useStageSessionStore.getState().ensureThreadStageBinding(WS, "t1");
    expect(fetchOutlineMock).toHaveBeenCalledWith(WS);
    expect(useStageSessionStore.getState().threadStageMeta.get("t1")?.stage).toBe("theme");
    expect(useContentStore.getState().outlinePipeline?.phase).toBe("theme");
  });

  it("拉取失败：静默不绑、不抛（FR-001 失败语义）", async () => {
    fetchOutlineMock.mockRejectedValue(new Error("network down"));
    await expect(useStageSessionStore.getState().ensureThreadStageBinding(WS, "t1")).resolves.toBeUndefined();
    expect(useStageSessionStore.getState().threadStageMeta.get("t1")).toBeUndefined();
  });

  it("legacy 管线：不绑定（历史侧归「早期」）", async () => {
    fetchOutlineMock.mockResolvedValue({
      kind: "legacy",
      pipeline: { kind: "legacy", phase: "legacy", next_unit: "", awaiting: {} },
      files: [],
    });
    await useStageSessionStore.getState().ensureThreadStageBinding(WS, "t1");
    expect(useStageSessionStore.getState().threadStageMeta.get("t1")).toBeUndefined();
  });
});

describe("confirmTransition 归档补绑（REQ-20261019-232635 FR-002）", () => {
  it("AC-002 旧会话无标签（首轮竞态漏绑）：确认切换时补绑 fromStage", async () => {
    const s = useStageSessionStore.getState();
    // t-old 无 meta——竞态受害者；管线推进到 outline 走迁移路径挂卡
    useWorkspaceStore.setState({ activeThreadId: "t-old", threads: [thread("t-old")] });
    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    expect(useStageSessionStore.getState().pendingTransition?.fromStage).toBe("theme");

    createThreadMock.mockResolvedValue(thread("t-new"));
    const ok = await s.confirmTransition();
    expect(ok).toBe(true);
    const meta = useStageSessionStore.getState().threadStageMeta;
    expect(meta.get("t-new")?.stage).toBe("outline");
    expect(meta.get("t-old")?.stage).toBe("theme");
  });

  it("旧会话已有标签：不重绑（meta 引用不变）", async () => {
    const s = useStageSessionStore.getState();
    s.bindThreadStage(WS, "t1", "theme");
    const before = useStageSessionStore.getState().threadStageMeta.get("t1");
    useWorkspaceStore.setState({ activeThreadId: "t1", threads: [thread("t1")] });
    s.syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    createThreadMock.mockResolvedValue(thread("t2"));
    await s.confirmTransition();
    expect(useStageSessionStore.getState().threadStageMeta.get("t1")).toBe(before);
  });
});

describe("remediateUnboundThreads 存量归位（REQ-20261019-232635 FR-003）", () => {
  function threadAt(id: string, iso: string): ThreadSummary {
    return { ...thread(id), created_at: iso, updated_at: iso };
  }

  it("AC-003 早于最早有标签会话的无标签会话补绑 theme（boundAt=创建时间，落盘可恢复）", async () => {
    useWorkspaceStore.setState({
      threads: [
        threadAt("t-victim", "2026-10-18T10:00:00Z"),
        threadAt("t-outline", "2026-10-19T09:00:00Z"),
      ],
    });
    useStageSessionStore.getState().bindThreadStage(WS, "t-outline", "outline");

    useStageSessionStore.getState().remediateUnboundThreads({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });

    const m = useStageSessionStore.getState().threadStageMeta.get("t-victim");
    expect(m?.stage).toBe("theme");
    expect(m?.boundAt).toBe("2026-10-18T10:00:00Z");
    await vi.waitFor(async () =>
      expect((await readStageMetas(WS))["t-victim"]).toMatchObject({ stage: "theme" }));
  });

  it("晚于锚点 / 与锚点时间戳相同：保守不补绑", () => {
    useWorkspaceStore.setState({
      threads: [
        threadAt("t-late", "2026-10-20T10:00:00Z"),
        threadAt("t-eq", "2026-10-19T09:00:00Z"),
        threadAt("t-anchor", "2026-10-19T09:00:00Z"),
      ],
    });
    useStageSessionStore.getState().bindThreadStage(WS, "t-anchor", "outline");

    useStageSessionStore.getState().remediateUnboundThreads({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });

    const meta = useStageSessionStore.getState().threadStageMeta;
    expect(meta.get("t-late")).toBeUndefined();
    expect(meta.get("t-eq")).toBeUndefined();
  });

  it("无任何有标签会话（无锚点）/ 管线未载 / legacy：不触发", () => {
    useWorkspaceStore.setState({ threads: [threadAt("t-a", "2026-10-18T10:00:00Z")] });
    useStageSessionStore.getState().remediateUnboundThreads(null);
    expect(useStageSessionStore.getState().threadStageMeta.get("t-a")).toBeUndefined();

    useStageSessionStore.getState().remediateUnboundThreads({ kind: "legacy", phase: "legacy", next_unit: "", awaiting: {} });
    expect(useStageSessionStore.getState().threadStageMeta.get("t-a")).toBeUndefined();
  });

  it("幂等：重复执行不重绑（meta 引用不变）", () => {
    useWorkspaceStore.setState({
      threads: [
        threadAt("t-victim", "2026-10-18T10:00:00Z"),
        threadAt("t-outline", "2026-10-19T09:00:00Z"),
      ],
    });
    useStageSessionStore.getState().bindThreadStage(WS, "t-outline", "outline");

    useStageSessionStore.getState().remediateUnboundThreads({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    const first = useStageSessionStore.getState().threadStageMeta.get("t-victim");
    useStageSessionStore.getState().remediateUnboundThreads({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });
    expect(useStageSessionStore.getState().threadStageMeta.get("t-victim")).toBe(first);
  });

  it("管线信号驱动（集成）：syncWithPipeline 到达 five 相位即自动归位（hydrate/SSE/轮询通道共用）", () => {
    useWorkspaceStore.setState({
      threads: [
        threadAt("t-victim", "2026-10-18T10:00:00Z"),
        threadAt("t-outline", "2026-10-19T09:00:00Z"),
      ],
    });
    useStageSessionStore.getState().bindThreadStage(WS, "t-outline", "outline");

    // 无 activeThreadId 也必须归位（管线晚到通道在选中会话前就能触发）
    useStageSessionStore.getState().syncWithPipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} });

    expect(useStageSessionStore.getState().threadStageMeta.get("t-victim")?.stage).toBe("theme");
  });
});


// ── 段间切换人工闸（REQ-20261019-232617 FR-001/002/004）────────────

describe("jumpToStage（阶段条跳段/回退，FR-004 AC-006）", () => {
  it("写人工段指针并开目标段新会话", async () => {
    const s = useStageSessionStore.getState();
    useWorkspaceStore.setState({ activeThreadId: "t-old" });
    useContentStore.setState({
      outlinePipeline: { kind: "five", phase: "outline", next_unit: "总纲", awaiting: {} },
    });
    createThreadMock.mockResolvedValue(thread("t-back"));
    await s.jumpToStage("theme");
    expect(updateStageMock).toHaveBeenCalledWith(WS, "theme");
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t-back");
    expect(useStageSessionStore.getState().threadStageMeta.get("t-back")?.stage).toBe("theme");
  });

  it("跳段建会话失败，指针已推进时仍能重试补开新对话", async () => {
    useWorkspaceStore.setState({ activeThreadId: "t-old" });
    useContentStore.setState({
      outlinePipeline: { kind: "five", phase: "text", next_unit: "正文", awaiting: {} },
    });
    createThreadMock.mockRejectedValueOnce(new Error("network down")).mockResolvedValueOnce(thread("t-back"));
    await useStageSessionStore.getState().jumpToStage("chapter");
    expect(useContentStore.getState().outlinePipeline?.phase).toBe("chapter");
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t-old");
    await useStageSessionStore.getState().jumpToStage("chapter");
    expect(useWorkspaceStore.getState().activeThreadId).toBe("t-back");
    expect(useStageSessionStore.getState().threadStageMeta.get("t-old")?.stage).toBe("text");
    expect(useStageSessionStore.getState().threadStageMeta.get("t-back")?.stage).toBe("chapter");
  });

  it("已在目标段时 no-op（不建会话不写指针）", async () => {
    const s = useStageSessionStore.getState();
    useWorkspaceStore.setState({ activeThreadId: "t-old" });
    useContentStore.setState({
      outlinePipeline: { kind: "five", phase: "theme", next_unit: "题材", awaiting: {} },
    });
    await s.jumpToStage("theme");
    expect(updateStageMock).not.toHaveBeenCalled();
    expect(createThreadMock).not.toHaveBeenCalled();
  });
});

describe("就绪通知边沿（FR-001 AC-004）", () => {
  it("未就绪→就绪触发一次系统通知；首见不算边沿；再就绪重新触发", async () => {
    const s = useStageSessionStore.getState();
    useWorkspaceStore.setState({ activeWorkspaceId: WS, activeThreadId: "t-1" });
    const bind = s.bindThreadStage;
    bind(WS, "t-1", "theme");

    const mk = (ready: boolean) => ({
      kind: "five" as const,
      phase: "theme",
      next_unit: "题材",
      awaiting: {},
      ready: { ready, next: ready ? "outline" : null },
    });
    // 首见就绪（启动场景）：不通知
    s.syncWithPipeline(mk(true));
    expect(notifyStageReadyMock).not.toHaveBeenCalled();
    // 未就绪 → 就绪：通知一次
    s.syncWithPipeline(mk(false));
    s.syncWithPipeline(mk(true));
    expect(notifyStageReadyMock).toHaveBeenCalledTimes(1);
    // 重复就绪信号：不重复通知
    s.syncWithPipeline(mk(true));
    expect(notifyStageReadyMock).toHaveBeenCalledTimes(1);
    // 收回后再就绪：再通知（卡片同步消失/重现）
    s.syncWithPipeline(mk(false));
    s.syncWithPipeline(mk(true));
    expect(notifyStageReadyMock).toHaveBeenCalledTimes(2);
    // 卡片随就绪挂起/收回
    expect(useStageSessionStore.getState().pendingTransition?.toStage).toBe("outline");
    s.syncWithPipeline(mk(false));
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
  });
});

// 就绪系统通知 mock（vi.hoisted：模块加载前就位）
const notifyStageReadyMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/notify", () => ({
  notifyStageReady: (...args: unknown[]) => notifyStageReadyMock(...args),
}));
