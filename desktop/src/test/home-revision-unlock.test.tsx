/**
 * 回执阶段输入框卡死验收测试（线上 bug，REQ-20261002）。
 *
 * 复现链路：表单提交 → SSE final → completed（交付/回执态）→ 停前补拉三件套
 * 全空（final 事件先于大纲文件落盘的竞态 / 接口瞬断被静默吞掉）→
 * outlineReady=false → revisionLocked=true → composer 永久禁用；
 * 需求表单也回不来（仅零用户消息时渲染）→ 用户彻底卡死。
 *
 * 修复契约：修订门控只锁首发（会话里还没有用户消息）。首发已发生后
 * 无论三件套是否就绪，composer 必须可用——交付态提示「要改跟我说」就得能输入。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

const encoder = new TextEncoder();
function sseEvent(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}
const SSE_CHUNKS: Uint8Array[] = [
  sseEvent("final", {
    mode: "screenplay",
    thread_id: "thread-1",
    workspace_id: "ws-1",
    session_name: "测试会话",
    workspace_path: "/test",
    title: "测试",
    content: "大纲完成",
    logline: "",
    synopsis: "",
    beats: [],
    markdown: "大纲完成",
    evaluation_markdown: "",
  }),
].map((e) => encoder.encode(e));

const { streamRequest } = vi.hoisted(() => ({ streamRequest: vi.fn() }));
vi.mock("@/lib/stream", () => ({ streamRequest }));
streamRequest.mockImplementation(() => {
  let index = 0;
  return Promise.resolve({
    read: () =>
      index < SSE_CHUNKS.length
        ? Promise.resolve({ done: false, value: SSE_CHUNKS[index++] })
        : Promise.resolve({ done: true, value: undefined }),
    cancel: () => Promise.resolve(),
  });
});

vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  fetchMeOrNull: vi.fn().mockResolvedValue({ user_id: "u1", username: "test", is_admin: false, has_api_key: true }),
  fetchInit: vi.fn().mockResolvedValue({
    workspaces: [{ workspace_id: "ws-1", title: "玄幻测试", domain: "writing", workspace_path: "/test",
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", session_count: 1, active_style_id: null }],
    styles: [],
  }),
  fetchWorkspaceBootstrap: vi.fn().mockResolvedValue({
    threads: [{
      thread_id: "thread-1", workspace_id: "ws-1", session_name: "会话 1", workspace_path: "/test",
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    }],
    outline: null, detail_outline: null, characters: null, novel: null, worldview: null,
    storyline: null,
  }),
  // 三件套恒空：模拟停前补拉竞态/拉空，outlineReady 永远 false。
  fetchWorkspaceStoryline: vi.fn().mockResolvedValue({ index_markdown: "", entries: [], panorama: [], format: "v2" }),
  fetchWorkspaceCharacters: vi.fn().mockResolvedValue({ characters: [] }),
  fetchWorkspaceWorldview: vi.fn().mockResolvedValue({ markdown: "" }),
  trackCopy: vi.fn(),
  trackRegenerate: vi.fn(),
  createThread: vi.fn().mockResolvedValue({
    thread_id: "thread-1", workspace_id: "ws-1", session_name: "会话 1", workspace_path: "/test",
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  }),
  updateThread: vi.fn().mockResolvedValue({
    thread_id: "thread-1", workspace_id: "ws-1", session_name: "玄幻·热血升级流", workspace_path: "/test",
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  }),
}));

vi.mock("@/components/workspace/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/workspace/TopBar", () => ({ TopBar: () => null }));
vi.mock("@/components/workspace/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/workspace/ConfirmDialog", () => ({ ConfirmDialog: () => null }));
vi.mock("@/components/workspace/TracePanel", () => ({ TracePanel: () => null }));
vi.mock("@/components/workspace/ScriptPanel", () => ({ ScriptPanel: () => null }));
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

describe("回执阶段输入框卡死（修订门控死局）", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { resetStores } = await import("./helpers");
    await resetStores();
  });

  it("首发完成后即使三件套仍为空，composer 也可输入（不再被修订门控锁死）", async () => {
    const user = userEvent.setup();
    renderHome();

    // 大纲未产出 → 首发渲染需求表单
    const genreInput = await screen.findByPlaceholderText("例：玄幻 · 热血升级流", {}, { timeout: 10000 });
    await user.type(genreInput, "玄幻·热血升级流");
    await user.type(screen.getByPlaceholderText(/主角是谁 \+ 核心困境/), "废物少年觉醒万器图录复刻天下兵器");
    await user.type(screen.getByPlaceholderText(/身份起点 \/ 核心欲望/), "铁匠之子，被家族除名，靠金手指打回去");
    await user.click(screen.getByRole("button", { name: "生成大纲" }));

    // 等流结束：final 事件 → assistant 消息 completed（交付/回执态）→ loading=false
    await waitFor(
      () => {
        const completed = document.querySelector('[data-status="completed"]');
        expect(completed).toBeTruthy();
      },
      { timeout: 10000 },
    );

    // 表单已被 composer 取代（会话里有用户消息，表单不再渲染）
    const composer = await screen.findByRole("textbox", {}, { timeout: 10000 });
    // 修复点：三件套全空（outlineReady=false）但首发已发生 → 不得禁用
    expect(composer).toBeEnabled();
    // 发送按钮同样可用
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
  });
});
