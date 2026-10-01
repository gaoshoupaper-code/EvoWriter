/**
 * REQ-20261001-154621 AC-003：写作流断流落 failed 终态
 *
 * 根因：SSE 流正常关闭（done）但未收到 final 事件时，消息不落任何终态，
 * tools 残留 running → 思考态永挂。
 *
 * 验证：mock 流吐出 run_start + tool_call 后直接 done（无 final）→
 * 消息 data-status=failed、显示失败文案与重试按钮、不再渲染思考态组件。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

// ── SSE 流：run_start → tool_call → done（无 final，模拟断流）──
const encoder = new TextEncoder();
function sseEvent(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

const BROKEN_CHUNKS: Uint8Array[] = [
  encoder.encode(sseEvent("trace_event", {
    trace_id: "trace-broken", event_id: "evt-1", sequence: 1, type: "run_start", status: "running",
    timestamp: "2026-01-01T00:00:00Z", source: "system",
    input: { workspace_id: "ws-1", thread_id: "thread-1", session_name: "测试会话", endpoint: "screenplay.generate.stream" },
  })),
  encoder.encode(sseEvent("tool_call", {
    tool: "task", call_id: "call-1", subagent_name: "storybuilding", subagent_type: "storybuilding", iteration: 1,
  })),
].map((e) => e);

vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation(() => {
    let index = 0;
    return Promise.resolve({
      read: () => {
        if (index < BROKEN_CHUNKS.length) {
          return Promise.resolve({ done: false, value: BROKEN_CHUNKS[index++] });
        }
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
    workspaces: [{ workspace_id: "ws-1", title: "测试", domain: "writing", workspace_path: "/test",
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", session_count: 1, active_style_id: null }],
    styles: [],
  }),
  fetchWorkspaceBootstrap: vi.fn().mockResolvedValue({
    threads: [{ thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话", workspace_path: "/test",
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }],
    outline: null, detail_outline: null, characters: null, novel: null, worldview: null,
    storyline: { index_markdown: "# 主线\n既有大纲", entries: [] },
  }),
  fetchThreadTraces: vi.fn().mockResolvedValue([]),
  fetchTraceDetail: vi.fn().mockResolvedValue(null),
  createThread: vi.fn().mockResolvedValue({ thread_id: "thread-1", workspace_id: "ws-1", session_name: "会话 1", workspace_path: "/test", created_at: "", updated_at: "" }),
  updateThread: vi.fn().mockResolvedValue({ thread_id: "thread-1", workspace_id: "ws-1", session_name: "renamed", workspace_path: "/test", created_at: "", updated_at: "" }),
  deleteThread: vi.fn().mockResolvedValue({}),
  createWorkspace: vi.fn(), deleteWorkspace: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  deleteTrace: vi.fn(), logout: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(),
  workspaceNovelPdfUrl: vi.fn().mockReturnValue(""), workspaceNovelWordUrl: vi.fn().mockReturnValue(""),
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
const { render } = await import("@testing-library/react");
const Home = (await import("@/pages/home")).default;

function renderHome() {
  return render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
}

describe("AC-003: 写作流断流（done 无 final）落 failed 终态", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
  });

  it("流断后消息标记 failed、显示重试按钮、不渲染思考态", async () => {
    const user = userEvent.setup();
    renderHome();

    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await user.type(input, "写一个关于勇气的故事");
    await user.keyboard("{Enter}");

    // 断流收敛后：最后一条 assistant 落 failed
    await vi.waitFor(() => {
      const messages = document.querySelectorAll(".message.assistant");
      const last = messages[messages.length - 1];
      expect(last).toHaveAttribute("data-status", "failed");
    }, { timeout: 10000 });

    // 失败态 UI：重试按钮存在；思考态组件不再渲染
    expect(await screen.findByText("↻ 再试一次")).toBeInTheDocument();
    expect(document.querySelector(".yan-thinking")).toBeNull();
    expect(screen.getByText(/连接中断/)).toBeInTheDocument();
  });
});
