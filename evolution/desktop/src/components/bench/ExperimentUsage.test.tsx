import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getExperimentEvaluationUsage } from "@/lib/api";
import { ExperimentUsage } from "./ExperimentUsage";

vi.mock("@/lib/api", () => ({ getExperimentEvaluationUsage: vi.fn() }));
const fetchPage = vi.mocked(getExperimentEvaluationUsage);
const group = { calls: 52, failed_calls: 2, input_tokens: 100, output_tokens: null, total_tokens: null, known: false, partial: true };
const usage = { generation: group, evaluation: group, evolution: group, known: false };
const item = (n: number) => ({ sample_id: `样本-${n}`, judge_id: 7, dimension: "需求兑现", status: "failed", usage: null });
const firstPage = { items: Array.from({ length: 50 }, (_, n) => item(n)), total: 52, offset: 0, limit: 50 };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((finish) => { resolve = finish; });
  return { promise, resolve };
}
function expand() { fireEvent.click(screen.getByText("调用用量，未知值单独标记")); }

beforeEach(() => { fetchPage.mockReset(); });
afterEach(() => { vi.useRealTimers(); });

describe("实验用量按需分页 / AC-002", () => {
  it("默认折叠不取明细，也不创建隐藏明细行", () => {
    render(<ExperimentUsage experimentId="id" usage={usage} />);
    expect(fetchPage).not.toHaveBeenCalled();
    expect(screen.queryByRole("table", { hidden: true })).not.toBeInTheDocument();
  });

  it("展开只取当前页，翻页替换明细，失败和未知用量仍显示", async () => {
    fetchPage.mockResolvedValueOnce(firstPage).mockResolvedValueOnce({ items: [item(50), item(51)], total: 52, offset: 50, limit: 50 });
    render(<ExperimentUsage experimentId="id" usage={usage} />);
    expand();
    expect(await screen.findByText("样本-0")).toBeInTheDocument();
    expect(fetchPage).toHaveBeenCalledWith("id", { offset: 0, limit: 50 });
    expect(screen.getAllByText("部分已知")).toHaveLength(3);
    const table = screen.getByRole("table", { name: "评分调用明细" });
    expect(within(table).getAllByRole("row")).toHaveLength(51);
    expect(within(table).getAllByText("失败")).toHaveLength(50);
    expect(within(table).getAllByText("未知")).toHaveLength(150);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByText("样本-50")).toBeInTheDocument();
    expect(fetchPage).toHaveBeenLastCalledWith("id", { offset: 50, limit: 50 });
    expect(screen.queryByText("样本-0")).not.toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "评分调用明细" })).getAllByRole("row")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });

  it("明细失败只显示局部错误，支持重试和空记录", async () => {
    fetchPage.mockRejectedValueOnce(new Error("暂无读取权限")).mockResolvedValueOnce({ items: [], total: 0, offset: 0, limit: 50 });
    render(<ExperimentUsage experimentId="id" usage={usage} />);
    expand();
    expect(await screen.findByRole("alert")).toHaveTextContent("暂无读取权限");
    expect(screen.getByText("生成用量")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试评分明细" }));
    expect(await screen.findByText("暂无评分调用记录")).toBeInTheDocument();
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });

  it("区分生成记录和评分调用，兼容原始用量键且保留零与未知", async () => {
    fetchPage.mockResolvedValueOnce({ items: [
      { ...item(0), usage: { prompt_tokens: 17, completion_tokens: 0 } },
      { ...item(1), usage: { input_tokens: 0, prompt_tokens: 90, output_tokens: 0, completion_tokens: 70, total_tokens: 0 } },
    ], total: 2, offset: 0, limit: 50 } as never);
    render(<ExperimentUsage experimentId="id" usage={usage} />);
    expand();
    expect(screen.getByText(/生成记录数：52/)).toBeInTheDocument();
    expect(screen.getByText(/改版记录数：52/)).toBeInTheDocument();
    expect(screen.getByText(/评分调用次数：52/)).toBeInTheDocument();
    const rawRow = (await screen.findByText("样本-0")).closest("tr")!;
    expect(within(rawRow).getAllByRole("cell").slice(-3).map((cell) => cell.textContent)).toEqual(["17", "0", "未知"]);
    const normalizedRow = screen.getByText("样本-1").closest("tr")!;
    expect(within(normalizedRow).getAllByRole("cell").slice(-3).map((cell) => cell.textContent)).toEqual(["0", "0", "0"]);
  });

  it("翻页后的旧响应不能覆盖当前页", async () => {
    const old = deferred<typeof firstPage>();
    fetchPage.mockResolvedValueOnce(firstPage).mockReturnValueOnce(old.promise).mockResolvedValueOnce(firstPage);
    render(<ExperimentUsage experimentId="id" usage={usage} />);
    expand();
    await screen.findByText("样本-0");
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "上一页" }));
    await screen.findByText("样本-0");
    await act(async () => { old.resolve({ items: [item(50)], total: 52, offset: 50, limit: 50 }); });
    expect(screen.queryByText("样本-50")).not.toBeInTheDocument();
  });

  it("切换实验重新折叠，旧实验响应失效", async () => {
    const old = deferred<typeof firstPage>();
    fetchPage.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ items: [item(99)], total: 1, offset: 0, limit: 50 });
    const view = render(<ExperimentUsage experimentId="old" usage={usage} />);
    expand();
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1));
    view.rerender(<ExperimentUsage experimentId="new" usage={usage} />);
    expect(screen.queryByRole("table", { hidden: true })).not.toBeInTheDocument();
    expand();
    await screen.findByText("样本-99");
    await act(async () => { old.resolve(firstPage); });
    expect(screen.queryByText("样本-0")).not.toBeInTheDocument();
    expect(fetchPage).toHaveBeenLastCalledWith("new", { offset: 0, limit: 50 });
  });

  it("展开定期更新当前页，收起停止刷新且忽略未完成响应", async () => {
    vi.useFakeTimers();
    const next = deferred<typeof firstPage>();
    fetchPage.mockResolvedValueOnce(firstPage).mockReturnValueOnce(next.promise);
    render(<ExperimentUsage experimentId="id" usage={usage} />);
    await act(async () => { expand(); });
    expect(fetchPage).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expand();
    await act(async () => { next.resolve(firstPage); await vi.advanceTimersByTimeAsync(15000); });
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("样本-0")).not.toBeInTheDocument();
  });
});
