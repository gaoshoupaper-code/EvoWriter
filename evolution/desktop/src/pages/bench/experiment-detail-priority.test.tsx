import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { getActiveRuns, getExperiment } from "@/lib/api";
import type { Experiment } from "@/lib/experiment-types";
import ExperimentDetail from "./ExperimentDetail";

vi.mock("@/lib/api", () => ({ getExperiment: vi.fn(), getActiveRuns: vi.fn(), stopExperiment: vi.fn() }));

const usage = {
  calls: 0, failed_calls: 0, input_tokens: null, output_tokens: null, total_tokens: null,
  known: false, partial: false,
};
const experiment: Experiment = {
  id: "run", name: "正文实验", task_type: "chapter_text", phase: "regression", status: "running",
  current_round: 2, max_rounds: 3, end_reason: null, baseline: { version: 1, commit: "base" },
  settings: {
    task_type: "chapter_text", baseline_version: 1, model_config_id: 1, judge_group_id: 2,
    rule_versions: {}, optimization_ids: [], regression_ids: {}, max_rounds: 3,
    thresholds: { completion_rate: 1, quality_gain: 0.1, regression_tolerance: {} },
  },
  best_candidate: null, rounds: [], samples: [], report: {}, events: [],
  usage: { generation: usage, evaluation: usage, evolution: usage, known: false },
  can_read_content: true, can_publish: false, stale_reason: null, publish_status: "not_started",
  loaded_revision: null, load_error: null, rollback_status: null,
  created_at: "2026-11-20T00:00:00Z", updated_at: "2026-11-20T00:00:00Z",
};

const metrics = { total: 2, completed: 2, completion_rate: 1, evaluation_errors: 0 };
const report: Experiment["report"] = { exploration_round: 2, exploration: {
  baseline: { ...metrics, quality: 3 }, candidate: { ...metrics, quality: 4 },
  paired_count: 2, quality_delta: 1, passed: true, new_failures: [], changes: [], problems: [],
} };

async function openExperiment(overrides: Partial<Experiment> = {}) {
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, ...overrides });
  vi.mocked(getActiveRuns).mockResolvedValue([]);
  render(<MemoryRouter initialEntries={["/bench/experiments/run"]}><Routes>
    <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
  </Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: experiment.name });
}

it("在详情头部显示当前阶段与真实评分，再展示实验流程", async () => {
  await openExperiment({ report });
  const header = within(screen.getByRole("banner"));
  expect(header.getByText("运行中")).toBeVisible();
  expect(header.getByText("本套回归")).toBeVisible();
  expect(within(header.getByRole("group", { name: "已有评分" })).getByText("3.000 → 4.000")).toBeVisible();
  expect(within(screen.getByRole("region", { name: "实验流程" })).getByText("正在准备实验记录。")).toBeVisible();
});

it.each(["stopped", "failed"])("已结束的 %s 旧记录明确显示无流程记录", async (status) => {
  await openExperiment({ status, end_reason: "已记录的结束原因" });
  expect(screen.getByText("暂无汇总评分")).toBeVisible();
  expect(within(screen.getByRole("region", { name: "实验流程" })).getByText("暂无实验流程记录。")).toBeVisible();
  expect(screen.queryByText("正在准备实验记录。")).not.toBeInTheDocument();
  expect(screen.getByText("已记录的结束原因")).toBeVisible();
});

function ReturnToExperiment() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>返回实验详情</button>;
}

it.each([false, true])("返回时按评分摘要更新后的记录位置检查视野，越界=%s", async (outsideViewport) => {
  const initial: Experiment = { ...experiment, samples: [{
    id: "sample", package_id: "package", package_title: "潮线邮局", input_revision: "input", source_commit: "base",
    task_id: "task", task_type: "chapter_text", phase: "baseline", round: 0, repeat: 1, attempt: 1, arm: "baseline",
    status: "completed", completed: true, trace_ids: [], checks: {}, artifacts: {}, usage: {}, error: null,
    scores: { overall: 3, dimensions: {}, reasons: {} },
  }] };
  vi.mocked(getExperiment).mockResolvedValue(initial);
  vi.mocked(getActiveRuns).mockResolvedValue([]);
  const { container } = render(<MemoryRouter initialEntries={["/bench/experiments/run"]}>
    <main className="shell-main"><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
      <Route path="/bench/experiments/:experimentId/records/:sampleId/writing" element={<ReturnToExperiment />} />
    </Routes></main>
  </MemoryRouter>);
  const shell = container.querySelector<HTMLElement>(".shell-main")!;
  const scrollIntoView = vi.fn();
  const originalScrollIntoView = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");
  Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    if (this === shell) return new DOMRect(0, 0, 600, 300);
    return new DOMRect(0, outsideViewport ? 320 : 260, 100, 20);
  });
  try {
    expect(await screen.findByText("暂无汇总评分")).toBeVisible();
    shell.scrollTop = 240;
    fireEvent.click(screen.getByRole("link", { name: "潮线邮局" }));
    const back = await screen.findByRole("button", { name: "返回实验详情" });
    shell.scrollTop = 0;
    vi.mocked(getExperiment).mockResolvedValue({ ...initial, report });
    fireEvent.click(back);
    expect(await screen.findByRole("group", { name: "已有评分" })).toBeVisible();
    expect(shell.scrollTop).toBe(240);
    const record = screen.getByRole("link", { name: "潮线邮局" });
    expect(record).toHaveFocus();
    if (outsideViewport) {
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
      expect(scrollIntoView.mock.contexts).toEqual([record]);
    } else expect(scrollIntoView).not.toHaveBeenCalled();
  } finally {
    if (originalScrollIntoView) Object.defineProperty(Element.prototype, "scrollIntoView", originalScrollIntoView);
    else Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  }
});
