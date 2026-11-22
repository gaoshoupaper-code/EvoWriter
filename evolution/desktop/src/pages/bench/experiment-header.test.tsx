import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { getActiveRuns, getExperiment, publishExperiment, restoreExperiment, type Experiment } from "@/lib/api";
import ExperimentDetail from "./ExperimentDetail";
import ExperimentReview from "./ExperimentReview";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getExperiment: vi.fn(), getActiveRuns: vi.fn(), publishExperiment: vi.fn(), restoreExperiment: vi.fn(),
}));

const noCalls = { calls: 0, failed_calls: 0, input_tokens: null, output_tokens: null, total_tokens: null, known: false, partial: false };
const base: Experiment = {
  id: "header-archive", name: "正文评测", task_type: "chapter_text", assessment_version: 1,
  phase: "baseline", status: "running", current_round: 0, max_rounds: 3, end_reason: null,
  baseline: { version: 1, commit: "base" }, best_candidate: null,
  models: { executor: { name: "执行配置", model: "writer" }, judges: [{ name: "评测配置", model: "judge" }] },
  samples: [], rounds: [], report: {}, events: [],
  settings: { task_type: "chapter_text", baseline_version: 1, model_config_id: 1, judge_group_id: 2,
    rule_versions: {}, optimization_ids: [], regression_ids: {}, max_rounds: 3,
    thresholds: { completion_rate: 1, quality_gain: 0.1, regression_tolerance: {} } },
  usage: { generation: noCalls, evaluation: noCalls, evolution: noCalls, known: false },
  can_read_content: true, can_publish: false, can_restore: false, publish_status: "not_started",
  stale_reason: null, loaded_revision: null, load_error: null, rollback_status: null,
  created_at: "2026-11-19T00:00:00Z", updated_at: "2026-11-19T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  vi.mocked(getActiveRuns).mockResolvedValue([]);
});
afterEach(() => vi.mocked(window.scrollTo).mockRestore());

async function openArchive(overrides: Partial<Experiment>) {
  vi.mocked(getExperiment).mockResolvedValue({ ...base, ...overrides });
  render(<MemoryRouter initialEntries={["/bench/experiments/header-archive"]}><Routes>
    <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    <Route path="/bench/experiments/:experimentId/review" element={<ExperimentReview />} />
  </Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: base.name });
}

it("紧凑版头先列出四项信息，再展开已有的运行和评测", async () => {
  await openArchive({
    status: "stopped",
    samples: [{ id: "sample", arm: "baseline", phase: "baseline", round: 0, package_title: "当前活跃题",
      package_id: "p", task_type: "chapter_text", repeat: 1, attempt: 1, status: "running", task_id: "t",
      trace_ids: [], completed: false, checks: {}, scores: null, error: null, artifacts: {}, usage: {},
      input_revision: "fixed", source_commit: "base" }],
    progress: { total: 4, done: 1, completed: 1 }, end_reason: "停止原因记录",
  });
  const header = within(screen.getByRole("banner"));
  const overview = within(screen.getByLabelText("实验概况"));
  const phases = screen.getByRole("region", { name: "实验流程" });
  expect(within(phases).getAllByRole("region").map((region) => region.getAttribute("aria-label"))).toEqual([
    "运行 · 第 1 轮", "评测 · 第 1 轮",
  ]);
  for (const label of ["Harness 原版 → 当前候选", "执行模型", "评测模型", "保留最佳候选"]) {
    expect(overview.getByText(label).compareDocumentPosition(phases) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
  expect(header.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.getByText(/停止原因记录/)).toBeInTheDocument();
  for (const removed of [/当前活跃题/, /最近活动/, /固定版本与实验设置/, /调用用量/, /改版先检查启动/]) {
    expect(header.queryByText(removed)).not.toBeInTheDocument();
  }
  expect(within(screen.getByRole("banner")).queryByText(base.id)).not.toBeInTheDocument();
});

it.each(["running", "validation_failed", "exhausted", "stopped"])("%s 时不展示空审阅区", async (status) => {
  await openArchive({ status });
  expect(screen.queryByRole("link", { name: /进入审核|查看发布状态/ })).not.toBeInTheDocument();
  expect(publishExperiment).not.toHaveBeenCalled();
  expect(restoreExperiment).not.toHaveBeenCalled();
});

it.each(["chapter_text", "volume_outlines", "chapter_outlines"] as const)("历史 %s 详情移除设置和调用用量", async (task_type) => {
  await openArchive({ task_type, assessment_version: undefined });
  for (const label of [/固定版本与实验设置/, /调用用量/, /未知值单独标记/, /评测范围/]) {
    expect(screen.queryByText(label)).not.toBeInTheDocument();
  }
});

it("在实验详情展示启动时的全量覆盖", async () => {
  await openArchive({ case_coverage: {
    optimization: { selected: 4, total: 4 }, regression: { selected: 3, total: 3 },
  } });
  const overview = within(screen.getByLabelText("实验概况"));
  expect(overview.getByText("全量评测 · 改进 4/4 道，回归 3/3 道")).toBeVisible();
});

it.each([
  { status: "awaiting_review", publish_status: "not_started", can_publish: true, can_restore: false, label: "尚未发布", action: "启用达标版本" },
  { status: "publishing", publish_status: "publishing", can_publish: false, can_restore: false, label: "发布中", action: null },
  { status: "publish_failed", publish_status: "failed", can_publish: true, can_restore: false, label: "发布失败", action: "重试启用" },
  { status: "activated", publish_status: "activated", can_publish: false, can_restore: true, label: "已生效", action: "恢复旧版" },
  { status: "restored", publish_status: "restored", can_publish: false, can_restore: false, label: "已恢复旧版", action: null },
])("$status 时在审核详情展示真实发布状态和可用操作", async ({ action, label, ...state }) => {
  await openArchive({ ...state, report: { passed: true },
    phase: state.status === "awaiting_review" ? "review" : "publish",
    best_candidate: { round: 1, commit: "candidate", digest: "digest", quality: 4, completion_rate: 1 } });
  const entry = await screen.findByRole("link", { name: state.status === "awaiting_review" ? "进入审核" : "查看发布状态" });
  expect(screen.queryByRole("button", { name: /启用达标版本|重试启用|恢复旧版/ })).not.toBeInTheDocument();
  fireEvent.click(entry);
  expect((await screen.findAllByText(label)).some((element) => element.textContent === label)).toBe(true);
  const actions = screen.queryAllByRole("button", { name: /启用达标版本|重试启用|恢复旧版/ });
  expect(actions.map((button) => button.textContent)).toEqual(action ? [action] : []);
  expect(publishExperiment).not.toHaveBeenCalled();
  expect(restoreExperiment).not.toHaveBeenCalled();
});
