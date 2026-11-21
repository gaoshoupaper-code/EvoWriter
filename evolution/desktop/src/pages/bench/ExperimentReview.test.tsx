import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { getExperiment, publishExperiment, restoreExperiment, type Experiment } from "@/lib/api";
import ExperimentReview from "./ExperimentReview";

vi.mock("@/lib/api", () => ({ getExperiment: vi.fn(), publishExperiment: vi.fn(), restoreExperiment: vi.fn() }));
const ready = { id: "run", name: "评测", task_type: "master_outline", status: "awaiting_review", publish_status: "not_started",
  best_candidate: { commit: "candidate-abc", round: 2 }, can_publish: true, report: { passed: true },
} as unknown as Experiment;
function open() {
  render(<MemoryRouter initialEntries={["/bench/experiments/run/review?agent=outline"]}><Routes>
    <Route path="/bench/experiments/:experimentId/review" element={<ExperimentReview />} />
  </Routes></MemoryRouter>);
}
beforeEach(() => { vi.resetAllMocks(); });

it("打开仅核对候选，主动发版只能请求一次，未确认加载保持可恢复", async () => {
  const pending = { ...ready, status: "publish_failed", publish_status: "unconfirmed", can_restore: true,
    load_error: "执行端还未反馈" };
  vi.mocked(getExperiment).mockResolvedValueOnce(ready).mockResolvedValue(pending);
  let finish!: (data: Experiment) => void;
  vi.mocked(publishExperiment).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  open();
  expect(await screen.findByText("candidate-abc")).toBeVisible();
  expect(publishExperiment).not.toHaveBeenCalled();
  const approve = screen.getByRole("button", { name: "批准并发布候选" });
  fireEvent.click(approve);
  fireEvent.click(approve);
  expect(publishExperiment).toHaveBeenCalledTimes(1);
  expect(publishExperiment).toHaveBeenCalledWith("run");
  await act(async () => { finish(pending); });
  expect(await screen.findByText("加载尚未确认")).toBeVisible();
  expect(screen.getByText("执行端还未反馈")).toBeVisible();
  expect(screen.getByRole("button", { name: "重试发布并核对加载" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "恢复旧版" })).toBeEnabled();
  expect(screen.getByRole("link", { name: /返回实验详情/ })).toHaveAttribute("href",
    "/bench/experiments/run?agent=outline&anchor=experiment-review");
});

it("发布中轮询到执行端确认后显示已生效，恢复仍须主动点击", async () => {
  vi.useFakeTimers();
  try {
    const publishing = { ...ready, status: "publishing", publish_status: "publishing", can_publish: false };
    const activated = { ...publishing, status: "activated", publish_status: "activated", can_restore: true,
      loaded_revision: "candidate-abc" };
    const restored = { ...activated, status: "restored", publish_status: "restored", can_restore: false,
      rollback_status: "restored", loaded_revision: "base-abc" };
    vi.mocked(getExperiment).mockResolvedValueOnce(publishing).mockResolvedValueOnce(activated).mockResolvedValue(restored);
    vi.mocked(restoreExperiment).mockResolvedValue(restored);
    open();
    await act(async () => {});
    expect(screen.queryByText("执行端已确认版本", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "恢复旧版" })).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("已生效", { selector: "strong" })).toBeVisible();
    expect(screen.getByText("执行端已确认版本", { exact: false })).toBeVisible();
    expect(restoreExperiment).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "恢复旧版" })); });
    expect(restoreExperiment).toHaveBeenCalledWith("run");
    expect(screen.getByText("已恢复旧版", { selector: "strong" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "恢复旧版" })).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("未达标实验直达审核也没有发布操作", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...ready, status: "exhausted", report: { passed: false },
    can_publish: false, best_candidate: null });
  open();
  expect(await screen.findByText("暂无可审核的候选。")).toBeVisible();
  expect(screen.queryByRole("button", { name: /发布候选/ })).not.toBeInTheDocument();
});

it("过期候选可核对原因，服务端允许恢复时才出现入口", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...ready, can_publish: false, can_restore: false,
    stale_reason: "生产基线已变化" });
  open();
  expect(await screen.findByText("生产基线已变化")).toBeVisible();
  expect(screen.queryByRole("button", { name: /发布候选|恢复旧版/ })).not.toBeInTheDocument();
});
