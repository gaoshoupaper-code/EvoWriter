/**
 * session-persist 单测（REQ-20261001-170627 FR-002/004/005）。
 *
 * mock @tauri-apps/plugin-store 为内存实现，验证：
 * - 读写删与账号隔离（FR-002）
 * - 恢复管线：streaming→stopped、running tool→done、executionPhase 重算、卡点 2h 阈值（FR-004/005）
 * - 节流/flush 时序与串行写（风险条目：终版不被中间版覆盖）
 * - 失败语义：store 抛错静默降级（FR-002）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@/lib/types";

// ── plugin-store 内存 mock ──
const stores = new Map<string, Map<string, unknown>>();
const loadMock = vi.fn(async (path: string) => {
  if (!stores.has(path)) stores.set(path, new Map());
  const map = stores.get(path)!;
  return {
    set: async (key: string, value: unknown) => { map.set(key, JSON.parse(JSON.stringify(value))); },
    get: async (key: string) => {
      const v = map.get(key);
      // JSON 往返模拟真实落盘语义（undefined 字段丢失、Date 无）
      return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
    },
    delete: async (key: string) => map.delete(key),
    entries: async () => Array.from(map.entries()).map(([k, v]) => [k, JSON.parse(JSON.stringify(v))] as [string, unknown]),
    save: async () => { savedPaths.add(path); },
  };
});
const savedPaths = new Set<string>();

vi.mock("@tauri-apps/plugin-store", () => ({ load: (path: string) => loadMock(path) }));

import {
  AWAITING_INPUT_TIMEOUT_MS,
  _resetPersistForTests,
  flushThreadPersist,
  deleteThreadRecord,
  deleteWorkspaceRecords,
  initSessionPersist,
  normalizeRestoredMessages,
  persistThreadMessages,
  readLastPosition,
  restoreThreadMessages,
  saveLastPosition,
  scheduleThreadPersist,
} from "@/lib/session-persist";

const NOW = 1_700_000_000_000;

function msg(partial: Partial<ChatMessage>): ChatMessage {
  return { role: "assistant", content: "正文", ...partial };
}

beforeEach(() => {
  stores.clear();
  savedPaths.clear();
  _resetPersistForTests();
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  initSessionPersist({ getUsername: () => "alice" });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("恢复管线 normalizeRestoredMessages（FR-004/FR-005）", () => {
  it("streaming 消息转 stopped，running tool 转 done，executionPhase 清除待重算", () => {
    const out = normalizeRestoredMessages([
      msg({ status: "streaming", tools: [{ key: "t1", name: "x", status: "running", startedAt: 1 }], executionPhase: "thinking" as never }),
    ]);
    expect(out[0].status).toBe("stopped");
    expect(out[0].tools![0].status).toBe("done");
    expect(out[0].executionPhase).toBeUndefined();
  });

  it("终态消息（completed/failed/stopped）原样保留", () => {
    const out = normalizeRestoredMessages([
      msg({ status: "completed" }),
      msg({ status: "failed" }),
      msg({ status: "stopped" }),
    ]);
    expect(out.map((m) => m.status)).toEqual(["completed", "failed", "stopped"]);
  });

  it("2 小时内的卡点保留可交互", () => {
    const out = normalizeRestoredMessages([
      msg({ content: "选方案一还是二？", awaitingInput: { question: "选方案一还是二？", askedAt: new Date(NOW - AWAITING_INPUT_TIMEOUT_MS + 1000).toISOString() } }),
    ]);
    expect(out[0].awaitingInput).toBeDefined();
  });

  it("超过 2 小时的卡点降级为只读（content 保留问题文本）", () => {
    const out = normalizeRestoredMessages([
      msg({ content: "选方案一还是二？", awaitingInput: { question: "选方案一还是二？", askedAt: new Date(NOW - AWAITING_INPUT_TIMEOUT_MS - 1).toISOString() } }),
    ]);
    expect(out[0].awaitingInput).toBeUndefined();
    expect(out[0].content).toContain("选方案");
  });

  it("askedAt 缺失的旧数据保守降级", () => {
    const out = normalizeRestoredMessages([
      msg({ awaitingInput: { question: "q" } as never }),
    ]);
    expect(out[0].awaitingInput).toBeUndefined();
  });

  it("用户消息与非数组输入边界", () => {
    const out = normalizeRestoredMessages([{ role: "user", content: "hi" }]);
    expect(out).toEqual([{ role: "user", content: "hi" }]);
  });
});

describe("读写与隔离（FR-002）", () => {
  it("persist 后 restore 拿到恢复管线输出", async () => {
    await persistThreadMessages("ws1", "th1", [msg({ status: "completed", content: "A" })]);
    const out = await restoreThreadMessages("ws1", "th1");
    expect(out).toHaveLength(1);
    expect(out![0].content).toBe("A");
    expect(out![0].status).toBe("completed");
  });

  it("不同账号互不可见", async () => {
    await persistThreadMessages("ws1", "th1", [msg({})]);
    initSessionPersist({ getUsername: () => "bob" });
    expect(await restoreThreadMessages("ws1", "th1")).toBeNull();
  });

  it("restore 未命中返回 null", async () => {
    expect(await restoreThreadMessages("ws1", "nope")).toBeNull();
  });
});

describe("节流与终版保证（FR-002 + 风险：写盘竞态）", () => {
  it("流式期间多次 schedule 合并为一次节流写，且写最新快照", async () => {
    scheduleThreadPersist("ws1", "th1", [msg({ content: "v1", status: "streaming" })]);
    scheduleThreadPersist("ws1", "th1", [msg({ content: "v2", status: "streaming" })]);
    await vi.advanceTimersByTimeAsync(10_000);
    const out = await restoreThreadMessages("ws1", "th1");
    expect(out).toHaveLength(1);
    expect(out![0].content).toBe("v2");
  });

  it("flush 取消 pending 节流并立即写终版", async () => {
    scheduleThreadPersist("ws1", "th1", [msg({ content: "mid", status: "streaming" })]);
    await flushThreadPersist([msg({ content: "final", status: "completed" })], "ws1", "th1");
    const out = await restoreThreadMessages("ws1", "th1");
    expect(out![0].content).toBe("final");
    expect(out![0].status).toBe("completed");
  });
});

describe("删除联动（FR-003）", () => {
  it("deleteThreadRecord 删指定会话", async () => {
    await persistThreadMessages("ws1", "th1", [msg({})]);
    await persistThreadMessages("ws1", "th2", [msg({})]);
    await deleteThreadRecord("ws1", "th1");
    expect(await restoreThreadMessages("ws1", "th1")).toBeNull();
    expect(await restoreThreadMessages("ws1", "th2")).toHaveLength(1);
  });

  it("deleteWorkspaceRecords 清整个工作区", async () => {
    await persistThreadMessages("ws1", "th1", [msg({})]);
    await persistThreadMessages("ws1", "th2", [msg({})]);
    await persistThreadMessages("ws2", "th3", [msg({})]);
    await deleteWorkspaceRecords("ws1");
    expect(await restoreThreadMessages("ws1", "th1")).toBeNull();
    expect(await restoreThreadMessages("ws1", "th2")).toBeNull();
    expect(await restoreThreadMessages("ws2", "th3")).toHaveLength(1);
  });
});

describe("上次位置（FR-001）", () => {
  it("save 后可 read 回来；账号间隔离", async () => {
    await saveLastPosition("ws9", "th9");
    expect(await readLastPosition()).toEqual({ workspaceId: "ws9", threadId: "th9" });
    initSessionPersist({ getUsername: () => "bob" });
    expect(await readLastPosition()).toBeNull();
  });
});

describe("失败语义（FR-002：静默降级）", () => {
  it("store load 抛错时 restore 返回 null、persist 不抛", async () => {
    loadMock.mockRejectedValueOnce(new Error("no tauri"));
    await expect(restoreThreadMessages("ws1", "th1")).resolves.toBeNull();
    loadMock.mockRejectedValueOnce(new Error("no tauri"));
    await expect(persistThreadMessages("ws1", "th1", [msg({})])).resolves.toBeUndefined();
  });

  it("restore 拿到损坏数据（非数组）返回 null", async () => {
    stores.set("sessions/alice.store", new Map([["msg:ws1:th1", { messages: "broken" }]]));
    expect(await restoreThreadMessages("ws1", "th1")).toBeNull();
  });
});
