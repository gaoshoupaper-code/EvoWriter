/**
 * REQ-20261001-154621 AC-004：图片流断流落 failed 终态
 *
 * 根因：performImageStream 的读循环正常结束（无 final/interrupt/error）时
 * 不落任何终态，消息停留在「正在生成...」占位 → booting 态永挂。
 *
 * 验证（store 级，不经整页渲染）：
 *   1. 图片流断流（done 无 final）→ 消息 status=failed，不停留在占位文案
 *   2. 图片流正常完成（final）→ 消息 status=completed（执行态退出）
 *   3. 图片流 error 事件 → 消息 status=failed
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

const encoder = new TextEncoder();
function sseEvent(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

// 受控 chunks：由各用例在运行前注入（vi.mock 工厂惰性执行，读取时已初始化）
let streamChunks: Uint8Array[] = [];

vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation(() => {
    let index = 0;
    return Promise.resolve({
      read: () => {
        if (index < streamChunks.length) {
          return Promise.resolve({ done: false, value: streamChunks[index++] });
        }
        return Promise.resolve({ done: true, value: undefined });
      },
      cancel: () => Promise.resolve(),
    });
  }),
}));

vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  createThread: vi.fn(), updateThread: vi.fn(),
  trackCopy: vi.fn(), trackRegenerate: vi.fn(),
}));

const { useExecutionStore, setExecutionDeps } = await import("@/stores/execution");

function injectDeps() {
  setExecutionDeps({
    getActiveThreadId: () => "thread-1",
    getActiveWorkspaceId: () => "ws-1",
    getActiveThreadSessionName: () => "测试会话",
    getActiveThreadWorkspacePath: () => "/test",
    getActiveWorkspaceDomain: () => "image",
    setActiveThreadId: () => {},
    setThreads: () => {},
    addThread: () => {},
    getTraceRuns: () => [],
    getActiveTraceId: () => "",
    getLiveTraceId: () => "",
    setTraceRuns: () => {},
    setTraceDetail: () => {},
    setActiveTraceId: () => {},
    setLiveTraceId: () => {},
  } as Parameters<typeof setExecutionDeps>[0]);
}

async function lastAssistant() {
  const { messages } = useExecutionStore.getState();
  return messages[messages.length - 1];
}

describe("AC-004: 图片流终态兜底", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    streamChunks = [];
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
    injectDeps();
  });

  it("断流（done 无 final）→ 消息落 failed，不停留在占位文案", async () => {
    streamChunks = [
      encoder.encode(sseEvent("model_stream", { content: "画面构思中" })),
    ];
    await useExecutionStore.getState().submitImage({ prompt: "画一只猫" });
    await vi.waitFor(() => {
      // 等待 finally 清理（loading false）标记流真正结束
    });
    const msg = await lastAssistant();
    expect(useExecutionStore.getState().loading).toBe(false);
    expect(msg.status).toBe("failed");
    expect(msg.content).not.toBe("正在生成...");
    expect(msg.content).toContain("连接中断");
  });

  it("正常完成（final）→ 消息落 completed，退出执行态", async () => {
    streamChunks = [
      encoder.encode(sseEvent("model_stream", { content: "好的" })),
      encoder.encode(sseEvent("final", { content: "图片已生成" })),
    ];
    await useExecutionStore.getState().submitImage({ prompt: "画一只猫" });
    const msg = await lastAssistant();
    expect(useExecutionStore.getState().loading).toBe(false);
    expect(msg.status).toBe("completed");
  });

  it("error 事件 → 消息落 failed", async () => {
    streamChunks = [
      encoder.encode(sseEvent("error", { error: "boom" })),
    ];
    await useExecutionStore.getState().submitImage({ prompt: "画一只猫" });
    const msg = await lastAssistant();
    expect(useExecutionStore.getState().loading).toBe(false);
    expect(msg.status).toBe("failed");
  });
});
