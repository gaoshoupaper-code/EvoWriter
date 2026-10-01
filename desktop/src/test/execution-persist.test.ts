/**
 * execution store 持久化调度集成测试（REQ-20261001-170627 FR-002/FR-004）。
 *
 * mock @/lib/session-persist，验证 store 接线：
 * - loadThreadMessages 内存 miss → 本地恢复 / 无记录回退欢迎语 / 恢复期间切走不写回
 * - installExecutionPersist 订阅：流式节流、非流式终版 flush、空数组跳过
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@/lib/types";

const persistMock = {
  scheduleThreadPersist: vi.fn(),
  flushThreadPersist: vi.fn((_messages?: unknown, _workspaceId?: string, _threadId?: string) => Promise.resolve()),
  restoreThreadMessages: vi.fn(),
};

vi.mock("@/lib/session-persist", () => persistMock);

// stream 依赖在模块加载时 import，mock 掉避免触达 invoke
vi.mock("@/lib/stream", () => ({ streamRequest: vi.fn() }));

const { useExecutionStore, setExecutionDeps, installExecutionPersist, _resetExecutionPersistForTests } = await import("@/stores/execution");
const { useWorkspaceStore } = await import("@/stores/workspace");

function msg(partial: Partial<ChatMessage>): ChatMessage {
  return { role: "assistant", content: "c", ...partial };
}

beforeEach(async () => {
  vi.clearAllMocks();
  _resetExecutionPersistForTests();
  const { resetStores } = await import("./helpers");
  await resetStores();
  setExecutionDeps({
    getActiveThreadId: () => useWorkspaceStore.getState().activeThreadId,
    getActiveWorkspaceId: () => useWorkspaceStore.getState().activeWorkspaceId,
    setActiveThreadId: (id: string) => useWorkspaceStore.getState().setActiveThreadId(id),
  } as never);
});

describe("loadThreadMessages 本地恢复（FR-002）", () => {
  it("内存 miss → restore 成功 → 消息为恢复结果", async () => {
    persistMock.restoreThreadMessages.mockResolvedValue([msg({ status: "stopped", content: "中断的内容" })]);
    useWorkspaceStore.setState({ activeThreadId: "th1", activeWorkspaceId: "ws1" });

    useExecutionStore.getState().loadThreadMessages("th1");
    await vi.waitFor(() => {
      expect(useExecutionStore.getState().messages).toHaveLength(1);
    });
    expect(useExecutionStore.getState().messages[0].content).toBe("中断的内容");
    expect(persistMock.restoreThreadMessages).toHaveBeenCalledWith("ws1", "th1");
  });

  it("本地无记录 → 回退欢迎语", async () => {
    persistMock.restoreThreadMessages.mockResolvedValue(null);
    useWorkspaceStore.setState({ activeThreadId: "th1", activeWorkspaceId: "ws1" });

    useExecutionStore.getState().loadThreadMessages("th1");
    await vi.waitFor(() => {
      expect(useExecutionStore.getState().messages[0].content).toBe("我们开始新的故事吧～");
    });
  });

  it("恢复期间切走会话 → 不写回旧会话", async () => {
    let releaseRestore: (v: ChatMessage[] | null) => void = () => {};
    persistMock.restoreThreadMessages.mockReturnValue(new Promise((resolve) => { releaseRestore = resolve; }));
    useWorkspaceStore.setState({ activeThreadId: "th1", activeWorkspaceId: "ws1" });

    useExecutionStore.getState().loadThreadMessages("th1");
    useWorkspaceStore.setState({ activeThreadId: "th2" }); // 恢复完成前切走
    releaseRestore([msg({ content: "旧会话内容" })]);
    await Promise.resolve();
    await Promise.resolve();

    // 旧会话内容被丢弃：仍是清空态（th2 的加载不在本测试范围）
    expect(useExecutionStore.getState().messages).toHaveLength(0);
  });
});

describe("installExecutionPersist 调度（FR-002/FR-004）", () => {
  it("非流式期间消息变化 → flush 终版", () => {
    installExecutionPersist();
    useWorkspaceStore.setState({ activeThreadId: "th1", activeWorkspaceId: "ws1" });
    useExecutionStore.getState().setMessages([msg({ status: "completed" })]);

    expect(persistMock.flushThreadPersist).toHaveBeenCalledTimes(1);
    const args = persistMock.flushThreadPersist.mock.calls[0] as unknown[];
    expect(args[1]).toBe("ws1");
    expect(args[2]).toBe("th1");
    expect((args[0] as ChatMessage[])[0].status).toBe("completed");
  });

  it("流式期间（loading）消息变化 → 节流写中间内容", () => {
    installExecutionPersist();
    useWorkspaceStore.setState({ activeThreadId: "th1", activeWorkspaceId: "ws1" });
    useExecutionStore.setState({ loading: true });
    useExecutionStore.getState().setMessages([msg({ content: "半截" })]);

    expect(persistMock.scheduleThreadPersist).toHaveBeenCalledTimes(1);
    expect(persistMock.flushThreadPersist).not.toHaveBeenCalled();
  });

  it("空数组不持久化（切换/恢复清空瞬间不覆盖既有记录）", () => {
    installExecutionPersist();
    useWorkspaceStore.setState({ activeThreadId: "th1", activeWorkspaceId: "ws1" });
    useExecutionStore.getState().setMessages([]);

    expect(persistMock.flushThreadPersist).not.toHaveBeenCalled();
    expect(persistMock.scheduleThreadPersist).not.toHaveBeenCalled();
  });

  it("流结束（loading 翻转、messages 引用不变）→ 终版 flush（FR-002 终态写终版）", () => {
    installExecutionPersist();
    useWorkspaceStore.setState({ activeThreadId: "th1", activeWorkspaceId: "ws1" });
    const terminal = [msg({ status: "completed" })];
    useExecutionStore.setState({ messages: terminal, loading: true });
    persistMock.flushThreadPersist.mockClear();

    // 流结束：只有 loading 变化，messages 引用不变
    useExecutionStore.setState({ loading: false });

    expect(persistMock.flushThreadPersist).toHaveBeenCalledTimes(1);
    expect(persistMock.flushThreadPersist.mock.calls[0][0]).toBe(terminal);
  });
});
