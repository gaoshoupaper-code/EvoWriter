import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TraceLogEvent } from "@/lib/types";

vi.mock("@/lib/api", () => ({ getTraceModelIO: vi.fn() }));
vi.mock("@/hooks/useTraceModelStream", () => ({ useTraceModelStream: vi.fn() }));

import { getTraceModelIO } from "@/lib/api";
import { useTraceModelStream } from "@/hooks/useTraceModelStream";
import { TraceModelIO } from "./TraceModelIO";

beforeEach(() => {
  vi.mocked(useTraceModelStream).mockReturnValue({ calls: [], status: "closed" });
});

function event(sequence: number, value: Partial<TraceLogEvent> = {}): TraceLogEvent {
  return { trace_id: "trace-order", event_id: "event-" + sequence, sequence, type: "llm_end",
    status: "completed", source: "middleware", timestamp: "2026-11-26T00:00:00Z", ...value };
}

async function renderOutputs(outputs: TraceLogEvent[]) {
  vi.mocked(getTraceModelIO).mockResolvedValue({ initial_input: null, outputs, has_more: false });
  render(<TraceModelIO traceId="trace-order" eventCount={outputs.length + 1} live={false} archiveReady />);
  return within(await screen.findByRole("region", { name: "模型输出" }));
}

describe("模型输出按真实顺序展示（AC-001）", () => {
  it("单次调用内按 思考摘要 → 文字 → 工具调用行 排列", async () => {
    const message = { type: "ai", content: [
      { type: "reasoning", summary: [{ type: "summary_text", text: "先核对已保存的正文。" }] },
      { type: "text", text: "我先保存第三章。" },
    ], tool_calls: [
      { name: "save_chapter", id: "call-1", type: "tool_call" },
      { name: "write_chapter", id: "call-2", type: "tool_call" },
    ] };
    const outputs = await renderOutputs([event(3, { output: { messages: [message] } })]);
    expect(outputs.getByText("思考摘要")).toBeVisible();
    expect(outputs.getByText("先核对已保存的正文。")).toBeVisible();
    expect(outputs.getByText("我先保存第三章。")).toBeVisible();
    const toolLines = outputs.getAllByText(/^↳ 调用了工具 /);
    expect(toolLines.map(line => line.textContent)).toEqual(["↳ 调用了工具 save_chapter", "↳ 调用了工具 write_chapter"]);
    const text = screen.getByRole("region", { name: "模型输出" }).textContent ?? "";
    const order = ["思考摘要", "先核对已保存的正文。", "我先保存第三章。", "↳ 调用了工具 save_chapter", "↳ 调用了工具 write_chapter"]
      .map(part => text.indexOf(part));
    expect(order.every((position, index) => position >= 0 && (index === 0 || position > order[index - 1]))).toBe(true);
    expect(outputs.queryByText(/^工具调用（/)).not.toBeInTheDocument();
  });

  it("多条消息依序排列，结构化输出保持在末尾", async () => {
    const outputs = await renderOutputs([event(3, { output: { messages: [
      { type: "ai", content: [{ type: "text", text: "第一条消息" }] },
      { type: "ai", content: [{ type: "text", text: "第二条消息" }] },
    ], structured_response: { score: 4.5 } } }) ]);
    const text = screen.getByRole("region", { name: "模型输出" }).textContent ?? "";
    expect(text.indexOf("第一条消息")).toBeGreaterThanOrEqual(0);
    expect(text.indexOf("第一条消息")).toBeLessThan(text.indexOf("第二条消息"));
    expect(text.indexOf("第二条消息")).toBeLessThan(text.indexOf("结构化输出"));
  });
});

describe("实时输出按真实到达顺序渲染（AC-004）", () => {
  it("思考、文字、再思考按交错顺序出现在同一条时间线", async () => {
    vi.mocked(useTraceModelStream).mockReturnValue({ status: "live", calls: [{
      run_id: "call-1", finished: false, segments: [
        { channel: "reasoning", text: "先核对章纲。" },
        { channel: "text", text: "我开始写第一章。" },
        { channel: "reasoning", text: "检查伏笔是否闭合。" },
      ],
    }] });
    vi.mocked(getTraceModelIO).mockResolvedValue({ initial_input: null, outputs: [], has_more: false });
    render(<TraceModelIO traceId="trace-live" eventCount={1} live archiveReady />);
    const outputs = within(await screen.findByRole("region", { name: "实时模型输出" }));
    const first = outputs.getByText("先核对章纲。");
    const middle = outputs.getByText("我开始写第一章。");
    const last = outputs.getByText("检查伏笔是否闭合。");
    expect(first.compareDocumentPosition(middle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(middle.compareDocumentPosition(last) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(outputs.getAllByText("思考")).toHaveLength(2);
    expect(outputs.getByText("回复")).toBeVisible();
  });
});

describe("空态与旧格式兼容（AC-003）", () => {
  it("纯字符串 content 正常渲染，不出现思考占位", async () => {
    const outputs = await renderOutputs([event(4, { output: { messages: [{ type: "ai", content: "纯字符串回复" }] } })]);
    expect(outputs.getByText("纯字符串回复")).toBeVisible();
    expect(outputs.queryByText("思考摘要")).not.toBeInTheDocument();
    expect(outputs.queryByText("本次调用没有可读的思考摘要。")).not.toBeInTheDocument();
  });

  it("块数组缺 reasoning 时不显示思考提示", async () => {
    const message = { type: "ai", content: [{ type: "text", text: "只有正文内容" }] };
    const outputs = await renderOutputs([event(4, { output: { messages: [message] } })]);
    expect(outputs.getByText("只有正文内容")).toBeVisible();
    expect(outputs.queryByText("本次调用没有可读的思考摘要。")).not.toBeInTheDocument();
  });

  it("有 reasoning 块但摘要为空时保留提示语义", async () => {
    const message = { type: "ai", content: [
      { type: "reasoning", summary: [] },
      { type: "text", text: "正文照常显示" },
    ] };
    const outputs = await renderOutputs([event(4, { output: { messages: [message] } })]);
    expect(outputs.getByText("本次调用没有可读的思考摘要。")).toBeVisible();
    expect(outputs.getByText("正文照常显示")).toBeVisible();
  });

  it("消息缺工具调用而事件带顶层 tool_calls 时兜底渲染工具行", async () => {
    const outputs = await renderOutputs([event(5, { output: null, tool_calls: [{ name: "read_file" }] })]);
    expect(outputs.getByText("↳ 调用了工具 read_file")).toBeVisible();
  });

  it("完全空的输出按 empty 文案显示", async () => {
    const outputs = await renderOutputs([event(6, { output: null })]);
    expect(outputs.getByText("本次调用未保存可读取的输出正文。")).toBeVisible();
  });
});
