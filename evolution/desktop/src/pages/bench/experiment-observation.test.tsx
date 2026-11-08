import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ExperimentRecords from "./ExperimentRecords";
import ExperimentDetail from "./ExperimentDetail";
import { ExperimentArtifacts } from "@/components/bench/ExperimentArtifacts";
import * as apiModule from "@/lib/api";

vi.mock("@/lib/api", () => ({ listExperiments: vi.fn(), getExperiment: vi.fn(), getExperimentPackage: vi.fn(), getActiveRuns: vi.fn(),
  stopExperiment: vi.fn(), publishExperiment: vi.fn(), restoreExperiment: vi.fn() }));
vi.mock("@/pages/trace-detail", () => ({ default: ({ traceIdOverride, displayContextOverride }: {
  traceIdOverride: string; displayContextOverride?: Record<string, unknown>;
}) => <div data-testid="embedded-trace" data-trace-id={traceIdOverride}
  data-display-context={JSON.stringify(displayContextOverride)}>运行详情</div> }));
const api = vi.mocked(apiModule);
const sample = (id: string, overrides = {}) => ({ id, package_id: "story-a", package_title: "山城样本",
  task_type: "chapter_text", phase: "exploration", arm: "candidate", round: 2, repeat: 1, attempt: 1,
  status: "failed", completed: false, trace_ids: ["generation-2"], evaluation_trace_ids: ["evaluation-2"],
  checks: {}, scores: null, error: "未完整生成", artifacts: { "第10章.md": "第十章内容", "第2章.md": "第二章内容" },
  usage: {}, input_revision: "input-a", source_commit: "candidate-a", ...overrides });
const run = (id: string, overrides = {}) => ({ id, name: id, task_type: "chapter_text", flow_version: 2,
  status: "running", phase: "exploration", current_round: 2, max_rounds: 3,
  baseline: { version: 1, commit: "base" }, best_candidate: null, settings: {}, report: {}, usage: {},
  rounds: [{ round: 2, status: "running", trace_ids: ["improvement-2"], report: {}, diff: "", regressions: {} }],
  samples: [sample("sample-2")], events: [], can_read_content: true, can_publish: false,
  publish_status: "not_started", created_at: "2026-11-08T01:00:00Z", updated_at: "2026-11-08T02:00:00Z", ...overrides });
beforeEach(() => { vi.clearAllMocks(); api.getActiveRuns.mockResolvedValue([]); });

describe("实验观测 / REQ-20261108-191146", () => {
  it("阶段分类内按新建时间倒序，旧记录活动更新不置顶", async () => {
    api.listExperiments.mockResolvedValue({ items: [run("旧正文", { created_at: "2026-11-01", updated_at: "2026-11-09" }),
      run("新正文", { created_at: "2026-11-08" }), run("卷纲实验", { task_type: "volume_outlines" })] } as never);
    render(<MemoryRouter><ExperimentRecords /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("tab", { name: /正文/ }));
    const links = screen.getAllByRole("link").filter((link) => ["旧正文", "新正文"].includes(link.textContent ?? ""));
    expect(links.map((link) => link.textContent)).toEqual(["新正文", "旧正文"]);
    expect(screen.queryByRole("link", { name: "卷纲实验" })).not.toBeInTheDocument();
  });

  it("三个标签页保留，生成、评分和改版 Trace 在运行过程中打开", async () => {
    api.getExperiment.mockResolvedValue(run("正文实验") as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/id"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    await screen.findByRole("tab", { name: "运行过程" });
    expect(screen.getByRole("tab", { name: "评分对比" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "产物文件" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /山城样本.*正文生成/ }));
    expect(await screen.findByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "generation-2");
    expect(JSON.parse(screen.getByTestId("embedded-trace").getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "正文实验", sample_name: "山城样本", operation: "generation", round: 2, repeat: 1,
    });
    fireEvent.click(screen.getByRole("button", { name: /山城样本.*正文评分/ }));
    expect(screen.getByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "evaluation-2");
    fireEvent.click(screen.getByRole("button", { name: /正文实验.*第 ?2 ?轮改版/ }));
    expect(screen.getByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "improvement-2");
    expect(JSON.parse(screen.getByTestId("embedded-trace").getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "正文实验", operation: "improvement", round: 2,
    });
    expect(screen.queryByText(/generation-2|evaluation-2|improvement-2/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Trace/ })).not.toBeInTheDocument();
  });

  it("失败样本仍可阅读实际文件，自然排序且切换版本不会串正文", async () => {
    const samples = [sample("candidate"), sample("baseline", { arm: "baseline", round: 0, phase: "baseline",
      status: "completed", completed: true, artifacts: { "第2章.md": "原版正文" } })];
    render(<ExperimentArtifacts samples={samples as never[]} canReadContent />);
    fireEvent.change(screen.getByLabelText("运行版本"), { target: { value: "candidate" } });
    const files = screen.getByRole("navigation", { name: "产物文件列表" });
    expect(within(files).getAllByRole("button").map((button) => button.textContent)).toEqual(["第2章.md", "第10章.md"]);
    expect(screen.getByText("第二章内容")).toBeInTheDocument();
    expect(screen.getByText(/未完成/)).toBeInTheDocument();
    fireEvent.click(within(files).getByRole("button", { name: "第10章.md" }));
    expect(screen.getByText("第十章内容")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("运行版本"), { target: { value: "baseline" } });
    await waitFor(() => expect(screen.getByText("原版正文")).toBeInTheDocument());
    expect(screen.queryByText("第十章内容")).not.toBeInTheDocument();
  });

  it("样本尚未回填 Trace 时，实时关联的长调用也能在实验内查看", async () => {
    api.getExperiment.mockResolvedValue(run("正文实验", { samples: [sample("sample-2", {
      status: "running", trace_ids: [], evaluation_trace_ids: [],
    })] }) as never);
    api.getActiveRuns.mockResolvedValue([{ trace_id: "live-before-save", run_purpose: "optimization",
      experiment_id: "id", external_refs: { experiment_id: "id", sample_id: "sample-2" }, status: "running" },
    { trace_id: "foreign-experiment", run_purpose: "optimization", experiment_id: "other",
      external_refs: { experiment_id: "other" }, status: "running" }] as never[]);
    render(<MemoryRouter initialEntries={["/bench/experiments/id"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /山城样本.*正文生成/ }));
    expect(api.getActiveRuns).toHaveBeenCalledWith("experiment");
    expect(screen.getByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "live-before-save");
    expect(screen.queryByText(/foreign-experiment/)).not.toBeInTheDocument();
  });

  it("运行快照保留旧名称，辅助行区分真实重复和秒级时间", async () => {
    api.getExperiment.mockResolvedValue(run("已改名实验", {
      samples: [sample("sample-2", { package_title: "已改名样本", trace_ids: ["generation-2", "generation-step-2"] })],
      trace_runs: {
        "generation-2": { trace_id: "generation-2", started_at: "2026-11-08T12:34:56Z", run_snapshot: {
          display_context: { experiment_name: "运行时实验", sample_name: "运行时样本", task_type: "chapter_text",
            operation: "generation", phase: "validation", arm: "candidate", round: 2, repeat: 3 },
        } },
        "generation-step-2": { trace_id: "generation-step-2", started_at: "2026-11-08T12:35:57Z" },
      },
    }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/id"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    const saved = await screen.findByRole("button", { name: /运行时样本.*正文生成/ });
    expect(saved).toHaveTextContent("运行时实验");
    expect(saved).toHaveTextContent("独立终验");
    expect(saved).toHaveTextContent("候选第2轮");
    expect(saved).toHaveTextContent("第3次重复");
    expect(saved).toHaveTextContent("34:56");
    expect(saved).not.toHaveTextContent("已改名");
    expect(saved).not.toHaveTextContent("generation-2");
    fireEvent.click(saved);
    expect(JSON.parse(screen.getByTestId("embedded-trace").getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "运行时实验", sample_name: "运行时样本", operation: "generation", repeat: 3,
    });
    const nextStep = screen.getByRole("button", { name: /已改名样本.*正文生成/ });
    expect(nextStep).toHaveTextContent("35:57");
    expect(nextStep).not.toHaveTextContent(/重试|尝试/);
    fireEvent.click(nextStep);
    expect(screen.getByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "generation-step-2");
  });

  it("运行中改版按真实轮次命名，不借用当前环节和样本", async () => {
    api.getExperiment.mockResolvedValue(run("正文实验", { phase: "validation", current_round: 3 }) as never);
    api.getActiveRuns.mockResolvedValue([{ trace_id: "live-improvement", run_purpose: "evolution_evolve",
      experiment_id: "id", external_refs: { experiment_id: "id", round: "2", phase: "improvement" },
      status: "running", started_at: "2026-11-08T12:34:56Z" }] as never[]);
    render(<MemoryRouter initialEntries={["/bench/experiments/id"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    const pending = await screen.findByRole("heading", { name: "正在执行的 Trace" });
    const entry = within(pending.closest("section")!).getByRole("button", { name: /正文实验.*第2轮改版/ });
    expect(entry).toHaveTextContent("34:56");
    expect(entry).not.toHaveTextContent(/山城样本|独立终验|重复|尝试/);
    fireEvent.click(entry);
    expect(screen.getByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "live-improvement");
    expect(JSON.parse(screen.getByTestId("embedded-trace").getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "正文实验", operation: "improvement", phase: "improvement", round: 2,
    });
  });

  it("实时 Trace 查询迟迟不返回时，实验详情和产物仍可读取", async () => {
    api.getExperiment.mockResolvedValue(run("可用的实验详情") as never);
    api.getActiveRuns.mockReturnValue(new Promise(() => {}));
    render(<MemoryRouter initialEntries={["/bench/experiments/id"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "可用的实验详情" })).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "产物文件" }), { button: 0, ctrlKey: false });
    expect(await screen.findByText("第二章内容")).toBeInTheDocument();
  });

  it("无正文权限明确提示，不冒充没有产物", () => {
    render(<ExperimentArtifacts samples={[sample("private")] as never[]} canReadContent={false} />);
    expect(screen.getByText(/没有正文读取权限/)).toBeInTheDocument();
    expect(screen.queryByText("第二章内容")).not.toBeInTheDocument();
    expect(screen.queryByText(/尚未生成/)).not.toBeInTheDocument();
  });
});
