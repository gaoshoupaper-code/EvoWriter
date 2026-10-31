/**
 * 行级时间线集成测试（REQ-20261018-235653）。
 *
 * 验证 row 事件进入 performSubmit 后的消息形态：
 * - AC-001（store 侧）：多响应 + 工具交错的行流 → rows 按 seq 有序、四类行齐备，
 *   content 由正文行拼接派生（含中间正文）；
 * - FR-005（客户端半边）：row 模式开启后旧 model_stream 增量不再写入 content
 *   （服务端双发期子代理文本走旧通道，不得混入）；
 * - AC-003：row 模式下 interrupt 不覆盖正文，提问走 awaitingInput；
 * - FR-004/FR-006：断流兜底保留行与内容，streaming 行标 interrupted。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

const encoder = new TextEncoder();
function sseEvent(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}
function encode(events: string[]): Uint8Array[] {
  return events.map((e) => encoder.encode(e));
}

const RUN_START = sseEvent("trace_event", {
  trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
  timestamp: "2026-01-01T00:00:00Z", source: "system",
  input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
});

// 完整行流：思考 → 中间正文（确认卡） → 工具 → 子代理聚合行 → 收尾正文
// 中间混入旧通道的子代理文本（model_stream）——row 模式下不得混入 content
const ROW_FULL_CHUNKS = encode([
  RUN_START,
  sseEvent("row", { op: "open", seq: 1, id: "row-1", kind: "reasoning", response_id: "run-1" }),
  sseEvent("row", { op: "delta", id: "row-1", text: "先想想怎么写" }),
  sseEvent("row", { op: "end", id: "row-1", state: "complete" }),
  sseEvent("row", { op: "open", seq: 2, id: "row-2", kind: "text", response_id: "run-1" }),
  sseEvent("row", { op: "delta", id: "row-2", text: "【确认卡】" }),
  sseEvent("row", { op: "end", id: "row-2", state: "complete", text: "【确认卡】全文" }),
  sseEvent("row", { op: "open", seq: 3, id: "c1", kind: "tool", tool: "write_file" }),
  sseEvent("row", { op: "end", id: "c1", state: "complete" }),
  // 旧通道子代理文本（双发期的 legacy 帧）——row 模式已开启，不得进 content
  sseEvent("model_stream", { content: "子代理混流文本" }),
  sseEvent("row", { op: "open", seq: 4, id: "t1", kind: "subagent", subagent_type: "storybuilding", iteration: 1 }),
  sseEvent("row", { op: "end", id: "t1", state: "complete", summary: "第 1 轮产出" }),
  sseEvent("row", { op: "open", seq: 5, id: "row-5", kind: "text", response_id: "run-2" }),
  sseEvent("row", { op: "delta", id: "row-5", text: "收尾一句" }),
  sseEvent("row", { op: "end", id: "row-5", state: "complete", text: "收尾一句" }),
  sseEvent("final", {
    mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话",
    workspace_path: "/test", title: "T", content: "", logline: "", synopsis: "", beats: [],
  }),
]);

// 行流 → 提问中断：正文（确认卡）不得被提问覆盖
const ROW_INTERRUPT_CHUNKS = encode([
  RUN_START,
  sseEvent("row", { op: "open", seq: 1, id: "row-1", kind: "text", response_id: "run-1" }),
  sseEvent("row", { op: "delta", id: "row-1", text: "【确认卡】全文" }),
  sseEvent("row", { op: "end", id: "row-1", state: "complete", text: "【确认卡】全文" }),
  sseEvent("interrupt", {
    kind: "choice", question: "确认这个版本吗？", options: [
      { label: "确认", description: "落盘为约束级" },
      { label: "再改改", description: "说明修改点" },
    ], multi_select: false, source: "interview",
  }),
]);

// 行流中断流（无 final）：streaming 行标 interrupted、内容保留
const ROW_BROKEN_CHUNKS = encode([
  RUN_START,
  sseEvent("row", { op: "open", seq: 1, id: "row-1", kind: "reasoning", response_id: "run-1" }),
  sseEvent("row", { op: "delta", id: "row-1", text: "思考到一半" }),
  sseEvent("row", { op: "open", seq: 2, id: "row-2", kind: "text", response_id: "run-1" }),
  sseEvent("row", { op: "delta", id: "row-2", text: "已输出正文" }),
]);

let streams: Uint8Array[][] = [];
vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation(() => {
    const chunks = streams.shift() ?? ROW_FULL_CHUNKS;
    let index = 0;
    return Promise.resolve({
      read: () => {
        if (index < chunks.length) return Promise.resolve({ done: false, value: chunks[index++] });
        return Promise.resolve({ done: true, value: undefined });
      },
      cancel: () => Promise.resolve(),
    });
  }),
}));

vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  fetchMeOrNull: vi.fn().mockResolvedValue({ user_id: "u1", username: "test", is_admin: false, has_api_key: true }),
  fetchInit: vi.fn().mockResolvedValue({
    workspaces: [{ workspace_id: "ws-1", title: "T", domain: "writing", workspace_path: "/t",
      created_at: "", updated_at: "", session_count: 1, active_style_id: null }],
    styles: [],
  }),
  fetchWorkspaceBootstrap: vi.fn().mockResolvedValue({
    threads: [{ thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话", workspace_path: "/t",
      created_at: "", updated_at: "" }],
    outline: null, detail_outline: null, characters: null, novel: null, worldview: null, storyline: null,
  }),
  fetchThreadTraces: vi.fn().mockResolvedValue([]),
  fetchTraceDetail: vi.fn().mockResolvedValue(null),
  createThread: vi.fn().mockResolvedValue({ thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话", workspace_path: "/t", created_at: "", updated_at: "" }),
  updateThread: vi.fn().mockResolvedValue({ thread_id: "thread-1" }),
  deleteThread: vi.fn().mockResolvedValue({}),
  createWorkspace: vi.fn(), deleteWorkspace: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  deleteTrace: vi.fn(), logout: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(),
  workspaceNovelPdfUrl: () => "", workspaceNovelWordUrl: () => "",
}));

vi.mock("@/lib/usePanelPolling", () => ({ usePanelPolling: () => {}, watchPipelineAfterDisconnect: () => {}, stopDisconnectWatch: () => {} }));

vi.mock("@/components/workspace/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/workspace/TopBar", () => ({ TopBar: () => null }));
vi.mock("@/components/workspace/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/workspace/StyleModal", () => ({ StyleModal: () => null }));
vi.mock("@/components/workspace/ConfirmDialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/workspace/TracePanel", () => ({ TracePanel: () => null }));
vi.mock("@/components/workspace/NovelPanel", () => ({ NovelPanel: () => null }));
vi.mock("@/components/workspace/DetailOutlinePanel", () => ({ DetailOutlinePanel: () => null }));
vi.mock("@/components/workspace/CharactersPanel", () => ({ CharactersPanel: () => null }));
vi.mock("@/components/workspace/WorldviewPanel", () => ({ WorldviewPanel: () => null }));
vi.mock("@/components/workspace/StorylinePanel", () => ({ StorylinePanel: () => null }));
vi.mock("@/components/workspace/InterviewOptions", () => ({
  InterviewOptions: ({ options }: { options: { label: string }[] }) => (
    <div data-testid="interview-options">{options.map((o) => <span key={o.label}>{o.label}</span>)}</div>
  ),
}));

const { MemoryRouter } = await import("react-router-dom");
const { render } = await import("@testing-library/react");
const Home = (await import("@/pages/home")).default;

describe("行级时间线（REQ-20261018-235653）", () => {
  beforeEach(async () => {
    streams = [ROW_FULL_CHUNKS];
    vi.clearAllMocks();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
  });

  async function submitAndWaitIdle() {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "写一个故事");
    await user.keyboard("{Enter}");
    const { useExecutionStore } = await import("@/stores/execution");
    await waitFor(() => {
      expect(useExecutionStore.getState().loading).toBe(false);
    }, { timeout: 10000 });
    const lastMsg = [...useExecutionStore.getState().messages].reverse().find((m) => m.role === "assistant");
    return lastMsg!;
  }

  it("AC-001（store 侧）：行按 seq 有序、四类齐备，content 由正文行派生", async () => {
    const lastMsg = await submitAndWaitIdle();

    expect(lastMsg.status).toBe("completed");
    expect(lastMsg.rows?.map((r) => r.kind)).toEqual(["reasoning", "text", "tool", "subagent", "text"]);
    expect(lastMsg.rows?.map((r) => r.seq)).toEqual([1, 2, 3, 4, 5]);
    expect(lastMsg.rows?.every((r) => r.status === "complete")).toBe(true);
    // 中间正文不丢：content = 两段正文行拼接（FR-002/FR-003）
    expect(lastMsg.content).toBe("【确认卡】全文\n\n收尾一句");
    // 子代理聚合行带摘要（FR-005）
    expect(lastMsg.rows?.[3].summary).toBe("第 1 轮产出");
  });

  it("FR-005（客户端半边）：row 模式下旧通道 model_stream 不混入 content", async () => {
    const lastMsg = await submitAndWaitIdle();
    expect(lastMsg.content).not.toContain("子代理混流文本");
    expect(lastMsg.content).not.toContain("正在执行");
  });

  it("AC-003：interrupt 不覆盖正文——提问走 awaitingInput，确认卡保留", async () => {
    streams = [ROW_INTERRUPT_CHUNKS];
    const lastMsg = await submitAndWaitIdle();

    expect(await screen.findByTestId("interview-options")).toBeInTheDocument();
    expect(lastMsg.content).toBe("【确认卡】全文");
    expect(lastMsg.awaitingInput?.question).toBe("确认这个版本吗？");
    expect(lastMsg.rows?.[0].status).toBe("complete");
    expect(lastMsg.status).not.toBe("failed");
    expect(lastMsg.content).not.toContain("连接中断");
  });

  it("FR-004/FR-006：断流兜底保留行与内容，streaming 行标 interrupted", async () => {
    streams = [ROW_BROKEN_CHUNKS];
    const lastMsg = await submitAndWaitIdle();

    expect(lastMsg.status).toBe("failed");
    expect(lastMsg.content).toContain("已输出正文");
    expect(lastMsg.content).toContain("连接中断");
    const statuses = lastMsg.rows?.map((r) => r.status);
    expect(statuses).toEqual(["interrupted", "interrupted"]);
    expect(lastMsg.rows?.[1].text).toBe("已输出正文");
  });
});
