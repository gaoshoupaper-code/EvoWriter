/**
 * 重启回现场集成测试（REQ-20261001-170627 FR-001/FR-002 / AC-001/AC-002）。
 *
 * 全链路（真实 session-persist 模块 + mock plugin-store 内存文件系统）：
 * 1. 第一次挂载 Home → 完成一轮对话（mock SSE final）→ 终版落盘 + 位置记忆
 * 2. 卸载 + 重置内存 store（模拟重启：内存 Map 清空、本地文件保留）
 * 3. 第二次挂载 Home → bootstrap 读位置 → 自动落回原会话 → 消息完整恢复
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

const encoder = new TextEncoder();
function sseEvent(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}
function encode(events: string[]): Uint8Array[] {
  return events.map((e) => encoder.encode(e));
}

// ── plugin-store 内存文件系统：跨「重启」存活 ──
const fileSystem = new Map<string, Map<string, unknown>>();
vi.mock("@tauri-apps/plugin-store", () => ({
  load: async (path: string) => {
    if (!fileSystem.has(path)) fileSystem.set(path, new Map());
    const map = fileSystem.get(path)!;
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

const FINAL_CHUNKS = encode([
  sseEvent("trace_event", {
    trace_id: "trace-1", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
    timestamp: "2026-01-01T00:00:00Z", source: "system",
    input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
  }),
  sseEvent("model_stream", { content: "第一轮生成的内容" }),
  sseEvent("final", {
    mode: "screenplay", thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话",
    workspace_path: "/test", title: "T", content: "第一轮生成的内容", logline: "", synopsis: "", beats: [],
  }),
]);

let streamCallCount = 0;
vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation(() => {
    streamCallCount++;
    let index = 0;
    return Promise.resolve({
      read: () => {
        if (index < FINAL_CHUNKS.length) return Promise.resolve({ done: false, value: FINAL_CHUNKS[index++] });
        return Promise.resolve({ done: true, value: undefined });
      },
      cancel: () => Promise.resolve(),
    });
  }),
}));

const threadSummary = { thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话", workspace_path: "/test", created_at: "", updated_at: "" };

vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  fetchMeOrNull: vi.fn().mockResolvedValue({ user_id: "u1", username: "testuser", is_admin: false, has_api_key: true }),
  fetchInit: vi.fn().mockResolvedValue({
    workspaces: [{ workspace_id: "ws-1", title: "T", domain: "writing", workspace_path: "/test", created_at: "", updated_at: "", session_count: 1, active_style_id: null }],
    styles: [],
  }),
  fetchWorkspaceBootstrap: vi.fn().mockResolvedValue({
    threads: [threadSummary],
    outline: null, detail_outline: null, characters: null, novel: null, worldview: null,
    storyline: { index_markdown: "# 主线\n既有大纲", entries: [] },
  }),
  fetchThreadTraces: vi.fn().mockResolvedValue([]),
  fetchTraceDetail: vi.fn().mockResolvedValue(null),
  createThread: vi.fn(),
  updateThread: vi.fn(), deleteThread: vi.fn(),
  createWorkspace: vi.fn(), deleteWorkspace: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  deleteTrace: vi.fn(), logout: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(),
  workspaceNovelPdfUrl: () => "", workspaceNovelWordUrl: () => "",
}));

vi.mock("@/lib/usePanelPolling", () => ({ usePanelPolling: () => {} }));
vi.mock("@/components/workspace/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/workspace/TopBar", () => ({ TopBar: () => null }));
vi.mock("@/components/workspace/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/workspace/StyleModal", () => ({ StyleModal: () => null }));
vi.mock("@/components/workspace/ConfirmDialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/workspace/TracePanel", () => ({ TracePanel: () => null }));
vi.mock("@/components/workspace/NovelPanel", () => ({ NovelPanel: () => null }));
vi.mock("@/components/workspace/ScriptPanel", () => ({ ScriptPanel: () => null }));
vi.mock("@/components/workspace/DetailOutlinePanel", () => ({ DetailOutlinePanel: () => null }));
vi.mock("@/components/workspace/CharactersPanel", () => ({ CharactersPanel: () => null }));
vi.mock("@/components/workspace/WorldviewPanel", () => ({ WorldviewPanel: () => null }));
vi.mock("@/components/workspace/StorylinePanel", () => ({ StorylinePanel: () => null }));
vi.mock("@/components/workspace/InterviewOptions", () => ({ InterviewOptions: () => null }));
vi.mock("@/components/workspace/ImageReviewCard", () => ({ ImageReviewCard: () => null }));
vi.mock("@/components/workspace/SessionMenu", () => ({ SessionMenu: () => null }));

const { MemoryRouter } = await import("react-router-dom");
const { render, unmountAll } = await import("./restart-mount");
const Home = (await import("@/pages/home")).default;
const { useWorkspaceStore } = await import("@/stores/workspace");
const { useExecutionStore, _resetExecutionPersistForTests } = await import("@/stores/execution");
const { _resetPersistForTests } = await import("@/lib/session-persist");

async function mountHome() {
  render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
}

describe("重启回现场（AC-001/AC-002 集成链路）", () => {
  beforeEach(async () => {
    streamCallCount = 0;
    fileSystem.clear();
    vi.clearAllMocks();
    _resetExecutionPersistForTests();
    const { resetStoresWithOutline } = await import("./helpers");
    await resetStoresWithOutline();
  });

  it("对话落盘后模拟重启（清内存保文件），重新挂载自动回到原会话并恢复消息", async () => {
    // ── 第一次会话：提交并完成一轮对话 ──
    const user = userEvent.setup();
    await mountHome();

    // bootstrap 完成前提交会被「未选工作目录」早退——先等会话落位
    await vi.waitFor(() => {
      expect(useWorkspaceStore.getState().activeThreadId).toBe("thread-1");
    }, { timeout: 5000 });

    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "写一个故事");
    await user.keyboard("{Enter}");

    // SSE 完成后，assistant 消息应包含流式累积的正文
    // （store 更新发生在 act 外，用 act 包裹等待以 flush 渲染）
    await act(async () => {
      await vi.waitFor(() => {
        expect(useExecutionStore.getState().messages.some((m) => m.content === "第一轮生成的内容")).toBe(true);
      }, { timeout: 10000 });
    });
    expect(screen.getByText("第一轮生成的内容")).toBeInTheDocument();

    // 等终版落盘：本地文件出现该会话的完整终版（user 消息 + completed 正文）+ 上次位置
    const storeFile = "sessions/testuser.store";
    await vi.waitFor(() => {
      const file = fileSystem.get(storeFile);
      const record = file?.get("msg:ws-1:thread-1") as { messages?: { role: string; content: string; status?: string }[] } | undefined;
      expect(record?.messages?.some((m) => m.role === "user")).toBe(true);
      expect(record?.messages?.some((m) => m.content === "第一轮生成的内容" && m.status === "completed")).toBe(true);
      expect(file?.get("__last__")).toEqual({ workspaceId: "ws-1", threadId: "thread-1" });
    }, { timeout: 5000 });

    // ── 模拟重启：卸载组件、清空内存态（store Map/订阅），本地文件保留 ──
    unmountAll();
    _resetExecutionPersistForTests();
    const { resetStoresWithOutline: resetAgain } = await import("./helpers");
    await resetAgain();

    // ── 第二次挂载：bootstrap → 读位置 → 落回原会话 → 消息恢复 ──
    await mountHome();

    await act(async () => {
      await vi.waitFor(() => {
        expect(useWorkspaceStore.getState().activeThreadId).toBe("thread-1");
        expect(useExecutionStore.getState().messages.some((m) => m.content === "第一轮生成的内容")).toBe(true);
      }, { timeout: 10000 });
    });
    expect(screen.getByText("第一轮生成的内容")).toBeInTheDocument();

    // 恢复的消息含历史 user 消息（本轮提交的提问）
    expect(useExecutionStore.getState().messages.some((m) => m.role === "user")).toBe(true);
    const lastAssistant = [...useExecutionStore.getState().messages].reverse().find((m) => m.role === "assistant");
    expect(lastAssistant?.status).toBe("completed");
    expect(lastAssistant?.content).toContain("第一轮生成的内容");
  });
});
