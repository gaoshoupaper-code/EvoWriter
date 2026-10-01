/**
 * REQ-20261001-154621 AC-001/AC-002：空会话欢迎消息不得进入执行态
 *
 * 根因：欢迎消息（initialAssistantMessage）无终态 status / tools / traceId，
 * derivePhaseFromMessage 对非空 content 返回 thinking → 永挂 ThinkingView。
 *
 * 验证：
 *   AC-002 无工作区：保留原句「先选择一个工作目录...」且不渲染思考态组件
 *   AC-001 有工作区：欢迎语为邀约式新文案且不渲染思考态组件（初始 + 新建会话两个入口）
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

// ── api mock ──
const fetchInitMock = vi.fn();
vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  fetchMeOrNull: vi.fn().mockResolvedValue({ user_id: "u1", username: "test", is_admin: false, has_api_key: true }),
  fetchInit: fetchInitMock,
  fetchWorkspaceBootstrap: vi.fn().mockResolvedValue({
    threads: [{ thread_id: "thread-1", workspace_id: "ws-1", session_name: "测试会话", workspace_path: "/test",
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }],
    outline: null, detail_outline: null, characters: null, novel: null, worldview: null,
    storyline: { index_markdown: "# 主线\n既有大纲", entries: [] },
  }),
  fetchThreadTraces: vi.fn().mockResolvedValue([]),
  fetchTraceDetail: vi.fn().mockResolvedValue(null),
  createThread: vi.fn().mockResolvedValue({ thread_id: "thread-2", workspace_id: "ws-1", session_name: "会话 2", workspace_path: "/test", created_at: "", updated_at: "" }),
  updateThread: vi.fn().mockResolvedValue({ thread_id: "thread-1", workspace_id: "ws-1", session_name: "renamed", workspace_path: "/test", created_at: "", updated_at: "" }),
  deleteThread: vi.fn().mockResolvedValue({}),
  createWorkspace: vi.fn(), deleteWorkspace: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  deleteTrace: vi.fn(), logout: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(),
  workspaceNovelPdfUrl: vi.fn().mockReturnValue(""), workspaceNovelWordUrl: vi.fn().mockReturnValue(""),
}));

vi.mock("@/lib/usePanelPolling", () => ({ usePanelPolling: () => {} }));

// ── 子组件 mock（passthrough）──
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

// ── Import（在所有 mock 之后）──
const { MemoryRouter } = await import("react-router-dom");
const { render } = await import("@testing-library/react");
const Home = (await import("@/pages/home")).default;

const WELCOME_WITH_WORKSPACE = "我们开始新的故事吧";
const WELCOME_NO_WORKSPACE = "先选择一个工作目录，再开启或恢复创作会话。";

function renderHome() {
  return render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
}

function noThinkingView() {
  return document.querySelector(".yan-thinking") === null;
}

describe("AC-001/AC-002: 空会话欢迎消息不进执行态", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
    fetchInitMock.mockResolvedValue({
      workspaces: [{ workspace_id: "ws-1", title: "测试", domain: "writing", workspace_path: "/test",
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", session_count: 1, active_style_id: null }],
      styles: [],
    });
  });

  it("AC-002: 无工作区时空会话显示原句，无思考态组件", async () => {
    fetchInitMock.mockResolvedValue({ workspaces: [], styles: [] });
    renderHome();

    expect(await screen.findByText(WELCOME_NO_WORKSPACE, {}, { timeout: 10000 })).toBeInTheDocument();
    expect(noThinkingView()).toBe(true);
    expect(screen.queryByText(/正在思考/)).not.toBeInTheDocument();
  });

  it("AC-001: 有工作区时空会话显示邀约式新欢迎语，无思考态组件", async () => {
    renderHome();

    expect(await screen.findByText(new RegExp(WELCOME_WITH_WORKSPACE), {}, { timeout: 10000 })).toBeInTheDocument();
    expect(noThinkingView()).toBe(true);
    expect(screen.queryByText(/正在思考/)).not.toBeInTheDocument();
  });

  it("AC-001: 新建会话入口同样显示新欢迎语，无思考态组件", async () => {
    const user = userEvent.setup();
    renderHome();

    // 等 bootstrap 完成、按钮可用
    const createButton = await screen.findByRole("button", { name: "新建会话" }, { timeout: 10000 });
    await waitFor(() => expect(createButton).not.toBeDisabled(), { timeout: 10000 });
    await user.click(createButton);

    expect(await screen.findByText(new RegExp(WELCOME_WITH_WORKSPACE), {}, { timeout: 10000 })).toBeInTheDocument();
    expect(noThinkingView()).toBe(true);
  });
});
