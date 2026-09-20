/**
 * v9 表单直入验收测试（FR-002 / AC-107 本地证据，REQ-20260920-150149）。
 *
 * 覆盖：
 *   1. 大纲未产出时，ChatPanel 的 composer 区渲染需求表单（非聊天输入框）
 *   2. 三项必填缺一不可提交；填齐后提交
 *   3. streamRequest 请求体带 demand_md（模板化渲染）+ kickoff prompt
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
    // 大纲未产出：storyline 为空 → 表单应出现
    storyline: null,
  }),
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

describe("v9 表单直入（FR-002）", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { resetStores } = await import("./helpers");
    await resetStores();
  });

  it("大纲未产出时渲染需求表单，提交后请求体带 demand_md 与 kickoff", async () => {
    const user = userEvent.setup();
    renderHome();

    // 表单出现（大纲未产出）
    const genreInput = await screen.findByPlaceholderText("例：玄幻 · 热血升级流", {}, { timeout: 10000 });

    // 未填必填时提交按钮禁用
    const submitBtn = screen.getByRole("button", { name: "生成大纲" });
    expect(submitBtn).toBeDisabled();

    // 填三项必填
    await user.type(genreInput, "玄幻·热血升级流");
    await user.type(screen.getByPlaceholderText(/主角是谁 \+ 核心困境/), "废物少年觉醒万器图录复刻天下兵器");
    await user.type(screen.getByPlaceholderText(/身份起点 \/ 核心欲望/), "铁匠之子，被家族除名，靠金手指打回去");
    await waitFor(() => expect(submitBtn).toBeEnabled());

    await user.click(submitBtn);

    await waitFor(
      () => expect(streamRequest).toHaveBeenCalled(),
      { timeout: 10000 },
    );
    const call = streamRequest.mock.calls[0];
    const body = call[1].body as Record<string, string>;
    expect(body.prompt).toContain("demand.md");
    expect(body.demand_md).toContain("玄幻·热血升级流");
    expect(body.demand_md).toContain("status: confirmed");
    expect(body.demand_md).toContain("expected_subagents");
  });
});
