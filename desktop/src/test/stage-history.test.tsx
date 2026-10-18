/**
 * StageHistoryPanel 单测（REQ-20261018-175526 FR-008/009）。
 * 覆盖：归组列表与倒序、折叠「更早」、只读查看（无输入框+禁续写提示）、
 * 单条删除二次确认、记录缺失占位。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
vi.mock("@/lib/api", () => ({
  API_BASE_URL: "",
  deleteThread: (...args: unknown[]) => deleteThreadMock(...args),
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
  _resetPersistForTests();
  initSessionPersist({ getUsername: () => "tester" });
  useStageSessionStore.setState({ threadStageMeta: new Map(), pendingTransition: null, historyView: null });
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

describe("查看态（FR-008 只读）", () => {
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

  it("记录缺失显示占位不消失（FR-008 失败语义）", async () => {
    useWorkspaceStore.setState({ threads: [thread("t-gone")] });
    useStageSessionStore.setState({
      threadStageMeta: new Map([["t-gone", meta("theme", "2026-10-18T09:00:00")]]),
      historyView: { stage: "theme", threadId: "t-gone" },
    });

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/本地记录缺失/)).toBeInTheDocument();
    });
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
