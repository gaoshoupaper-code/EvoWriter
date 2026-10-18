/**
 * 阶段制会话集成测试（REQ-20261018-175526 主线验收）。
 *
 * 覆盖：SSE pipeline 事件消费（DEC-013）→ 完成卡片（FR-004/DEC-003）→
 * 点击切换 / 隐式切换（FR-005/DEC-012）→ 新会话绑定新阶段 + 引导语（FR-003）→
 * 阶段条与等拍板标记（FR-002/010）→ 无会话操作（FR-001/AC-001）→
 * 跳段后退标注（FR-006/AC-007 场景卡片文案）。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

const encoder = new TextEncoder();
function sseEvent(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

function finalEvent(threadId: string): string {
  return sseEvent("final", {
    mode: "screenplay", thread_id: threadId, workspace_id: "ws-1", session_name: "s",
    workspace_path: "/t", title: "T", content: "完成", logline: "", synopsis: "",
    beats: [], markdown: "完成", evaluation_markdown: "",
  });
}

// 每个用例自行组块（pipeline 事件的 phase 不同）
let SSE_CHUNKS: Uint8Array[] = [];
vi.mock("@/lib/stream", () => ({
  streamRequest: vi.fn().mockImplementation(() => {
    let index = 0;
    return Promise.resolve({
      read: () => {
        if (index < SSE_CHUNKS.length) return Promise.resolve({ done: false, value: SSE_CHUNKS[index++] });
        return Promise.resolve({ done: true, value: undefined });
      },
      cancel: () => Promise.resolve(),
    });
  }),
}));

const createThreadMock = vi.fn();
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
    outline: { kind: "five", pipeline: { kind: "five", phase: "theme", next_unit: "题材", awaiting: {} }, files: [] },
    detail_outline: null, characters: null, novel: null, worldview: null, storyline: null,
  }),
  fetchThreadTraces: vi.fn().mockResolvedValue([]),
  fetchTraceDetail: vi.fn().mockResolvedValue(null),
  createThread: (...args: unknown[]) => createThreadMock(...args),
  updateThread: vi.fn().mockResolvedValue({ thread_id: "x", workspace_id: "ws-1", session_name: "n", workspace_path: "", created_at: "", updated_at: "" }),
  deleteThread: vi.fn().mockResolvedValue({}),
  createWorkspace: vi.fn(), deleteWorkspace: vi.fn(),
  activateStyle: vi.fn(), createStyle: vi.fn(), updateStyle: vi.fn(), deleteStyle: vi.fn(), optimizeStyle: vi.fn(),
  deleteTrace: vi.fn(), logout: vi.fn(), trackCopy: vi.fn(), trackRegenerate: vi.fn(),
  workspaceNovelPdfUrl: () => "", workspaceNovelWordUrl: () => "",
}));

vi.mock("@/lib/usePanelPolling", () => ({ usePanelPolling: () => {} }));
vi.mock("@/components/workspace/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/workspace/TopBar", () => ({ TopBar: () => null }));
vi.mock("@/components/workspace/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/workspace/ConfirmDialog", () => ({ ConfirmDialog: () => null }));

const { MemoryRouter } = await import("react-router-dom");
const { render } = await import("@testing-library/react");
const Home = (await import("@/pages/home")).default;
const { useWorkspaceStore } = await import("@/stores/workspace");
const { useContentStore } = await import("@/stores/content");
const { useStageSessionStore } = await import("@/stores/stage-session");

function renderHome() {
  return render(<MemoryRouter initialEntries={["/"]}><Home /></MemoryRouter>);
}

/** 绑定当前会话阶段（等价于首轮提交时的自动绑定）。 */
async function bindActive(stage: string) {
  const ws = useWorkspaceStore.getState();
  useStageSessionStore.getState().bindThreadStage(ws.activeWorkspaceId, ws.activeThreadId, stage);
}

describe("阶段制会话主线", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    SSE_CHUNKS = [];
    createThreadMock.mockReset();
    const { resetStoresWithOutline: resetStores } = await import("./helpers");
    await resetStores();
    useStageSessionStore.setState({ threadStageMeta: new Map(), pendingTransition: null, historyView: null });
    useContentStore.setState({
      outlineKind: "five",
      outlinePipeline: { kind: "five", phase: "theme", next_unit: "题材", awaiting: {} },
      outlineFiles: [], activeOutlineRel: "",
    });
  });

  it("AC-001/002：无会话操作入口；阶段条五段显示且当前阶段高亮", async () => {
    renderHome();
    await screen.findByRole("textbox", {}, { timeout: 10000 });

    // FR-001：会话操作不复存在
    expect(screen.queryByRole("button", { name: /新建会话/ })).not.toBeInTheDocument();
    expect(screen.queryByText("历史会话")).not.toBeInTheDocument();

    // FR-002：五段横排 + 主题（当前）高亮；点非当前阶段开只读历史（AC-002）
    const nav = screen.getByRole("navigation", { name: "创作阶段" });
    for (const name of ["主题", "总纲", "卷纲", "章纲", "正文"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(nav.querySelector(".stage-bar-step.current .stage-bar-name")).toHaveTextContent("主题");
    expect(createThreadMock).not.toHaveBeenCalled();
  });

  it("AC-004/AC-003：SSE 阶段事件 → 完成卡片 → 点击进入新阶段新会话+引导语", async () => {
    const user = userEvent.setup();
    renderHome();
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await bindActive("theme");

    SSE_CHUNKS = [
      encoder.encode(sseEvent("pipeline", { kind: "five", phase: "outline", next_unit: "总纲", awaiting: {}, agent: "author" })),
      encoder.encode(sseEvent("model_stream", { content: "总纲完成" })),
      encoder.encode(finalEvent("thread-1")),
    ];
    await user.type(input, "写总纲");
    await user.keyboard("{Enter}");

    // DEC-013：SSE pipeline 事件即时更新相位
    await waitFor(() => {
      expect(useContentStore.getState().outlinePipeline?.phase).toBe("outline");
    }, { timeout: 10000 });

    // FR-004：完成卡片出现（主题完成 → 进入总纲）
    const card = await screen.findByTestId("stage-transition-card", {}, { timeout: 2000 });
    expect(card).toHaveTextContent("主题完成");
    expect(card).toHaveTextContent("进入总纲");

    // 点击主按钮：归档旧会话、开新会话（FR-004 步骤 2）
    createThreadMock.mockResolvedValue({ thread_id: "thread-2", workspace_id: "ws-1", session_name: "n", workspace_path: "", created_at: "", updated_at: "" });
    await user.click(screen.getByRole("button", { name: "进入总纲" }));

    await waitFor(() => {
      expect(useWorkspaceStore.getState().activeThreadId).toBe("thread-2");
    });
    expect(useStageSessionStore.getState().threadStageMeta.get("thread-2")?.stage).toBe("outline");
    expect(useStageSessionStore.getState().pendingTransition).toBeNull();
    expect(useWorkspaceStore.getState().threads.map((t) => t.thread_id)).toContain("thread-1");

    // FR-003：新会话空页面显示总纲阶段引导语
    await waitFor(() => {
      expect(screen.getByTestId("stage-guide")).toHaveTextContent("主题已定，开始搭故事骨架");
    });
  });

  it("AC-005/AC-007：章纲↔正文后退切换，卡片标注「应正文申请」", async () => {
    renderHome();
    await screen.findByRole("textbox", {}, { timeout: 10000 });
    await bindActive("text");

    // 阶段后退（text → chapter）：跳段申请来源标注
    act(() => {
      useContentStore.getState().setOutlinePipeline({ kind: "five", phase: "chapter", next_unit: "章纲", awaiting: {}, agent: "author" });
    });

    const card = await screen.findByTestId("stage-transition-card", {}, { timeout: 2000 });
    expect(card).toHaveTextContent("应正文申请，回到章纲");
    // 历史命名带来源标记（FR-006 显示名规则已在 stage-session.test 验证）
  });

  it("AC-006：卡片挂起时直接发消息 = 隐式切换，消息进新会话", async () => {
    const user = userEvent.setup();
    renderHome();
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await bindActive("theme");

    act(() => {
      useContentStore.getState().setOutlinePipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {}, agent: "author" });
    });
    await screen.findByTestId("stage-transition-card", {}, { timeout: 10000 });

    // 不点卡片，直接打字发送（DEC-012）
    createThreadMock.mockResolvedValue({ thread_id: "thread-3", workspace_id: "ws-1", session_name: "n", workspace_path: "", created_at: "", updated_at: "" });
    SSE_CHUNKS = [encoder.encode(finalEvent("thread-3"))];
    await user.type(input, "直接继续");
    await user.keyboard("{Enter}");

    const { streamRequest } = await import("@/lib/stream");
    await waitFor(() => {
      // 先切换（新会话）再提交：消息发进新会话 thread-3
      expect(streamRequest).toHaveBeenCalledWith(
        expect.anything(),
        expect.objectContaining({
          body: expect.objectContaining({ thread_id: "thread-3", prompt: "直接继续" }),
        }),
      );
    }, { timeout: 10000 });
    expect(useWorkspaceStore.getState().activeThreadId).toBe("thread-3");
    expect(useStageSessionStore.getState().threadStageMeta.get("thread-3")?.stage).toBe("outline");
  });

  it("AC-011：等拍板时当前阶段显示「等你拍板」，拍板完成后消失", async () => {
    renderHome();
    await screen.findByRole("textbox", {}, { timeout: 10000 });

    act(() => {
      useContentStore.getState().setOutlinePipeline({ kind: "five", phase: "theme", next_unit: "题材", awaiting: { confirm: "1" }, agent: "author" });
    });

    const nav = screen.getByRole("navigation", { name: "创作阶段" });
    await waitFor(() => {
      expect(nav.querySelector(".stage-bar-step.current")).toHaveTextContent("等你拍板");
    });

    act(() => {
      useContentStore.getState().setOutlinePipeline({ kind: "five", phase: "theme", next_unit: "题材", awaiting: {}, agent: "author" });
    });
    await waitFor(() => {
      expect(nav.querySelector(".stage-bar-step.current")).not.toHaveTextContent("等你拍板");
    });
  });

  it("P0 回归：新书无会话时首条消息自动建会话并绑定阶段（对话即入口）", async () => {
    const user = userEvent.setup();
    const api = await import("@/lib/api");
    // mockResolvedValueOnce：仅本用例生效，不泄漏到后续用例（clearAllMocks 不清实现）
    vi.mocked(api.fetchWorkspaceBootstrap).mockResolvedValueOnce({
      threads: [],
      outline: { kind: "five", pipeline: { kind: "five", phase: "theme", next_unit: "题材", awaiting: {} }, files: [] },
    } as any);

    renderHome();
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    expect(useWorkspaceStore.getState().activeThreadId).toBe("");

    createThreadMock.mockResolvedValue({ thread_id: "fresh-1", workspace_id: "ws-1", session_name: "n", workspace_path: "", created_at: "", updated_at: "" });
    SSE_CHUNKS = [encoder.encode(finalEvent("fresh-1"))];
    await user.type(input, "新书第一句");
    await user.keyboard("{Enter}");

    const { streamRequest } = await import("@/lib/stream");
    await waitFor(() => {
      expect(streamRequest).toHaveBeenCalledWith(
        expect.anything(),
        expect.objectContaining({ body: expect.objectContaining({ thread_id: "fresh-1", prompt: "新书第一句" }) }),
      );
    }, { timeout: 10000 });
    // 首轮绑定当前阶段
    expect(useStageSessionStore.getState().threadStageMeta.get("fresh-1")?.stage).toBe("theme");
  });

  it("AC-006 边界：隐式切换建会话失败时消息不发送、输入不丢（FR-005 失败语义）", async () => {
    const user = userEvent.setup();
    renderHome();
    const input = await screen.findByRole("textbox", {}, { timeout: 10000 });
    await bindActive("theme");

    act(() => {
      useContentStore.getState().setOutlinePipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {}, agent: "author" });
    });
    await screen.findByTestId("stage-transition-card", {}, { timeout: 10000 });

    createThreadMock.mockRejectedValue(new Error("network down"));
    await user.type(input, "直接继续");
    await user.keyboard("{Enter}");

    const { streamRequest } = await import("@/lib/stream");
    await waitFor(() => {
      expect(createThreadMock).toHaveBeenCalled();
    });
    expect(streamRequest).not.toHaveBeenCalled();
    // 输入保留在框里不丢
    await waitFor(() => {
      expect(input).toHaveValue("直接继续");
    });
  });

  it("FR-004：完成卡片次链接——查看产物切产物面板并选中、翻历史打开该阶段列表", async () => {
    const user = userEvent.setup();
    renderHome();
    await screen.findByRole("textbox", {}, { timeout: 10000 });
    await bindActive("theme");
    useContentStore.setState({
      outlineFiles: [
        { rel: "主题.md", markdown: "" },
        { rel: "总纲.md", markdown: "" },
      ],
    });

    act(() => {
      useContentStore.getState().setOutlinePipeline({ kind: "five", phase: "outline", next_unit: "总纲", awaiting: {}, agent: "author" });
    });
    const card = await screen.findByTestId("stage-transition-card", {}, { timeout: 2000 });

    // 先测「翻历史」（不切面板，卡片存活）
    await user.click(within(card).getByRole("button", { name: "翻主题历史" }));
    await waitFor(() => {
      expect(useStageSessionStore.getState().historyView).toEqual({ stage: "theme", threadId: null });
    });
    await user.click(screen.getByRole("button", { name: "← 返回当前对话" }));

    // 再测「查看产物」（切到产物面板，ChatPanel 卸载，卡片不复用）
    const card2 = await screen.findByTestId("stage-transition-card", {}, { timeout: 2000 });
    await user.click(within(card2).getByRole("button", { name: "查看主题产物" }));
    expect(useContentStore.getState().activeOutlineRel).toBe("主题.md");
    expect(useWorkspaceStore.getState().activePanel).toBe("artifacts");
  });

  it("AC-002 边界：点非当前阶段打开该阶段历史列表，不创建会话", async () => {
    const user = userEvent.setup();
    renderHome();
    await screen.findByRole("textbox", {}, { timeout: 10000 });

    await user.click(screen.getByRole("button", { name: "正文" }));

    await waitFor(() => {
      expect(screen.getByText("正文阶段历史")).toBeInTheDocument();
    });
    // 当前活动会话不出现在历史（review P2 修复）：thread-1 是当前会话，被排除后列表为空
    expect(screen.getByText("该阶段还没有历史对话。")).toBeInTheDocument();
    expect(screen.queryByText("早期对话（未记录阶段）")).not.toBeInTheDocument();
    expect(createThreadMock).not.toHaveBeenCalled();
    // 返回当前对话
    await user.click(screen.getByRole("button", { name: "← 返回当前对话" }));
    await waitFor(() => {
      expect(screen.queryByText("正文阶段历史")).not.toBeInTheDocument();
    });
  });

  it("AC-014：全部阶段文案显示「主题」，不出现「题材」", async () => {
    renderHome();
    await screen.findByRole("textbox", {}, { timeout: 10000 });
    const panel = document.querySelector(".conversation-panel")!;
    expect(panel.textContent).toContain("主题");
    expect(panel.textContent).not.toContain("题材");
  });
});
