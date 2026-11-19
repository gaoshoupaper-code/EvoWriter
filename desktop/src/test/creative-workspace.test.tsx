import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { CreativeWorkspace } from "@/components/workspace/CreativeWorkspace";
import { apiJson } from "@/lib/api";
import { streamRequest } from "@/lib/stream";
import { readCreativeStream, updateReply } from "@/lib/creative-agents";
import type { ChatMessage } from "@/lib/types";

vi.mock("@/lib/api", async (load) => ({
  ...await load<typeof import("@/lib/api")>(),
  apiJson: vi.fn(),
}));
vi.mock("@/lib/stream", () => ({ streamRequest: vi.fn() }));
vi.mock("@/lib/usePanelPolling", () => ({ refreshWorkspaceContent: vi.fn() }));
vi.mock("@/stores/billing", () => ({ useBillingStore: { getState: () => ({ submissionBudget: async () => 4 }) } }));
vi.mock("@/components/workspace/ChatPanel", () => ({
  ChatPanel: ({ messages, onResumeSubmit }: { messages: { content: string }[]; onResumeSubmit: (v: string) => Promise<void> }) =>
    <><div aria-label="会话内容">{messages.map((m) => m.content).join(" ")}</div>
      <button onClick={() => void onResumeSubmit("采用方案甲")}>提交作者指令</button></>,
}));

const request = vi.mocked(apiJson);
const threads = (ids: string[]) => ids.map((id) => ({ thread_id: id, session_name: id }));
let volumeThreads: string[];

beforeEach(() => {
  volumeThreads = ["卷纲原会话"];
  request.mockReset();
  vi.mocked(streamRequest).mockReset();
  request.mockImplementation(async (path, init) => {
    const url = new URL(path, "https://writer.test");
    const agent = url.pathname.split("/")[5];
    if (url.pathname.endsWith("/entry")) {
      const ids = agent === "volume" ? volumeThreads : [agent + "-1"];
      return { agent, threads: threads(ids), last_thread_id: ids.at(-1), task: null,
        conflicts: [], missing: agent === "text" ? ["本章章纲"] : [] };
    }
    if (url.pathname.endsWith("/threads") && init?.method === "POST") {
      volumeThreads.push("新卷纲会话");
      return threads(volumeThreads).at(-1);
    }
    if (url.pathname.endsWith("/conversation")) {
      const id = decodeURIComponent(url.pathname.split("/").at(-2)!);
      return [{ id, role: "user", status: "completed", content: id, ts: "" }];
    }
    throw new Error("unexpected request: " + path);
  });
});

it("keeps separate conversations and supports resume and new threads without starting generation", async () => {
  const user = userEvent.setup();
  render(<CreativeWorkspace workspaceId="book-1" />);
  await screen.findByText("outline-1", { selector: '[aria-label="会话内容"]' });
  await user.click(screen.getByRole("button", { name: "卷纲" }));
  await screen.findByText("卷纲原会话", { selector: '[aria-label="会话内容"]' });
  await user.click(screen.getByRole("button", { name: "正文" }));
  await screen.findByText(/缺少.*本章章纲/);
  expect(screen.queryByLabelText("会话内容")).toBeNull();
  await user.click(screen.getByRole("button", { name: "卷纲" }));
  await screen.findByText("卷纲原会话", { selector: '[aria-label="会话内容"]' });
  await user.click(screen.getByRole("button", { name: "新会话" }));
  await screen.findByText("新卷纲会话", { selector: '[aria-label="会话内容"]' });
  await user.selectOptions(screen.getByLabelText("当前会话"), "卷纲原会话");
  await waitFor(() => expect(screen.getByLabelText("会话内容")).toHaveTextContent("卷纲原会话"));
  expect(streamRequest).not.toHaveBeenCalled();
});

it("sends the author's instruction to the selected agent and current thread only", async () => {
  const user = userEvent.setup();
  vi.mocked(streamRequest).mockResolvedValue({
    read: async () => ({ done: true, value: undefined }), cancel: async () => {},
  });
  render(<CreativeWorkspace workspaceId="book-1" />);
  await screen.findByLabelText("会话内容");
  await user.click(screen.getByRole("button", { name: "卷纲" }));
  await screen.findByText("卷纲原会话", { selector: '[aria-label="会话内容"]' });
  await user.click(screen.getByRole("button", { name: "提交作者指令" }));
  await waitFor(() => expect(streamRequest).toHaveBeenCalledTimes(1));
  expect(vi.mocked(streamRequest).mock.calls[0]).toEqual([
    "/api/workspaces/book-1/agents/volume/threads/" + encodeURIComponent("卷纲原会话") + "/generate/stream",
    { method: "POST", body: expect.objectContaining({ thread_id: "卷纲原会话", prompt: "采用方案甲", billing_budget: 4 }) },
  ]);
});

it("retains an interruption card across split stream frames", async () => {
  const bytes = new TextEncoder().encode('event: interrupt\r\ndata: {"question":"选择哪一项？","kind":"choice"}\r\n\r\n');
  let offset = 0;
  const cancel = vi.fn(async () => {});
  let reply: ChatMessage = { role: "assistant", content: "" };
  await readCreativeStream({
    read: async () => offset === bytes.length ? { done: true, value: undefined }
      : { done: false, value: bytes.slice(offset, ++offset) }, cancel,
  }, (kind, data) => { reply = updateReply(reply, kind, data); });
  expect(reply.awaitingInput?.question).toBe("选择哪一项？");
  expect(cancel).toHaveBeenCalledOnce();
});

it("reopens the saved target and waits for the author to continue an interrupted task", async () => {
  const user = userEvent.setup();
  const requestNormally = request.getMockImplementation()!;
  let chosen = false;
  request.mockImplementation(async (path, init) => {
    if (path.includes("/agents/text/entry")) return {
      agent: "text", missing: [], threads: threads(["正文甲"]), last_thread_id: "正文甲",
      task: { task_id: "task-8", thread_id: "正文甲", status: "interrupted", volume: 2, chapter: 8,
        progress: ["正文/第008章.md"], harness_commit: "original" },
      conflicts: chosen ? [] : [{ id: 7, name: "主角", field: "阵营", current: "旧盟", proposed: "新盟" }],
    };
    if (path.endsWith("/conflicts/7/resolve")) { chosen = true; return { conflicts: [] }; }
    return requestNormally(path, init);
  });
  vi.mocked(streamRequest).mockResolvedValue({
    read: async () => ({ done: true, value: undefined }), cancel: async () => {},
  });
  render(<CreativeWorkspace workspaceId="book-1" />);
  await user.click(screen.getByRole("button", { name: "正文" }));
  await screen.findByText(/已保存.*第008章/);
  expect(screen.getByLabelText("章号")).toHaveValue(8);
  expect(streamRequest).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /采用本次值.*新盟/ }));
  await waitFor(() => expect(screen.queryByLabelText("人物卡冲突")).toBeNull());
  expect(request).toHaveBeenCalledWith("/api/workspaces/book-1/agents/text/conflicts/7/resolve",
    expect.objectContaining({ body: JSON.stringify({ choice: "proposed" }) }));
  await user.click(screen.getByRole("button", { name: "继续原任务" }));
  await waitFor(() => expect(streamRequest).toHaveBeenCalledWith(
    "/api/workspaces/book-1/agents/text/tasks/task-8/continue", { method: "POST", body: {} }));
});
