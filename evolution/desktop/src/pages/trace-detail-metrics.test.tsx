import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TraceDetailLite } from "@/lib/types";

vi.mock("react-router-dom", () => ({
  useParams: () => ({ traceId: "trace-metrics" }),
  useNavigate: () => vi.fn(),
  useOutletContext: () => ({ isSuperAdmin: true }),
  useSearchParams: () => [new URLSearchParams()],
}));
vi.mock("@/hooks/useTraceStream", () => ({ useTracePolling: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getTraceEvents: vi.fn(), getTraceContext: vi.fn(), getTraceIntegrity: vi.fn(),
  stopActiveSession: vi.fn(), resolveTrace: vi.fn(),
}));
const { timeline, drawer } = vi.hoisted(() => ({ timeline: vi.fn(() => null), drawer: vi.fn(() => null) }));
vi.mock("@/components/trace/TraceChainTimeline", () => ({ TraceChainTimeline: timeline }));
vi.mock("@/components/trace/TraceChainDrawer", () => ({ TraceChainDrawer: drawer }));
vi.mock("@/components/trace/TokenChartPanel", () => ({ TokenChartPanel: () => null }));
vi.mock("@/components/trace/ArtifactsPanel", () => ({ ArtifactsPanel: () => null }));

import { useTracePolling } from "@/hooks/useTraceStream";
import { getTraceIntegrity } from "@/lib/api";
import TraceDetailPage from "@/pages/trace-detail";

function detail(input = 12000, output = 3000, average = 10000): TraceDetailLite {
  return {
    run: {
      trace_id: "trace-metrics", workspace_id: "ws", thread_id: "thread",
      session_name: "运行中统计", workspace_path: "/workspace", endpoint: "test",
      status: "running", started_at: "2026-10-29T00:00:00Z", duration_ms: null,
      event_count: 4, path: "", integrity_status: "pending",
    },
    nodes: [], todos: [],
    metrics: {
      input_tokens: input, output_tokens: output, total_tokens: input + output,
      input_partial: false, output_partial: false, total_partial: false,
      average_duration_ms: average,
    },
  };
}

function mockDetail(value: TraceDetailLite, isStale = false) {
  vi.mocked(useTracePolling).mockReturnValue({
    detail: value, isLive: !isStale, isStale, lastSyncedAt: null,
    refresh: vi.fn(), refreshing: false, activeSession: null,
    loading: false, error: isStale ? "同步失败" : null, availability: "available",
  });
}

function valueFor(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!;
}

beforeEach(() => { vi.clearAllMocks(); vi.mocked(getTraceIntegrity).mockReset(); });

describe("Trace 详情实时统计（REQ-20261029-104420）", () => {
  it.each(["incomplete", "conflict", "legacy"] as const)("运行中保留 %s 标记和采集异常，不提示重跑", (integrity) => {
    const snapshot = detail();
    snapshot.run.integrity_status = integrity;
    snapshot.run.error = "已记录的采集异常";
    mockDetail(snapshot);
    render(<TraceDetailPage />);
    const hint = document.querySelector('.integrity-' + integrity)!;
    expect(hint).toHaveTextContent("Trace 尚未结束");
    expect(hint).toHaveTextContent({ incomplete: "不完整", conflict: "冲突", legacy: "旧版" }[integrity]);
    expect(hint).not.toHaveTextContent("重新运行");
    expect(screen.getByText("已记录的采集异常")).toBeInTheDocument();
    expect(getTraceIntegrity).not.toHaveBeenCalled();
  });

  it.each(["awaiting_input", "pending", "cancelling"] as const)("%s 尚未结束时也不提示重跑当前任务", (status) => {
    const snapshot = detail();
    snapshot.run.status = status;
    snapshot.run.integrity_status = "incomplete";
    mockDetail(snapshot);
    render(<TraceDetailPage />);
    const hint = document.querySelector('.integrity-incomplete')!;
    expect(hint).toHaveTextContent("Trace 尚未结束");
    expect(hint).not.toHaveTextContent("重新运行");
  });

  it("运行结束后仍展示真实缺口和原有重跑恢复建议", async () => {
    const snapshot = detail();
    snapshot.run.integrity_status = "incomplete";
    mockDetail(snapshot);
    const page = render(<TraceDetailPage />);
    vi.mocked(getTraceIntegrity).mockResolvedValue({ trace_id: "trace-metrics", integrity_status: "incomplete", evidence_status: "unknown", evidence_gaps: [], recoverable: true,
      missing_checks: [{ check: "manifest_missing", stage: "终态封存", impact: "缺少最终清单", recovery: "重新运行该任务" }], affected_downstreams: [] });
    mockDetail({ ...snapshot, run: { ...snapshot.run, status: "completed" } });
    page.rerender(<TraceDetailPage />);
    expect(await screen.findByText("恢复：重新运行该任务")).toBeInTheDocument();
    expect(screen.getByText("缺少最终清单")).toBeInTheDocument();
    expect(document.querySelector('.integrity-incomplete')).not.toHaveTextContent("Trace 尚未结束");
  });
  it("Trace 未结束时展示输入输出，并按新快照更新平均耗时", async () => {
    mockDetail(detail());
    const page = render(<TraceDetailPage />);
    expect(valueFor("输入 Token")).toHaveTextContent("1.2w");
    expect(valueFor("输出 Token")).toHaveTextContent("0.3w");
    expect(valueFor("平均耗时")).toHaveTextContent("10s");

    mockDetail(detail(20000, 5000, 20000));
    await act(async () => { page.rerender(<TraceDetailPage />); });
    expect(valueFor("输入 Token")).toHaveTextContent("2.0w");
    expect(valueFor("输出 Token")).toHaveTextContent("0.5w");
    expect(valueFor("平均耗时")).toHaveTextContent("20s");
  });

  it("分项独立显示未知、零值和部分缺失，陈旧快照仍保留统计", () => {
    const snapshot = detail();
    snapshot.metrics = {
      input_tokens: 8000, output_tokens: 0, total_tokens: 8000,
      input_partial: true, output_partial: false, total_partial: true,
      average_duration_ms: null,
    };
    mockDetail(snapshot, true);
    const page = render(<TraceDetailPage />);
    expect(valueFor("输入 Token")).toHaveTextContent("0.8w");
    expect(valueFor("输入 Token")).toHaveTextContent("部分用量缺失");
    expect(valueFor("输出 Token")).toHaveTextContent("0w");
    expect(valueFor("输出 Token")).not.toHaveTextContent("部分用量缺失");
    expect(valueFor("平均耗时")).toHaveTextContent("—");
    expect(screen.getByText("陈旧")).toBeInTheDocument();

    mockDetail({ ...snapshot, metrics: undefined });
    page.rerender(<TraceDetailPage />);
    expect(valueFor("输入 Token")).toHaveTextContent("—");
    expect(valueFor("输出 Token")).toHaveTextContent("—");
  });
});

describe("Trace 失败重点（REQ-20261121-161413）", () => {
  it("在头部展示失败原因，优先定位具体步骤并打开已有权限的详情", () => {
    const snapshot = detail();
    snapshot.run.status = "failed";
    snapshot.run.error = "本次运行停止";
    snapshot.nodes = [
      { node_id: "run-error", kind: "error", status: "failed", label: "Run 失败", depth: 0, raw_event_ids: [] },
      { node_id: "failed-step", kind: "error", status: "failed", label: "检查卷纲", tool_name: "检查卷纲", depth: 1, raw_event_ids: [] },
    ];
    mockDetail(snapshot);
    render(<TraceDetailPage />);

    const failure = screen.getByRole("region", { name: "失败详情" });
    expect(within(failure).getByText("本次运行停止")).toBeInTheDocument();
    expect(within(failure).getByText("检查卷纲")).toBeInTheDocument();
    expect(screen.getByText("运行指标").closest("details")).not.toHaveAttribute("open");

    fireEvent.click(screen.getByRole("button", { name: "Token 图表" }));
    fireEvent.click(within(failure).getByRole("button", { name: "定位出错记录" }));
    expect(timeline).toHaveBeenLastCalledWith(expect.objectContaining({ highlightedNodeId: "failed-step" }), undefined);
    expect(drawer).toHaveBeenLastCalledWith(expect.objectContaining({ node: snapshot.nodes[1] }), undefined);
  });

  it("失败的历史记录没有步骤和原因时保留失败状态，且无定位按钮", () => {
    const snapshot = detail();
    snapshot.run.status = "failed";
    snapshot.nodes = [{ node_id: "run", kind: "run", status: "failed", label: "运行", depth: 0, raw_event_ids: [] }];
    mockDetail(snapshot);
    render(<TraceDetailPage />);

    const failure = screen.getByRole("region", { name: "失败详情" });
    expect(screen.getByText("失败", { exact: true })).toBeInTheDocument();
    expect(within(failure).getByText("未记录出错步骤和失败原因。")).toBeInTheDocument();
    expect(within(failure).queryByRole("button")).not.toBeInTheDocument();
  });

  it("取消超时定位超时节点，跳过之前的取消请求和工具失败", () => {
    const snapshot = detail();
    snapshot.run.status = "cancel_timeout";
    snapshot.nodes = [
      { node_id: "run", kind: "run", status: "cancel_timeout", label: "运行", depth: 0, raw_event_ids: [] },
      { node_id: "request", kind: "error", status: "cancelling", label: "用户请求取消", depth: 0, raw_event_ids: [] },
      { node_id: "tool-error", kind: "error", status: "failed", label: "写入失败", tool_name: "写入文件", depth: 1, raw_event_ids: [] },
      { node_id: "timeout", kind: "error", status: "cancel_timeout", label: "取消超时（未收敛）", depth: 0,
        raw_event_ids: [], error: "取消请求尚未收敛" },
    ];
    mockDetail(snapshot);
    render(<TraceDetailPage />);

    const failure = screen.getByRole("region", { name: "失败详情" });
    expect(within(failure).getByText("取消超时（未收敛）")).toBeInTheDocument();
    expect(within(failure).getByText("取消请求尚未收敛")).toBeInTheDocument();
    fireEvent.click(within(failure).getByRole("button", { name: "定位出错记录" }));
    expect(timeline).toHaveBeenLastCalledWith(expect.objectContaining({ highlightedNodeId: "timeout" }), undefined);
  });

  it("取消超时记录缺失时，正常取消控制节点不能当成出错记录", () => {
    const snapshot = detail();
    snapshot.run.status = "cancel_timeout";
    snapshot.nodes = [
      { node_id: "request", kind: "error", status: "cancelling", label: "用户请求取消", depth: 0, raw_event_ids: [] },
      { node_id: "cancelled", kind: "error", status: "cancelled", label: "已取消", depth: 0, raw_event_ids: [] },
    ];
    mockDetail(snapshot);
    render(<TraceDetailPage />);

    const failure = screen.getByRole("region", { name: "失败详情" });
    expect(within(failure).getByText("未记录出错步骤和失败原因。")).toBeInTheDocument();
    expect(within(failure).queryByRole("button")).not.toBeInTheDocument();
  });

  it("可定位受限工具的结构节点，且保留原有详情权限", () => {
    const snapshot = detail();
    snapshot.run.status = "failed";
    snapshot.nodes = [{ node_id: "tool", kind: "tool", status: "failed", label: "写入文件", depth: 0,
      raw_event_ids: [], error: "受限工具详情" }];
    mockDetail(snapshot);
    render(<TraceDetailPage canReadContent={false} />);

    const failure = screen.getByRole("region", { name: "失败详情" });
    expect(within(failure).getByText("写入文件")).toBeInTheDocument();
    expect(screen.queryByText("受限工具详情")).not.toBeInTheDocument();
    fireEvent.click(within(failure).getByRole("button", { name: "定位出错记录" }));
    expect(timeline).toHaveBeenLastCalledWith(expect.objectContaining({ highlightedNodeId: "tool" }), undefined);
    expect(drawer).toHaveBeenLastCalledWith(expect.objectContaining({ node: null }), undefined);
  });
});
