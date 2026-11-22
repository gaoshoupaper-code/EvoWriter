import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { getExperiment, type Experiment } from "@/lib/api";
import ExperimentComparison from "./ExperimentComparison";

vi.mock("@/lib/api", () => ({ getExperiment: vi.fn() }));

function TraceDestination() {
  const { search } = useLocation();
  return <p>Trace Agent {new URLSearchParams(search).get("agent")}</p>;
}

it("从回归比较进入时优先显示同一批回归产物并提供原评测区返回链接", async () => {
  const baseline = { id: "base", package_id: "reg", package_title: "回归题", input_revision: "input",
    task_type: "chapter_text", arm: "baseline", phase: "regression", round: 2, repeat: 1, attempt: 1, completed: true, trace_ids: [],
    target: { chapter: 1 }, artifacts: { "正文/第001章.md": "原版回归稿。" } };
  const report = { baseline: { total: 3, completed: 3, completion_rate: 1, quality: 3.5, evaluation_errors: 0 },
    candidate: { total: 3, completed: 3, completion_rate: 1, quality: 4, evaluation_errors: 0 },
    paired_count: 3, quality_delta: 0.5, passed: true };
  vi.mocked(getExperiment).mockResolvedValue({
    id: "run", task_type: "chapter_text", can_read_content: true, baseline: { commit: "base" }, report: {},
    rounds: [{ round: 2, regressions: { chapter_text: report } }],
    samples: [{ ...baseline, id: "first", phase: "baseline", package_title: "改进题", artifacts: { "正文/第001章.md": "原版改进稿。" } },
      baseline, { ...baseline, id: "candidate", arm: "candidate", artifacts: { "正文/第001章.md": "候选回归稿。" } }],
  } as unknown as Experiment);
  render(<MemoryRouter initialEntries={["/bench/experiments/run/comparison?agent=text&round=2&phase=regression"]}><Routes>
    <Route path="/bench/experiments/:experimentId/comparison" element={<ExperimentComparison />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByText("候选回归稿。")).toBeVisible();
  expect(screen.getByText("原版回归稿。")).toBeVisible();
  expect(screen.queryByText("原版改进稿。")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /返回实验详情/ })).toHaveAttribute("href",
    "/bench/experiments/run?agent=text&round=2&phase=regression&anchor=comparison-regression-2");
});

it("对比链接省略 Agent 时，打开 Trace 仍保留评测记住的写作套", async () => {
  localStorage.setItem("evolution.agent.evaluation", "text");
  localStorage.setItem("evolution.agent.observation", "outline");
  try {
    vi.mocked(getExperiment).mockResolvedValue({
      id: "run", name: "正文对比实验", task_type: "chapter_text", can_read_content: true, report: {},
      baseline: { commit: "base" }, rounds: [],
      samples: [{ id: "baseline", package_id: "prompt", package_title: "题目", input_revision: "input",
        task_type: "chapter_text", arm: "baseline", phase: "baseline", round: 0, repeat: 1, attempt: 1,
        completed: true, artifacts: {}, trace_ids: ["writing-trace"] }],
    } as unknown as Experiment);
    render(<MemoryRouter initialEntries={["/bench/experiments/run/comparison"]}><Routes>
      <Route path="/bench/experiments/:experimentId/comparison" element={<ExperimentComparison />} />
      <Route path="/traces/:traceId" element={<TraceDestination />} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "生成 Trace 1" }));
    expect(await screen.findByText("Trace Agent text")).toBeVisible();
  } finally {
    localStorage.removeItem("evolution.agent.evaluation");
    localStorage.removeItem("evolution.agent.observation");
  }
});

it("正文对比只显示所选同题同次的运行依据", async () => {
  const sample = { package_id: "same-case", package_title: "回归题", input_revision: "same-input", phase: "regression",
    task_type: "chapter_text", round: 1, attempt: 1, completed: true, trace_ids: [], artifacts: {} };
  const scores = (value: number, baseline: number) => ({ overall: 4, quality: 4, dimensions: { 运行时: 4 }, reasons: {},
    runtime: { score: 4, tools: { successes: 8, total: 10, rate: .8, score: 4.2 },
      tokens: { value, baseline, saving: (baseline - value) / baseline, score: 4 }, skill: { score: 4 } } });
  vi.mocked(getExperiment).mockResolvedValue({ id: "run", name: "正文实验", task_type: "chapter_text", can_read_content: false,
    report: {}, baseline: { commit: "base" }, rounds: [{ round: 1 }], runtime_scoring_version: 1, samples: [
      { ...sample, id: "original-1", arm: "baseline", repeat: 1, scores: scores(70000, 70000) },
      { ...sample, id: "candidate-1", arm: "candidate", repeat: 1, scores: scores(60000, 70000) },
      { ...sample, id: "original-2", arm: "baseline", repeat: 2, scores: scores(80000, 80000) },
      { ...sample, id: "candidate-2", arm: "candidate", repeat: 2, scores: scores(72000, 80000) },
    ] } as unknown as Experiment);
  render(<MemoryRouter initialEntries={["/bench/experiments/run/comparison?agent=text&round=1&phase=regression"]}><Routes>
    <Route path="/bench/experiments/:experimentId/comparison" element={<ExperimentComparison />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByText("60,000")).toBeVisible();
  const choice = screen.getByRole<HTMLOptionElement>("option", { name: /重复 2/ });
  fireEvent.change(screen.getByLabelText("题目与重复"), { target: { value: choice.value } });
  expect(screen.getByText("72,000")).toBeVisible();
  expect(screen.getAllByText("80,000")).toHaveLength(3);
  expect(screen.queryByText("60,000")).not.toBeInTheDocument();
});

it.each([["exploration", "优化复测"], ["validation", "独立终验"]])(
  "旧候选的 %s 对比不借用当前候选的汇总分数", async (phase, heading) => {
    const metrics = { total: 3, completed: 3, completion_rate: 1, quality: 3.5, evaluation_errors: 0 };
    const latest = { baseline: metrics, candidate: { ...metrics, quality: 4.7 }, passed: true };
    vi.mocked(getExperiment).mockResolvedValue({ id: "run", name: "历史实验", task_type: "master_outline",
      current_round: 3, can_read_content: true, rounds: [{ round: 2, report: {} }], samples: [],
      report: { exploration: latest, validation: latest },
    } as unknown as Experiment);
    render(<MemoryRouter initialEntries={["/bench/experiments/run/comparison?round=2&phase=" + phase]}><Routes>
      <Route path="/bench/experiments/:experimentId/comparison" element={<ExperimentComparison />} />
    </Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "历史实验" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: new RegExp(heading) })).not.toBeInTheDocument();
    expect(screen.queryByText("4.700")).not.toBeInTheDocument();
  },
);

it.each([
  { query: "&task=chapter_outlines", expected: ["章纲回归"], absent: ["卷纲回归", "卷纲产物。"] },
  { query: "", expected: ["卷纲回归", "章纲回归", "卷纲产物。"], absent: [] },
])("读取旧版跨写作类型的回归报告 $query", async ({ query, expected, absent }) => {
  const metrics = { total: 3, completed: 3, completion_rate: 1, quality: 3.5, evaluation_errors: 0 };
  const volume = { id: "volume", package_id: "volumes", package_title: "卷纲题", task_type: "volume_outlines",
    arm: "baseline", phase: "regression", round: 2, repeat: 1, attempt: 1, completed: true, trace_ids: [],
    artifacts: { "卷纲/第01卷.md": "卷纲产物。" } };
  const chapter = { ...volume, id: "chapter", package_id: "chapters", package_title: "章纲题", task_type: "chapter_outlines",
    artifacts: { "章纲/第01章.md": "章纲产物。" } };
  vi.mocked(getExperiment).mockResolvedValue({ id: "run", flow_version: 2, task_type: "master_outline",
    can_read_content: true, report: {}, rounds: [{ round: 2, regressions: {
      volume_outlines: { baseline: metrics, candidate: { ...metrics, quality: 3.6 }, passed: true },
      chapter_outlines: { baseline: metrics, candidate: { ...metrics, quality: 4.0 }, passed: true },
    } }], samples: [volume, chapter],
  } as unknown as Experiment);
  render(<MemoryRouter initialEntries={["/bench/experiments/run/comparison?agent=outline&round=2&phase=regression" + query]}><Routes>
    <Route path="/bench/experiments/:experimentId/comparison" element={<ExperimentComparison />} />
  </Routes></MemoryRouter>);
  for (const text of expected) expect(await screen.findByText(text, { exact: false })).toBeVisible();
  for (const text of absent) expect(screen.queryByText(text, { exact: false })).not.toBeInTheDocument();
  if (query) {
    expect(screen.getByText("章纲产物。")).toBeVisible();
    expect(screen.getByRole("link", { name: /返回实验详情/ })).toHaveAttribute("href",
      "/bench/experiments/run?agent=outline&round=2&phase=regression&task=chapter_outlines&anchor=comparison-regression-2-chapter_outlines");
  }
});
