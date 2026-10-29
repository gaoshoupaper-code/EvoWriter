import { act, render, screen } from "@testing-library/react";
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
vi.mock("@/components/trace/TraceChainTimeline", () => ({ TraceChainTimeline: () => null }));
vi.mock("@/components/trace/TraceChainDrawer", () => ({ TraceChainDrawer: () => null }));
vi.mock("@/components/trace/TokenChartPanel", () => ({ TokenChartPanel: () => null }));
vi.mock("@/components/trace/ArtifactsPanel", () => ({ ArtifactsPanel: () => null }));

import { useTracePolling } from "@/hooks/useTraceStream";
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

beforeEach(() => { vi.clearAllMocks(); });

describe("Trace 详情实时统计（REQ-20261029-104420）", () => {
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
