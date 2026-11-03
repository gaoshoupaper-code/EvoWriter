import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ account: vi.fn(), stream: vi.fn(), info: vi.fn() }));
vi.mock("sonner", () => ({ toast: { info: mocks.info, error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/stream", () => ({ streamRequest: mocks.stream }));
vi.mock("@/lib/usePanelPolling", () => ({ watchPipelineAfterDisconnect: vi.fn() }));
vi.mock("@/lib/session-persist", () => ({ flushThreadPersist: vi.fn(), restoreThreadMessages: vi.fn(), scheduleThreadPersist: vi.fn() }));
vi.mock("@/lib/api", () => ({
  API_BASE_URL: "", fetchMyBilling: mocks.account,
  fetchThreadConversation: vi.fn().mockResolvedValue([]), fetchThreadRunState: vi.fn().mockResolvedValue({ status: "running", trace_id: "trace" }),
  fetchBillingOperation: vi.fn(), createThread: vi.fn(), updateThread: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(), apiFetch: vi.fn(),
}));

const { useExecutionStore, setExecutionDeps } = await import("@/stores/execution");
const { apiFetch, fetchBillingOperation, fetchThreadConversation, fetchThreadRunState } = await import("@/lib/api");
const { useBillingStore } = await import("@/stores/billing");
const { useContentStore } = await import("@/stores/content");

const operation = { operation_id: "op", user_id: "owner", thread_id: "thread", request_id: "request", budget: 20,
  status: "running", consumed: 0, charged: 0, missing_usage: 0, trace_id: "trace", created_at: "", settled_at: null };
const account = { enabled: true, protocol: 2, contact_qq: "1134559830", balance: 100, total_balance: 100,
  reserved: 0, default_budgets: { default: 20 }, operations: [operation], transactions: [] };
const event = (type: string, data: unknown) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
const frame = (type: string, data: unknown) => new TextEncoder().encode(event(type, data));

beforeEach(async () => {
  vi.clearAllMocks();
  vi.mocked(fetchBillingOperation).mockReset();
  vi.mocked(fetchThreadConversation).mockResolvedValue([]);
  vi.mocked(fetchThreadRunState).mockResolvedValue({ status: "running", trace_id: "trace" } as never);
  useBillingStore.getState().reset();
  useBillingStore.getState().setOwner("owner");
  mocks.account.mockResolvedValue(account);
  await useBillingStore.getState().refresh();
  useContentStore.setState({ outlinePipeline: null });
  useExecutionStore.setState({ messages: [], loading: false, streamReader: null, threadMessages: new Map(), characterRepair: false });
  setExecutionDeps({ getActiveWorkspaceId: () => "workspace", getActiveThreadId: () => "thread", getActiveThreadSessionName: () => "已命名会话",
    getActiveThreadWorkspacePath: () => "/test", getTraceRuns: () => [], getLiveTraceId: () => "trace", setLiveTraceId: vi.fn(), setTraceDetail: vi.fn(),
    setTraceRuns: vi.fn(), setActiveTraceId: vi.fn(), setThreads: vi.fn(),
  } as never);
});
afterEach(() => { vi.useRealTimers(); useBillingStore.getState().reset(); });

describe("真实执行 store 的积分接线", () => {
  it("只发送已确认预算和稳定消息标识，接收预算耗尽后保留暂停原因", async () => {
    const terminal = event("credit_exhausted", { message: "本次预算已用尽" }) + event("billing", { ...operation, status: "budget_exhausted", charged: 20 });
    const chunks = [frame("billing", operation), frame("model_stream", { content: "已创作的正文" }), new TextEncoder().encode(terminal)];
    mocks.stream.mockResolvedValue({ read: vi.fn(async () => chunks.length ? { done: false, value: chunks.shift() } : { done: true }), cancel: vi.fn() });
    await useExecutionStore.getState().submit("继续写作");
    expect(mocks.stream).toHaveBeenCalledTimes(1);
    expect(mocks.stream.mock.calls[0][1].body).toMatchObject({ billing_protocol: 2, billing_budget: 20 });
    expect(mocks.stream.mock.calls[0][1].body.message_id).toBeTruthy();
    const last = useExecutionStore.getState().messages.at(-1)!;
    expect(last.billingOperationId).toBe("op");
    expect(last.billingOutcome).toBe("budget_exhausted");
    expect(last.status).toBe("stopped");
    expect(last.content).toContain("预算已用尽");
    expect(last.content).toContain("本次已扣 20.00 分");
    expect(last.content).toContain("已创作的正文");
    expect(last.content).not.toContain("账户已冻结");
  });

  it("无效预算不会发送模型请求，也不会把拍板卡改为已回答", async () => {
    useBillingStore.getState().setBudget("100.01");
    useExecutionStore.setState({ messages: [{ role: "assistant", content: "请确认", awaitingInput: { question: "请确认" } }] });
    await expect(useExecutionStore.getState().resume("继续")).rejects.toThrow("超过可用积分");
    expect(mocks.stream).not.toHaveBeenCalled();
    expect(useExecutionStore.getState().messages[0].awaitingInput).toBeDefined();
  });

  it("收费操作掉线后只查询账目，不标停止、不自动重新 POST", async () => {
    vi.useFakeTimers();
    let first = true;
    mocks.stream.mockResolvedValue({ read: vi.fn(async () => {
      if (first) { first = false; return { done: false, value: frame("billing", operation) }; }
      throw new Error("network disconnected");
    }), cancel: vi.fn() });
    await useExecutionStore.getState().submit("继续写作");
    expect(mocks.stream).toHaveBeenCalledTimes(1);
    const last = useExecutionStore.getState().messages.at(-1)!;
    expect(last.billingDetached).toBe(true);
    expect(last.status).toBeUndefined();
    expect(last.queuedInterrupted).toBe(false);
    expect(last.content).toContain("后台继续");
    expect(mocks.account).toHaveBeenCalled();
    vi.clearAllTimers();
  });

  it("服务端拒绝预算时保留拒绝原因，不误报后台继续", async () => {
    mocks.stream.mockResolvedValue({ read: vi.fn(async () => { throw new Error("HTTP 402: 可用积分不足"); }), cancel: vi.fn(async () => {}) });
    await useExecutionStore.getState().submit("继续写作");
    const last = useExecutionStore.getState().messages.at(-1)!;
    expect(last.status).toBe("failed");
    expect(last.content).toContain("可用积分不足");
    expect(last.content).not.toContain("后台继续");
    expect(last.billingDetached).not.toBe(true);
  });

  it("账号切换后到达的旧账单帧不能污染新账号", async () => {
    let release!: (value: { done: boolean; value: Uint8Array }) => void;
    const reader = { read: vi.fn(() => new Promise((resolve) => { release = resolve; })), cancel: vi.fn(async () => {}) };
    mocks.stream.mockResolvedValue(reader);
    const request = useExecutionStore.getState().submit("继续写作");
    await vi.waitFor(() => expect(reader.read).toHaveBeenCalled());
    useBillingStore.getState().setOwner("other");
    await useBillingStore.getState().refresh();
    release({ done: false, value: frame("billing", { ...operation, operation_id: "late-old-account-operation" }) });
    await request;
    expect(useBillingStore.getState().account?.operations).not.toEqual(expect.arrayContaining([expect.objectContaining({ operation_id: "late-old-account-operation" })]));
    expect(reader.cancel).toHaveBeenCalled();
  });

  it.each([true, false])("停止请求失败时不假报已停止；新计费开关为 %s", async (enabled) => {
    const cancel = vi.fn();
    useBillingStore.setState({ account: { ...account, enabled, operations: [] } });
    useExecutionStore.setState({ loading: true, streamReader: { read: vi.fn(), cancel }, messages: [{ role: "assistant", content: "正在创作", status: "streaming", billingManaged: true }] });
    vi.mocked(apiFetch).mockResolvedValue({ ok: false } as never);
    useExecutionStore.getState().stop();
    await vi.waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(useExecutionStore.getState().messages[0].status).toBe("streaming");
    expect(cancel).not.toHaveBeenCalled();
  });

  it("停止回执未取消任务时查询原操作和消息，已完成任务保持完成", async () => {
    const cancel = vi.fn();
    useExecutionStore.setState({ loading: true, streamReader: { read: vi.fn(), cancel }, messages: [
      { role: "user", content: "继续写作", messageId: "request" },
      { role: "assistant", content: "正在创作", status: "streaming", billingManaged: true, billingOperationId: "op" },
    ] });
    vi.mocked(apiFetch).mockResolvedValue({ ok: true, json: async () => ({ status: "accepted", task_cancelled: false, queue_cleared: 0 }) } as never);
    vi.mocked(fetchBillingOperation).mockResolvedValue({ ...operation, status: "completed", charged: 2, result: { content: "完成的正文" }, calls: [] });
    vi.mocked(fetchThreadConversation).mockResolvedValue([
      { id: "request", role: "user", content: "继续写作" }, { id: "answer", role: "assistant", content: "服务器已保存的正文", status: "completed", trace_id: "trace" },
    ] as never);
    vi.mocked(fetchThreadRunState).mockResolvedValue({ status: "completed", trace_id: "trace" } as never);
    useExecutionStore.getState().stop();
    await vi.waitFor(() => expect(useExecutionStore.getState().messages.at(-1)?.status).toBe("completed"));
    expect(fetchBillingOperation).toHaveBeenCalledWith("op");
    expect(fetchThreadConversation).toHaveBeenCalledWith("thread");
    expect(useExecutionStore.getState().messages.at(-1)?.content).toBe("服务器已保存的正文");
    expect(cancel).not.toHaveBeenCalled();
  });

  it("关闭新计费后，尚未收到操作编号的在途付费任务仍等待停止回执", async () => {
    let release!: (value: { done: boolean; value: undefined }) => void;
    const reader = { read: vi.fn(() => new Promise((resolve) => { release = resolve; })), cancel: vi.fn(async () => { release({ done: true, value: undefined }); }) };
    mocks.stream.mockResolvedValue(reader);
    const request = useExecutionStore.getState().submit("继续写作");
    await vi.waitFor(() => expect(reader.read).toHaveBeenCalled());
    useBillingStore.setState({ account: { ...account, enabled: false, operations: [] } });
    let stopReply!: (value: unknown) => void;
    vi.mocked(apiFetch).mockReturnValue(new Promise((resolve) => { stopReply = resolve; }) as never);
    useExecutionStore.getState().stop();
    expect(reader.cancel).not.toHaveBeenCalled();
    expect(useExecutionStore.getState().messages.at(-1)?.status).not.toBe("stopped");
    stopReply({ ok: true, json: async () => ({ task_cancelled: true }) });
    await request;
    expect(reader.cancel).toHaveBeenCalledTimes(1);
    expect(useExecutionStore.getState().messages.at(-1)?.status).toBe("stopped");
  });

  it("停止未确认且操作仍在准备时，不把旧运行空闲误判为已停止", async () => {
    const cancel = vi.fn();
    useExecutionStore.setState({ loading: true, streamReader: { read: vi.fn(), cancel }, messages: [
      { role: "user", content: "继续写作", messageId: "request" },
      { role: "assistant", content: "正在执行...", billingManaged: true, billingOperationId: "op" },
    ] });
    vi.mocked(apiFetch).mockResolvedValue({ ok: true, json: async () => ({ task_cancelled: false }) } as never);
    vi.mocked(fetchBillingOperation).mockResolvedValue({ ...operation, status: "reserved", trace_id: null, result: null, calls: [] });
    vi.mocked(fetchThreadRunState).mockResolvedValue({ status: "idle", trace_id: null } as never);
    useExecutionStore.getState().stop();
    await vi.waitFor(() => expect(mocks.info).toHaveBeenCalled());
    expect(useExecutionStore.getState().messages.at(-1)?.status).toBeUndefined();
    expect(cancel).not.toHaveBeenCalled();
  });

  it("非行模式正文不会覆盖系统故障后的退款说明", async () => {
    const chunks = [frame("billing", operation), frame("model_stream", { content: "保留的正文" }), frame("billing_error", { operation_id: "op", message: "供应商错误" })];
    mocks.stream.mockResolvedValue({ read: vi.fn(async () => chunks.length ? { done: false, value: chunks.shift() } : { done: true }), cancel: vi.fn() });
    await useExecutionStore.getState().submit("继续写作");
    const last = useExecutionStore.getState().messages.at(-1)!;
    expect(last.status).toBe("failed");
    expect(last.content).toContain("保留的正文");
    expect(last.content).toContain("按规则退分");
    expect(last.content).toContain("最终退款状态请查看账单");
  });
});
