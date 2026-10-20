/**
 * StageHistoryPanel 单测（REQ-20260118-175526 FR-008/009；REQ-20261020-193647 FR-002/003）。
 * 覆盖：归组列表与倒序、折叠「更早」、只读查看（无输入框+禁续写提示）、
 * 服务器优先链路（污染绕过/断网回退/诚实占位）、未答拍板提问可见、
 * 单条删除二次确认。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const stores = new Map<string, Map<string, unknown>>();
vi.mock("@tauri-apps/plugin-store", () => ({
  load: async (path: string) => {
    if (!stores.has(path)) stores.set(path, new Map());
    const map = stores.get(path)!;
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

const deleteThreadMock = vi.fn().mockResolvedValue({});
const fetchConversationMock = vi.fn().mockResolvedValue([]);
vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  deleteThread: (...args: unknown[]) => deleteThreadMock(...args),
  fetchThreadConversation: (...args: unknown[]) => fetchConversationMock(...args),
}));

vi.mock("@/components/workspace/ConfirmDialog", () => ({
  ConfirmDialog: ({ open, title, confirmLabel, onConfirm, onCancel }: any) =>
    open ? (
      <div role="dialog">
        <span>{title}</span>
        <button type="button" onClick={onConfirm}>{confirmLabel}</button>
        <button type="button" onClick={onCancel}>取消</button>
      </div>
    ) : null,
}));

import { StageHistoryPanel } from "@/components/workspace/StageHistoryPanel";
import { useStageSessionStore, type StageThreadMeta } from "@/stores/stage-session";
import { useExecutionStore } from "@/stores/execution";
import { useWorkspaceStore } from "@/stores/workspace";
import { initSessionPersist, _resetPersistForTests } from "@/lib/session-persist";

function thread(id: string, name = "") {
  return { thread_id: id, workspace_id: "ws-1", session_name: name || `会话 ${id}`, workspace_path: "/t", created_at: "2026-10-01T00:00:00Z", updated_at: "" };
}

function meta(stage: string, boundAt: string, sourceStage?: string): StageThreadMeta {
  return { stage, boundAt, sourceStage };
}

function openHistory(stage: string) {
  useStageSessionStore.setState({ historyView: { stage, threadId: null } });
}

beforeEach(() => {
  stores.clear();
  deleteThreadMock.mockClear();
  fetchConversationMock.mockReset().mockResolvedValue([]);
  _resetPersistForTests();
  initSessionPersist({ getUsername: () => "tester" });
  useStageSessionStore.setState({ threadStageMeta: new Map(), pendingTransition: null, historyView: null });
  useExecutionStore.getState().clearThreadMessages();
  useWorkspaceStore.setState({
    activeWorkspaceId: "ws-1", activeThreadId: "", threads: [], deleting: false,
  });
});

describe("列表态（FR-008/DEC-004）", () => {
  it("同阶段会话时间倒序、自动命名、跳段带申请标记", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1"), thread("t2"), thread("t3")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([
        ["t1", meta("text", "2026-10-18T10:00:00")],
        ["t2", meta("text", "2026-10-18T12:00:00")],
        ["t3", meta("outline", "2026-10-18T11:00:00", "text")],
      ]),
    });
    openHistory("text");

    renderPanel();
    const items = document.querySelectorAll(".stage-history-item-main");
    expect(items[0]).toHaveTextContent("正文 · 10-18 12:00");
    expect(items[1]).toHaveTextContent("正文 · 10-18 10:00");
    // outline 阶段的会话不出现在正文列表
    expect(screen.queryByText(/总纲 · /)).not.toBeInTheDocument();

    // 切到总纲阶段列表（store 订阅驱动，无需重挂）
    openHistory("outline");
    await waitFor(() => {
      expect(document.querySelector(".stage-history-item-main")).toHaveTextContent("总纲 · 10-18 11:00（正文申请）");
    });
  });

  it("超过 15 条折叠「更早」（AC-009）", () => {
    const threads = Array.from({ length: 17 }, (_, i) => thread(`t${i}`));
    const stageMeta = new Map(threads.map((t, i) => [t.thread_id, meta("text", `2026-10-18T1${String(i % 10).padStart(2, "0")}:00:00`)] as const));
    useWorkspaceStore.setState({ threads });
    useStageSessionStore.setState({ threadStageMeta: stageMeta });
    openHistory("text");

    renderPanel();
    expect(screen.getByText("更早（2）")).toBeInTheDocument();
    // 折叠时仅平铺 15 条
    expect(document.querySelectorAll(".stage-history-item").length).toBe(15);
  });
});

describe("查看态（FR-008 只读；FR-002 服务器优先链路 REQ-20261020-193647）", () => {
  it("只读浏览本地消息，无输入框，显示禁续写提示", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    // 本地记录：两条消息
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "user", content: "你好" },
      { role: "assistant", content: "开始吧" },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("开始吧")).toBeInTheDocument();
    });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText(/该对话已归档，不能继续/)).toBeInTheDocument();
    expect(screen.getByText("主题 · 10-18 09:00")).toBeInTheDocument();
  });

  it("服务器优先：本地记录被欢迎语污染时，显示服务端全量（AC-002）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "assistant", content: "我们开始新的故事吧～" }, // 覆盖 bug 受害形态
    ]);
    fetchConversationMock.mockResolvedValue([
      { id: "m1", role: "user", content: "帮我设计世界观", status: "completed" },
      { id: "m2", role: "assistant", content: "好的，先确定基调。", status: "completed" },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("帮我设计世界观")).toBeInTheDocument();
    });
    expect(screen.getByText("好的，先确定基调。")).toBeInTheDocument();
    expect(screen.queryByText("我们开始新的故事吧～")).not.toBeInTheDocument();
  });

  it("服务端不可达 → 回退本地完好记录（AC-003）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "user", content: "离线也看得到" },
      { role: "assistant", content: "本地兜底正文" },
    ]);
    fetchConversationMock.mockRejectedValue(new Error("offline"));

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("本地兜底正文")).toBeInTheDocument();
    });
    expect(screen.queryByText(/正在载入对话记录/)).not.toBeInTheDocument();
  });

  it("内存缓存被欢迎语污染、服务端不可达 → 越过缓存读本地文件（FR-002 链路边界）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "user", content: "磁盘上的真实记录" },
    ]);
    useExecutionStore.getState().threadMessages.set("t1", [
      { role: "assistant", content: "我们开始新的故事吧～" }, // 切换时被污染的缓存
    ]);
    fetchConversationMock.mockRejectedValue(new Error("offline"));

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("磁盘上的真实记录")).toBeInTheDocument();
    });
  });

  it("两边都无实质记录 → 诚实占位（AC-004，本地缺失/只剩欢迎语两形态）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t-gone"), thread("t-welcome")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([
        ["t-gone", meta("theme", "2026-10-18T09:00:00")],
        ["t-welcome", meta("theme", "2026-10-18T10:00:00")],
      ]),
      historyView: { stage: "theme", threadId: "t-gone" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t-welcome", [
      { role: "assistant", content: "我们开始新的故事吧～" },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/记录无法恢复/)).toBeInTheDocument();
    });

    useStageSessionStore.setState({ historyView: { stage: "theme", threadId: "t-welcome" } });
    await waitFor(() => {
      expect(screen.getByText("主题 · 10-18 10:00")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/记录无法恢复/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/本地记录缺失/)).not.toBeInTheDocument();
  });

  it("未答拍板提问可见：服务端 awaiting 腿正文即提问（AC-005 服务端路径）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    fetchConversationMock.mockResolvedValue([
      { id: "m1", role: "user", content: "走访谈流程", status: "completed" },
      { id: "m2", role: "assistant", content: "故事发生在什么时代？", status: "awaiting" },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("故事发生在什么时代？")).toBeInTheDocument();
    });
  });

  it("未答拍板提问可见：本地记录带 awaitingInput，占位正文不显示（AC-005 本地路径）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "user", content: "走访谈流程" },
      {
        role: "assistant",
        content: "正在生成...",
        // 恢复管线按 2h 阈值剥 awaitingInput（REQ-20260101-170627 FR-005）：
        // 本地路径提问可见的窗口 = 卡点发生后 2h 内；更早的提问走服务端腿
        awaitingInput: { kind: "choice", question: "主角的职业是什么？", options: [], askedAt: new Date().toISOString() },
      },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("主角的职业是什么？")).toBeInTheDocument();
    });
    expect(screen.queryByText("正在生成...")).not.toBeInTheDocument();
  });

  it("提问与正文同文（旧管线把提问写进 content）→ 只显示提问块不重复", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "user", content: "走访谈流程" },
      {
        role: "assistant",
        content: "主角的职业是什么？",
        awaitingInput: { kind: "choice", question: "主角的职业是什么？", options: [], askedAt: new Date().toISOString() },
      },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.queryAllByText(/主角的职业是什么/)).toHaveLength(1);
    });
  });

  it("双源均有实质内容 → 服务器优先（DEC-001 顺序钉死）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "user", content: "本地独有内容" },
      { role: "assistant", content: "本地版本正文" },
    ]);
    fetchConversationMock.mockResolvedValue([
      { id: "m1", role: "user", content: "服务端版本的消息", status: "completed" },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("服务端版本的消息")).toBeInTheDocument();
    });
    expect(screen.queryByText("本地独有内容")).not.toBeInTheDocument();
    expect(screen.queryByText("本地版本正文")).not.toBeInTheDocument();
  });

  it("服务端挂起 → 查看态预算内回退本地（AC-003 超时形态）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    const { persistThreadMessages } = await import("@/lib/session-persist");
    await persistThreadMessages("ws-1", "t1", [
      { role: "user", content: "离线也看得到" },
      { role: "assistant", content: "本地兜底正文" },
    ]);
    fetchConversationMock.mockReturnValue(new Promise(() => {})); // 永不 resolve

    vi.useFakeTimers();
    renderPanel();
    expect(screen.getByText(/正在载入对话记录/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    vi.useRealTimers();
    await waitFor(() => {
      expect(screen.getByText("本地兜底正文")).toBeInTheDocument();
    });
  });

  it("readOnly 中断腿：带内容 → 「内容保存至此」且不出现「可以重试」", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    fetchConversationMock.mockResolvedValue([
      { id: "m1", role: "user", content: "继续写", status: "completed" },
      { id: "m2", role: "assistant", content: "写到一半的正文", status: "interrupted" },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/内容保存至此/)).toBeInTheDocument();
    });
    expect(screen.getByText(/写到一半的正文/)).toBeInTheDocument();
    expect(screen.queryByText(/可以重试/)).not.toBeInTheDocument();
  });

  it("readOnly 中断腿：空内容 → 「未收到完成信号」形态", async () => {
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t1" },
    });
    fetchConversationMock.mockResolvedValue([
      { id: "m1", role: "assistant", content: "", status: "interrupted" },
    ]);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/未收到完成信号/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/可以重试/)).not.toBeInTheDocument();
  });
});

describe("删除（FR-009/AC-010）", () => {
  it("二次确认后删除；取消不动", async () => {
    const user = userEvent.setup();
    useWorkspaceStore.setState({ threads: [thread("t1")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t1", meta("theme", "2026-10-18T09:00:00")]]),
    });
    openHistory("theme");

    renderPanel();
    await user.click(screen.getByRole("button", { name: /删除 主题/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // 取消：不删
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteThreadMock).not.toHaveBeenCalled();

    // 确认：删除
    await user.click(screen.getByRole("button", { name: /删除 主题/ }));
    await user.click(screen.getByRole("button", { name: "删除对话" }));
    await waitFor(() => expect(deleteThreadMock).toHaveBeenCalledWith("t1"));
  });
});

// 轻量渲染：直接挂载面板（它自身从 store 读数据；RTL 自动 cleanup）
function renderPanel() {
  return render(<StageHistoryPanel onDeleteThread={(id) => deleteThreadMock(id)} />);
}
