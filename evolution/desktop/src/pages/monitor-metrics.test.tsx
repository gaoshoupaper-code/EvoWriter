import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ActiveRun, TraceListItem } from "@/lib/types";
import type { FinishedRunInfo } from "@/hooks/useActiveRunsStream";

vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("@/lib/api", () => ({
  getTraces: vi.fn(), getUserUsage: vi.fn(), getWorkspaceUsage: vi.fn(),
}));
vi.mock("@/hooks/useActiveRunsStream", () => ({ useActiveRunsStream: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

import { getTraces, getUserUsage } from "@/lib/api";
import { useActiveRunsStream } from "@/hooks/useActiveRunsStream";
import MonitorPage from "@/pages/monitor";

const metrics = {
  input_tokens: 20000, output_tokens: 5000, total_tokens: 25000,
  input_partial: false, output_partial: false, total_partial: true,
  average_duration_ms: 20000,
};
let onFinished: ((run: FinishedRunInfo | null, traceId: string, lastMetrics?: typeof metrics) => void) | undefined;

function recentRun(): TraceListItem {
  return {
    trace_id: "trace-recent", workspace_id: "ws", thread_id: null,
    session_name: "近期任务", endpoint: null, status: "completed", started_at: null,
    ended_at: null, duration_ms: 10000, event_count: 1, error: null, flag_count: 0,
    owner_user_id: "user", owner_username: null, run_purpose: "user_generation",
    schema_version: 2, service: "executor", workload: "creation",
    integrity_status: "verified", evidence_status: "", evidence_gaps: [], coverage: {},
    skill_activation_count: 0, middleware_intervention_count: 0, hitl_count: 0,
    token_total: 15000,
  };
}

function activeRun(): ActiveRun {
  return {
    trace_id: "trace-active", workspace_id: "ws", thread_id: null, endpoint: null,
    status: "running", started_at: null, duration_ms: null, event_count: 4,
    session_name: "活跃任务", ingested: false, run_purpose: "user_generation",
    service: "executor", workload: "creation", integrity_status: "pending", coverage: {},
    skill_activation_count: 0, middleware_intervention_count: 0, hitl_count: 0,
    metrics, stats_stale: true,
  };
}

async function settle() {
  await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); });
}

beforeEach(() => {
  vi.clearAllMocks();
  onFinished = undefined;
  vi.mocked(getTraces).mockResolvedValue({ items: [recentRun()], total: 1, limit: 50, offset: 0 });
  vi.mocked(getUserUsage).mockResolvedValue([]);
  vi.mocked(useActiveRunsStream).mockImplementation((callback) => {
    onFinished = callback;
    return { activeRuns: [activeRun()], transport: "sse", loading: false, error: null, refresh: vi.fn() };
  });
});

describe("全局 Trace Token（REQ-20261029-104420）", () => {
  it("用户汇总未知用量显示未知，部分已知明确提示不完整", async () => {
    vi.mocked(getUserUsage).mockResolvedValue([
      { user_id: "unknown-user", username: "未知用量用户", running: false, last_created_at: null, total_tokens: null, total_partial: true },
      { user_id: "partial-user", username: "部分用量用户", running: false, last_created_at: null, total_tokens: 12000, total_partial: true },
    ]);
    render(<MonitorPage />);
    await settle();
    const unknown = screen.getByText("未知用量用户").closest("tr")!;
    expect(within(unknown).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(unknown).getByText("部分用量缺失")).toBeInTheDocument();
    const partial = screen.getByText("部分用量用户").closest("tr")!;
    expect(within(partial).getByText("1.2w")).toBeInTheDocument();
    expect(within(partial).getByText("部分用量缺失")).toBeInTheDocument();
  });
  it("活跃及近期条目显示总量、缺失和陈旧提示", async () => {
    render(<MonitorPage />);
    await settle();
    const active = screen.getByText("活跃任务 · 创作").closest("tr")!;
    const recent = screen.getByText("近期任务 · 创作").closest("tr")!;
    expect(within(active).getByText("2.5w")).toBeInTheDocument();
    expect(within(active).getByText("部分用量缺失")).toBeInTheDocument();
    expect(within(active).getByText("数据可能过时")).toBeInTheDocument();
    expect(within(recent).getByText("1.5w")).toBeInTheDocument();
  });

  it("完成占位携带统计，近期校正的未知用量不覆盖已知值", async () => {
    vi.useFakeTimers();
    render(<MonitorPage />);
    await settle();
    const finished: FinishedRunInfo = {
      trace_id: "trace-finished", session_name: "刚结束任务", run_purpose: "user_generation", workload: "creation",
      status: "completed", started_at: null, ended_at: null, duration_ms: 10000,
      event_count: 4, error: null, metrics,
    };
    await act(async () => { onFinished?.(finished, finished.trace_id); });
    let row = screen.getByText("刚结束任务 · 创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();

    vi.mocked(getTraces).mockResolvedValue({
      items: [{ ...recentRun(), trace_id: finished.trace_id, session_name: finished.session_name, token_total: null }],
      total: 1, limit: 50, offset: 0,
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    row = screen.getByText("刚结束任务 · 创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();
  });

  it("业务名作为列表标题，无名终态和迟到校正保留已知名称与用量", async () => {
    vi.useFakeTimers();
    const known: TraceListItem = { ...recentRun(), session_name: "会话 7", status: "running",
      started_at: "2026-11-08T12:34:56Z", display_context: { workspace_title: "雾城", task_phase: "writing" } };
    vi.mocked(getTraces).mockResolvedValue({ items: [known], total: 1, limit: 50, offset: 0 });
    render(<MonitorPage />);
    await settle();
    const title = screen.getByTitle("雾城 · 正文创作");
    expect(title).toHaveTextContent("雾城 · 正文创作");
    expect(title).not.toHaveTextContent(known.trace_id);
    expect(title).not.toHaveTextContent("会话 7");

    const finished: FinishedRunInfo = {
      trace_id: known.trace_id, session_name: null, run_purpose: "user_generation", workload: "creation",
      status: "completed", started_at: null, ended_at: null, duration_ms: 10000,
      event_count: 4, error: null, metrics,
    };
    await act(async () => { onFinished?.(finished, known.trace_id); });
    let row = screen.getByTitle("雾城 · 正文创作").closest("tr")!;
    expect(within(row).getByText("completed")).toBeInTheDocument();
    expect(within(row).getByText("2.5w")).toBeInTheDocument();
    expect(within(row).getByTitle(/34:56/)).toBeInTheDocument();

    vi.mocked(getTraces).mockResolvedValue({
      items: [{ ...recentRun(), trace_id: known.trace_id, session_name: null, token_total: null }],
      total: 1, limit: 50, offset: 0,
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    row = screen.getByTitle("雾城 · 正文创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();
    expect(within(row).getByTitle(/34:56/)).toBeInTheDocument();
    expect(screen.getAllByTitle("雾城 · 正文创作")).toHaveLength(1);
    expect(screen.queryByText(known.trace_id)).not.toBeInTheDocument();
  });

  it("近期同一运行条目优先显示活跃快照统计", async () => {
    vi.mocked(getTraces).mockResolvedValue({
      items: [{ ...recentRun(), trace_id: "trace-active", session_name: "近期同一任务", token_total: 1000 }],
      total: 1, limit: 50, offset: 0,
    });
    render(<MonitorPage />);
    await settle();
    const row = screen.getByText("近期同一任务 · 创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();
    expect(within(row).getByText("数据可能过时")).toBeInTheDocument();
  });

  it("无终态摘要时不伪造完成条目，权威条目到达后保留已知消耗", async () => {
    vi.useFakeTimers();
    render(<MonitorPage />);
    await settle();
    await act(async () => { onFinished?.(null, "trace-fallback", metrics); });
    expect(screen.queryByText("兜底任务 · 创作")).not.toBeInTheDocument();
    vi.mocked(getTraces).mockResolvedValue({
      items: [{ ...recentRun(), trace_id: "trace-fallback", session_name: "兜底任务", token_total: null }],
      total: 1, limit: 50, offset: 0,
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    const row = screen.getByText("兜底任务 · 创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();
  });

  it("null 完成后已有近期行保留活跃用量，旧权威快照不能回退累计", async () => {
    vi.useFakeTimers();
    let runs = [activeRun()];
    vi.mocked(useActiveRunsStream).mockImplementation((callback) => {
      onFinished = callback;
      return { activeRuns: runs, transport: "sse", loading: false, error: null, refresh: vi.fn() };
    });
    const old = { ...recentRun(), trace_id: "trace-active", session_name: "近期旧快照", status: "running" as const };
    vi.mocked(getTraces).mockResolvedValue({ items: [old], total: 1, limit: 50, offset: 0 });
    const page = render(<MonitorPage />);
    await settle();
    expect(within(screen.getByText("近期旧快照 · 创作").closest("tr")!).getByText("2.5w")).toBeInTheDocument();

    runs = [];
    await act(async () => {
      onFinished?.(null, "trace-active", metrics);
      page.rerender(<MonitorPage />);
    });
    let row = screen.getByText("近期旧快照 · 创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();
    expect(within(row).getByText("running")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    row = screen.getByText("近期旧快照 · 创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();

    vi.mocked(getTraces).mockResolvedValue({
      items: [{ ...old, status: "completed", token_total: 30000, metrics: { ...metrics, total_tokens: 30000 } }],
      total: 1, limit: 50, offset: 0,
    });
    await act(async () => { screen.getByTitle("刷新").click(); });
    await settle();
    row = screen.getByText("近期旧快照 · 创作").closest("tr")!;
    expect(within(row).getByText("3.0w")).toBeInTheDocument();
    expect(within(row).getByText("completed")).toBeInTheDocument();
  });

  it("null 兜底后权威完成摘要补齐已有行，不重复计入运行总数", async () => {
    let runs = [{ ...activeRun(), metrics: { ...metrics, total_tokens: 15000 } }];
    vi.mocked(useActiveRunsStream).mockImplementation((callback) => {
      onFinished = callback;
      return { activeRuns: runs, transport: "sse", loading: false, error: null, refresh: vi.fn() };
    });
    vi.mocked(getTraces).mockResolvedValue({
      items: [{ ...recentRun(), trace_id: "trace-active", session_name: "等待完整摘要", status: "running" }],
      total: 1, limit: 50, offset: 0,
    });
    const page = render(<MonitorPage />);
    await settle();
    runs = [];
    await act(async () => {
      onFinished?.(null, "trace-active", { ...metrics, total_tokens: 15000 });
      page.rerender(<MonitorPage />);
    });
    const final: FinishedRunInfo = {
      trace_id: "trace-active", session_name: "完整摘要已到", run_purpose: "user_generation", workload: "creation",
      status: "completed", started_at: null, ended_at: null, duration_ms: 10000,
      event_count: 5, error: null, metrics,
    };
    await act(async () => { onFinished?.(final, final.trace_id); });
    const row = screen.getByText("完整摘要已到 · 创作").closest("tr")!;
    expect(within(row).getByText("2.5w")).toBeInTheDocument();
    expect(within(row).getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("1 条运行")).toBeInTheDocument();
  });

  it("null 兜底后首次完整摘要插入一次，重复完整摘要只更新统计", async () => {
    vi.mocked(getTraces).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    render(<MonitorPage />);
    await settle();
    const final: FinishedRunInfo = {
      trace_id: "trace-new-finish", session_name: "新增完成任务", run_purpose: "user_generation", workload: "creation",
      status: "completed", started_at: null, ended_at: null, duration_ms: 10000,
      event_count: 5, error: null, metrics,
    };
    await act(async () => {
      onFinished?.(null, final.trace_id, { ...metrics, total_tokens: 15000 });
      onFinished?.(final, final.trace_id);
      onFinished?.({ ...final, metrics: { ...metrics, total_tokens: 30000 } }, final.trace_id);
    });
    const row = screen.getByText("新增完成任务 · 创作").closest("tr")!;
    expect(within(row).getByText("3.0w")).toBeInTheDocument();
    expect(screen.getByText("1 条运行")).toBeInTheDocument();
    expect(screen.getAllByText("新增完成任务 · 创作")).toHaveLength(1);
  });
  it("实验和来源不明的完成事件不能插入用户运行", async () => {
    render(<MonitorPage />);
    await settle();
    expect(getTraces).toHaveBeenCalledWith(expect.objectContaining({ observation_scope: "user" }));
    const experiment: FinishedRunInfo = { trace_id: "experiment-finish", session_name: "实验完成",
      run_purpose: "optimization", workload: "creation", status: "completed", started_at: null,
      ended_at: null, duration_ms: 10, event_count: 2, error: null, metrics };
    await act(async () => {
      onFinished?.(experiment, experiment.trace_id);
      onFinished?.({ ...experiment, trace_id: "unknown", session_name: "来源不明", run_purpose: null }, "unknown");
      onFinished?.({ ...experiment, trace_id: "legacy-exp", session_name: "历史用途冲突",
        run_purpose: "user_generation", observation_scope: "experiment" }, "legacy-exp");
    });
    expect(screen.queryByText("实验完成 · 创作")).not.toBeInTheDocument();
    expect(screen.queryByText("来源不明 · 创作")).not.toBeInTheDocument();
    expect(screen.queryByText("历史用途冲突 · 创作")).not.toBeInTheDocument();
    expect(screen.getByText("1 条运行")).toBeInTheDocument();
  });
});
