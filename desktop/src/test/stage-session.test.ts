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
vi.mock("@/lib/api", () => ({
  createThread: (...args: unknown[]) => createThreadMock(...args),
}));

import { useStageSessionStore, stageThreadDisplayName, threadsForStage } from "@/stores/stage-session";
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
  useExecutionStore.setState({ loading: false });
  useContentStore.setState({ outlinePipeline: null });
}

beforeEach(() => {
  stores.clear();
  createThreadMock.mockReset();
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
