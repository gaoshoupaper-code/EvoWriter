import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TraceDetailLite, TraceLogEvent } from "@/lib/types";

vi.mock("react-router-dom", () => ({
  useParams: () => ({ traceId: "trace-metrics" }),
  useNavigate: () => vi.fn(),
  useOutletContext: () => ({ isSuperAdmin: true }),
  useSearchParams: () => [new URLSearchParams()],
}));
vi.mock("@/hooks/useTraceStream", () => ({ useTracePolling: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getTraceEvents: vi.fn(), getTraceContext: vi.fn(), getTraceIntegrity: vi.fn(),
  getTraceModelIO: vi.fn(), stopActiveSession: vi.fn(), resolveTrace: vi.fn(),
  evoStreamPath: (path: string) => `/evolution-api/text${path}`,
}));
vi.mock("@/lib/stream", () => ({ streamRequest: vi.fn() }));
const { timeline, drawer } = vi.hoisted(() => ({ timeline: vi.fn(() => null), drawer: vi.fn(() => null) }));
vi.mock("@/components/trace/TraceChainTimeline", () => ({ TraceChainTimeline: timeline }));
vi.mock("@/components/trace/TraceChainDrawer", () => ({ TraceChainDrawer: drawer }));
vi.mock("@/components/trace/TokenChartPanel", () => ({ TokenChartPanel: () => null }));
vi.mock("@/components/trace/ArtifactsPanel", () => ({ ArtifactsPanel: () => null }));

import { useTracePolling } from "@/hooks/useTraceStream";
import { getTraceIntegrity, getTraceModelIO } from "@/lib/api";
import { streamRequest } from "@/lib/stream";
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

function mockDetail(value: TraceDetailLite | null, isStale = false) {
  vi.mocked(useTracePolling).mockReturnValue({
    detail: value, isLive: !isStale, isStale, lastSyncedAt: null,
    refresh: vi.fn(), refreshing: false, activeSession: null,
    loading: value === null, error: isStale ? "同步失败" : null, availability: value ? "available" : "preparing",
  });
}

function valueFor(label: string) {
  return screen.getByText(label, { selector: "label" }).parentElement!;
}

function mockStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const source = new ReadableStream<Uint8Array>({ start(value) { controller = value; } }).getReader();
  return {
    reader: {
      read: async () => {
        const result = await source.read();
        return { done: result.done, value: result.value };
      },
      cancel: vi.fn(() => source.cancel()),
    },
    push: (chunk: string) => controller.enqueue(new TextEncoder().encode(chunk)),
    close: () => controller.close(),
  };
}

const sse = (event: string, data: unknown) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getTraceIntegrity).mockReset();
  vi.mocked(streamRequest).mockReset().mockImplementation(async () => mockStream().reader);
});

describe("评测运行的模型输入和输出", () => {
  it("优先展示首轮输入和逐条输出，保留可展开的运行详情", async () => {
    const event = (sequence: number, value: Partial<TraceLogEvent> = {}): TraceLogEvent => ({
      trace_id: "trace-metrics", event_id: "event-" + sequence, sequence, type: "llm_end", status: "completed",
      source: "middleware", timestamp: "2026-11-21T00:00:00Z", ...value,
    });
    vi.mocked(getTraceModelIO).mockResolvedValueOnce({
      initial_input: event(2, { type: "llm_start", input: { messages: [
        { type: "system", content: "遵循写作规则" }, { role: "user", content: "写出下一章" },
      ] } }),
      outputs: [event(3, { output: { messages: [{ type: "ai", content: [
        { type: "reasoning", encrypted_content: "encrypted-provider-data", summary: [
          { type: "summary_text", text: "先核对章纲中的因果关系。" },
          { type: "reasoning_text", text: "private-reasoning-text" },
        ] },
        { type: "text", text: "读取材料" },
      ] }] } }),
        event(8, { output: null, tool_calls: [{ name: "read_file" }] })], has_more: true,
    }).mockResolvedValueOnce({ initial_input: null, outputs: [event(12, { output: "最终输出" })], has_more: false });
    mockDetail(detail());
    render(<TraceDetailPage prioritizeModelIO />);

    const input = await screen.findByRole("region", { name: "初始模型输入" });
    expect(await within(input).findByText("写出下一章")).toBeVisible();
    expect(within(input).getByText("系统提示词")).toBeVisible();
    const outputs = within(screen.getByRole("region", { name: "模型输出" }));
    expect(outputs.getByText("思考摘要")).toBeVisible();
    expect(outputs.getByText("先核对章纲中的因果关系。")).toBeVisible();
    expect(outputs.getByText("读取材料")).toBeVisible();
    expect(outputs.queryByText(/encrypted-provider-data|private-reasoning-text/)).not.toBeInTheDocument();
    expect(outputs.getByText("↳ 调用了工具 read_file")).toBeVisible();
    expect(screen.queryByRole("button", { name: "调用链" })).not.toBeInTheDocument();

    fireEvent.click(outputs.getByRole("button", { name: "加载更多输出" }));
    expect(await outputs.findByText("最终输出")).toBeVisible();
    expect(getTraceModelIO).toHaveBeenLastCalledWith("trace-metrics", 8, false);
    expect(outputs.getAllByRole("heading", { level: 3 }).map(item => item.textContent)).toEqual([
      "第 1 次调用", "第 2 次调用", "第 3 次调用",
    ]);

    fireEvent.click(screen.getByText("运行详情"));
    expect(await screen.findByRole("button", { name: "调用链" })).toBeVisible();
  });

  it("用已有改进依据解释历史操作，空摘要不会显示加密内容", async () => {
    mockDetail(detail());
    vi.mocked(getTraceModelIO).mockResolvedValueOnce({ initial_input: null, has_more: false, outputs: [{
      trace_id: "trace-metrics", event_id: "model-old", timestamp: "2026-11-21T00:00:00Z", sequence: 3,
      type: "llm_end", status: "completed", source: "middleware",
      output: { messages: [{ type: "reasoning", summary: [], content: "private-reasoning-text", encrypted_content: "ciphertext" }] },
      tool_calls: [{ name: "write_design_doc" }],
    }] });
    render(<TraceDetailPage prioritizeModelIO modelRationale="关键选择没有体现替代行动。" />);
    const output = within(screen.getByRole("region", { name: "模型输出" }));
    expect(output.getByText("本轮改进依据")).toBeVisible();
    expect(output.getByText("关键选择没有体现替代行动。")).toBeVisible();
    expect(await output.findByText("本次调用没有可读的思考摘要。")).toBeVisible();
    expect(output.queryByText(/ciphertext|private-reasoning-text/)).not.toBeInTheDocument();
  });

  it("正文权限缺失时不加载模型输入和输出", () => {
    mockDetail(detail());
    render(<TraceDetailPage prioritizeModelIO canReadContent={false} />);
    expect(screen.queryByRole("region", { name: "初始模型输入" })).not.toBeInTheDocument();
    expect(getTraceModelIO).not.toHaveBeenCalled();
    expect(streamRequest).not.toHaveBeenCalled();
  });

  it("实时运行的新输出随已保存事件加载，即使运行计数没有变化", async () => {
    vi.mocked(getTraceModelIO)
      .mockResolvedValueOnce({ initial_input: null, outputs: [], has_more: false })
      .mockResolvedValueOnce({ initial_input: null, has_more: false, outputs: [{
        trace_id: "trace-metrics", event_id: "model-3", timestamp: "2026-11-21T00:00:00Z", sequence: 3,
        type: "llm_end", status: "completed", source: "middleware", output: { messages: [
          { type: "ai", content: [{ type: "reasoning", summary: [{ type: "summary_text", text: "模型刚返回的思考摘要" }] }] },
        ] },
      }] });
    const snapshot = detail();
    mockDetail(snapshot);
    const page = render(<TraceDetailPage prioritizeModelIO />);
    expect(await screen.findByText("正在连接实时输出…")).toBeVisible();
    mockDetail({ ...snapshot, nodes: [{
      node_id: "llm:model-2", kind: "llm", label: "模型调用", depth: 0, status: "completed", raw_event_ids: ["model-2", "model-3"],
    }] });
    page.rerender(<TraceDetailPage prioritizeModelIO />);
    expect(await screen.findByText("模型刚返回的思考摘要")).toBeVisible();
  });

  it("实时追加未结束模型调用的思考和回复，归档后替换同一调用", async () => {
    const stream = mockStream();
    vi.mocked(streamRequest).mockResolvedValueOnce(stream.reader);
    vi.mocked(getTraceModelIO).mockResolvedValueOnce({ initial_input: null, has_more: false, outputs: [] })
      .mockResolvedValueOnce({ initial_input: null, has_more: false, outputs: [{
        trace_id: "trace-metrics", event_id: "finished-call", run_id: "call-1", timestamp: "2026-11-21T00:00:00Z", sequence: 3,
        type: "llm_end", status: "completed", source: "middleware", output: "已保存的回复",
      }] });
    const snapshot = detail();
    mockDetail(null);
    const page = render(<TraceDetailPage prioritizeModelIO modelLiveHint />);
    expect(streamRequest).toHaveBeenCalledWith("/evolution-api/text/api/traces/trace-metrics/model-io/stream", { method: "GET" });

    await act(async () => {
      const frame = sse("snapshot", { calls: [{ run_id: "call-1", reasoning: "正在核对", text: "", finished: false }] });
      stream.push(frame.slice(0, -1));
      stream.push(frame.slice(-1));
      stream.push(sse("delta", { run_id: "call-1", channel: "reasoning", text: "章纲" }));
      stream.push(sse("delta", { run_id: "call-1", channel: "text", text: "继续写作" }));
    });
    const live = within(screen.getByRole("region", { name: "实时模型输出" }));
    expect(live.getByText("正在核对章纲")).toBeVisible();
    expect(live.getByText("继续写作")).toBeVisible();
    expect(getTraceModelIO).not.toHaveBeenCalled();

    // AC-004：文字之后再到达的思考增量必须出现在文字之后，而不是跳回顶部的思考框
    await act(async () => { stream.push(sse("delta", { run_id: "call-1", channel: "reasoning", text: "回头核对伏笔。" })); });
    const mid = live.getByText("继续写作");
    const late = live.getByText("回头核对伏笔。");
    expect(mid.compareDocumentPosition(late) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    mockDetail(snapshot);
    await act(async () => page.rerender(<TraceDetailPage prioritizeModelIO modelLiveHint />));
    expect(getTraceModelIO).toHaveBeenCalledTimes(1);
    expect(streamRequest).toHaveBeenCalledTimes(1);

    await act(async () => stream.push(sse("end", { run_id: "call-1" })));
    expect(live.getByText("继续写作")).toBeVisible();
    mockDetail({ ...snapshot, nodes: [{
      node_id: "llm:model-2", kind: "llm", label: "模型调用", depth: 0, status: "completed", raw_event_ids: ["finished-call"],
    }] });
    page.rerender(<TraceDetailPage prioritizeModelIO modelLiveHint />);
    expect(await screen.findByText("已保存的回复")).toBeVisible();
    expect(live.queryByText("继续写作")).not.toBeInTheDocument();
    await act(async () => stream.push(sse("done", {})));
    expect(stream.reader.cancel).toHaveBeenCalledOnce();
    expect(screen.queryByRole("region", { name: "实时模型输出" })).not.toBeInTheDocument();
  });

  it("重连后用快照恢复同一调用，避免重复拼接已接收的片段", async () => {
    vi.useFakeTimers();
    try {
      const first = mockStream();
      const second = mockStream();
      vi.mocked(streamRequest).mockResolvedValueOnce(first.reader).mockResolvedValueOnce(second.reader);
      vi.mocked(getTraceModelIO).mockResolvedValue({ initial_input: null, has_more: false, outputs: [] });
      mockDetail(detail());
      render(<TraceDetailPage prioritizeModelIO />);
      const data = { run_id: "call-1", reasoning: "接续思考", text: "", finished: false };
      await act(async () => {
        first.push(sse("snapshot", { calls: [data] }));
        first.close();
      });
      expect(screen.getByText("实时输出重新连接中…")).toBeVisible();
      await act(async () => vi.advanceTimersByTimeAsync(2_000));
      await act(async () => {
        second.push(sse("snapshot", { calls: [data] }));
        second.push(sse("delta", { run_id: "call-1", channel: "reasoning", text: "中" }));
      });
      expect(screen.getByText("接续思考中")).toBeVisible();
      expect(streamRequest).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

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
