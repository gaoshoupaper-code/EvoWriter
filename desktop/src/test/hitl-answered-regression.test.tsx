/**
 * 回归测试：拍板卡（HITL）用户选择后，卡片所在消息已输出的内容不得消失。
 *
 * 场景（行模式主线）：
 *   1. 首轮流：run_start → row（思考/正文）→ interrupt（question + options）
 *   2. 用户在拍板卡上选择并提交 → resume
 *   3. 断言：提问文案与该消息已输出的正文仍可见（不随 awaitingInput 清空蒸发）
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

// 第一轮流：行模式（思考行 + 正文行）→ interrupt 提问
const INTERRUPT_ROW_CHUNKS = encode([
  sseEvent("trace_event", {
    trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
    timestamp: "2026-01-01T00:00:00Z", source: "system",
    input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
  }),
  sseEvent("row", { op: "open", seq: 1, id: "row-1", kind: "reasoning" }),
  sseEvent("row", { op: "delta", id: "row-1", text: "正在设计第 3 章的冲突走向" }),
  sseEvent("row", { op: "end", id: "row-1", state: "complete" }),
  sseEvent("row", { op: "open", seq: 2, id: "row-2", kind: "text" }),
  sseEvent("row", { op: "delta", id: "row-2", text: "本章有两种走法，需要你拍板。" }),
  sseEvent("row", { op: "end", id: "row-2", state: "complete" }),
  sseEvent("interrupt", {
    kind: "choice",
    question: "第 3 章主角是否遇到反派？",
    options: [
      { label: "遇到，加些冲突", description: "让主角在此章遇到反派" },
      { label: "先铺垫情绪", description: "下章再遇反派" },
    ],
    multi_select: false,
    source: "writing-subagent",
  }),
]);

// 第二轮流（resume）：正文行 → final
const RESUME_FINAL_CHUNKS = encode([
  sseEvent("row", { op: "open", seq: 3, id: "row-3", kind: "text" }),
  sseEvent("row", { op: "delta", id: "row-3", text: "收到，按「遇到反派」展开第 3 章。" }),
  sseEvent("row", { op: "end", id: "row-3", state: "complete" }),
  sseEvent("final", {
    mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话",
    workspace_path: "/test", title: "T", content: "正文完成", logline: "", synopsis: "",
    beats: [],
  }),
]);

let streams: Uint8Array[][] = [];
vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation(() => {
    const chunks = streams.shift() ?? RESUME_FINAL_CHUNKS;
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
  fetchThreadRunState: vi.fn().mockResolvedValue(null),
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
// InterviewOptions 用真实组件形态的简化占位：选项按钮 + 提交回调（选择即提交）
vi.mock("@/components/workspace/InterviewOptions", () => ({
  InterviewOptions: ({ options, onSubmit }: { options: { label: string }[]; onSubmit: (t: string) => void }) => (
    <div data-testid="interview-options">
      {options.map((o, i) => (
        <button key={i} type="button" onClick={() => onSubmit(o.label)}>{o.label}</button>
      ))}
    </div>
  ),
}));
vi.mock("@/components/workspace/ImageReviewCard", () => ({ ImageReviewCard: () => null }));

const { MemoryRouter } = await import("react-router-dom");
const { render } = await import("@testing-library/react");
const Home = (await import("@/pages/home")).default;
const { useExecutionStore } = await import("@/stores/execution");

describe("拍板卡选择后内容保留（回归）", () => {
  beforeEach(async () => {
    streams = [INTERRUPT_ROW_CHUNKS, RESUME_FINAL_CHUNKS];
    vi.clearAllMocks();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
    useExecutionStore.setState({ messages: [], hasHistory: false });
  });

  it("选择提交后：提问文案与已输出正文仍可见", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });

    await user.type(input, "写第 3 章");
    await user.keyboard("{Enter}");

    // 拍板卡出现（提问 + 选项）
    await screen.findByTestId("interview-question", {}, { timeout: 10000 });
    expect(screen.getByTestId("interview-question")).toHaveTextContent("第 3 章主角是否遇到反派？");

    // 用户选择并提交（触发 resume）
    await user.click(screen.getByRole("button", { name: "遇到，加些冲突" }));

    // resume 流结束（final 到达，loading 归 false）
    await waitFor(() => {
      expect(useExecutionStore.getState().loading).toBe(false);
    }, { timeout: 10000 });

    // 回归断言：提问沉淀为正文行常显，不随 awaitingInput 清空蒸发
    const panelText = document.querySelector(".conversation-panel")?.textContent ?? "";
    expect(panelText).toContain("第 3 章主角是否遇到反派？");

    // 已输出的正文行也保留
    expect(panelText).toContain("本章有两种走法");

    // 僵尸态收敛：不再显示「已暂停 · 等你回答」，定格为「已拍板」
    expect(panelText).not.toContain("已暂停 · 等你回答");
    expect(panelText).toContain("已拍板");

    // 消息定格：status=answered、awaitingInput 清空、提问进 rows/content
    const answeredMsg = useExecutionStore
      .getState()
      .messages.findLast((m) => m.role === "assistant" && m.status === "answered");
    expect(answeredMsg?.awaitingInput).toBeUndefined();
    expect(answeredMsg?.rows?.at(-1)?.kind).toBe("text");
    expect(answeredMsg?.rows?.at(-1)?.text).toBe("第 3 章主角是否遇到反派？");
    expect(answeredMsg?.content).toContain("第 3 章主角是否遇到反派？");
  });
});
