import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TraceListItem, UserUsageRow, WorkspaceUsageRow } from "@/lib/api";

vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("@/lib/api", () => ({ getTraces: vi.fn(), getUserUsage: vi.fn(), getWorkspaceUsage: vi.fn() }));
vi.mock("@/hooks/useActiveRunsStream", () => ({ useActiveRunsStream: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

import { getTraces, getUserUsage, getWorkspaceUsage } from "@/lib/api";
import { useActiveRunsStream } from "@/hooks/useActiveRunsStream";
import MonitorPage from "@/pages/monitor";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const recent = { trace_id: "recent", session_name: "已加载历史", workspace_id: "ws", status: "completed",
  workload: "creation", integrity_status: "verified", coverage: {}, token_total: 12 } as TraceListItem;
const user = { user_id: "a", username: "作家甲", running: false, last_created_at: null, total_tokens: 10 } as UserUsageRow;
const page = (row = recent) => ({ items: [row], total: 1, limit: 50, offset: 0 });
const activeRefresh = vi.fn();

async function settle() {
  await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getTraces).mockResolvedValue(page());
  vi.mocked(getUserUsage).mockResolvedValue([user]);
  vi.mocked(getWorkspaceUsage).mockResolvedValue([]);
  vi.mocked(useActiveRunsStream).mockReturnValue({ activeRuns: [{ ...recent, trace_id: "active", session_name: "已加载活跃",
    status: "running", ingested: false }], transport: "sse", loading: false, error: null, refresh: activeRefresh } as ReturnType<typeof useActiveRunsStream>);
});

describe("运行观测独立加载（REQ-20261108-223920 AC-004）", () => {
  it("历史悬挂时，用量立即请求并和活跃区展示", async () => {
    vi.mocked(getTraces).mockReturnValue(deferred<Awaited<ReturnType<typeof getTraces>>>().promise);
    render(<MonitorPage />);
    await settle();
    expect(getUserUsage).toHaveBeenCalledTimes(1);
    expect(screen.getByText("作家甲")).toBeInTheDocument();
    expect(screen.getByText("已加载活跃 · 创作")).toBeInTheDocument();
    expect(screen.getByText("加载近期运行…")).toBeInTheDocument();
  });

  it("用量或活跃快照悬挂不挡历史", async () => {
    vi.mocked(getUserUsage).mockReturnValue(deferred<UserUsageRow[]>().promise);
    vi.mocked(useActiveRunsStream).mockReturnValue({ activeRuns: [], transport: "connecting", loading: true,
      error: null, refresh: activeRefresh } as ReturnType<typeof useActiveRunsStream>);
    render(<MonitorPage />);
    await settle();
    expect(screen.getByText("已加载历史 · 创作")).toBeInTheDocument();
    expect(screen.getByText("加载用量观测…")).toBeInTheDocument();
    expect(screen.getByText("加载活跃运行…")).toBeInTheDocument();
  });

  it("历史失败只影响历史，重试成功", async () => {
    vi.mocked(getTraces).mockRejectedValueOnce(new Error("历史接口失败"));
    render(<MonitorPage />);
    await settle();
    expect(screen.getByText("作家甲")).toBeInTheDocument();
    expect(screen.getByText("已加载活跃 · 创作")).toBeInTheDocument();
    const section = screen.getByText("近期运行").closest("section")!;
    expect(within(section).getByText("历史接口失败")).toBeInTheDocument();
    fireEvent.click(within(section).getByText("重试"));
    await settle();
    expect(screen.getByText("已加载历史 · 创作")).toBeInTheDocument();
  });

  it("刷新历史保留原数据，失败也不清空", async () => {
    render(<MonitorPage />);
    await settle();
    const pending = deferred<Awaited<ReturnType<typeof getTraces>>>();
    vi.mocked(getTraces).mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByTitle("刷新"));
    expect(screen.getByText("已加载历史 · 创作")).toBeInTheDocument();
    expect(screen.getByText("更新近期运行…")).toBeInTheDocument();
    await act(async () => pending.reject(new Error("刷新历史失败")));
    expect(screen.getByText("已加载历史 · 创作")).toBeInTheDocument();
    expect(screen.getByText("刷新历史失败")).toBeInTheDocument();
  });

  it("旧筛选请求不能覆盖新筛选", async () => {
    const old = deferred<Awaited<ReturnType<typeof getTraces>>>();
    vi.mocked(getTraces).mockReturnValueOnce(old.promise).mockResolvedValueOnce(page({ ...recent, session_name: "新筛选结果" }));
    render(<MonitorPage />);
    fireEvent.change(screen.getByLabelText("工作负载"), { target: { value: "creation" } });
    await settle();
    expect(screen.getByText("新筛选结果 · 创作")).toBeInTheDocument();
    await act(async () => old.resolve(page({ ...recent, session_name: "旧筛选结果" })));
    expect(screen.queryByText("旧筛选结果 · 创作")).not.toBeInTheDocument();
    expect(screen.getByText("新筛选结果 · 创作")).toBeInTheDocument();
  });

  it("点击当前时间范围不会丢弃正在加载的响应", async () => {
    const pending = deferred<Awaited<ReturnType<typeof getTraces>>>();
    vi.mocked(getTraces).mockReturnValueOnce(pending.promise);
    render(<MonitorPage />);
    fireEvent.click(screen.getByRole("button", { name: "7 天" }));
    await act(async () => pending.resolve(page()));
    expect(screen.getByText("已加载历史 · 创作")).toBeInTheDocument();
  });

  it("刷新用量保留已知用户，失败保留旧数据", async () => {
    render(<MonitorPage />);
    await settle();
    const pending = deferred<UserUsageRow[]>();
    vi.mocked(getUserUsage).mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByRole("button", { name: "刷新用量观测" }));
    expect(screen.getByText("作家甲")).toBeInTheDocument();
    expect(screen.getByText("更新用量观测…")).toBeInTheDocument();
    await act(async () => pending.reject(new Error("用量刷新失败")));
    expect(screen.getByText("作家甲")).toBeInTheDocument();
    expect(screen.getByText("用量刷新失败")).toBeInTheDocument();
  });

  it("用量失败可独立重试，活跃错误可独立重试", async () => {
    vi.mocked(getUserUsage).mockRejectedValueOnce(new Error("用量失败"));
    vi.mocked(useActiveRunsStream).mockReturnValue({ activeRuns: [], transport: "polling", loading: false,
      error: "快照失败", refresh: activeRefresh } as ReturnType<typeof useActiveRunsStream>);
    render(<MonitorPage />);
    await settle();
    expect(screen.getByText("已加载历史 · 创作")).toBeInTheDocument();
    const usage = screen.getByText("用量观测").closest("section")!;
    fireEvent.click(within(usage).getByText("重试"));
    await settle();
    expect(screen.getByText("作家甲")).toBeInTheDocument();
    const active = screen.getByText("活跃运行").closest("section")!;
    expect(within(active).getByText("快照失败")).toBeInTheDocument();
    fireEvent.click(within(active).getByText("重试"));
    expect(activeRefresh).toHaveBeenCalledTimes(1);
  });

  it("用量快速切换用户时，旧作品响应不能覆盖新用户", async () => {
    vi.mocked(getUserUsage).mockResolvedValue([user, { ...user, user_id: "b", username: "作家乙" }]);
    const old = deferred<WorkspaceUsageRow[]>();
    vi.mocked(getWorkspaceUsage).mockReturnValueOnce(old.promise).mockResolvedValueOnce([
      { workspace_id: "b-ws", owner_user_id: "b", title: "乙的作品", status: "normal", last_created_at: null, total_tokens: 20 },
    ]);
    render(<MonitorPage />);
    await settle();
    fireEvent.click(screen.getByText("作家甲"));
    fireEvent.click(screen.getByRole("button", { name: "全部用户" }));
    await settle();
    fireEvent.click(screen.getByText("作家乙"));
    await settle();
    expect(screen.getByText("乙的作品")).toBeInTheDocument();
    await act(async () => old.resolve([{ workspace_id: "a-ws", owner_user_id: "a", title: "甲的迟到作品", status: "normal", last_created_at: null, total_tokens: 10 }]));
    expect(screen.queryByText("甲的迟到作品")).not.toBeInTheDocument();
    expect(screen.getByText("乙的作品")).toBeInTheDocument();
  });
});
