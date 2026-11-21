import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import ExperimentRecords from "./ExperimentRecords";
import ExperimentDetail from "./ExperimentDetail";
import ExperimentComparison from "./ExperimentComparison";
import ExperimentImprovementDetail from "./ExperimentImprovementDetail";
import ExperimentRecordDetail from "./ExperimentRecordDetail";
import { ExperimentArtifacts } from "@/components/bench/ExperimentArtifacts";
import TraceDetailPage from "@/pages/trace-detail";
import * as apiModule from "@/lib/api";

vi.mock("@/lib/api", () => ({ listExperiments: vi.fn(), getExperiment: vi.fn(), getExperimentPackage: vi.fn(), getActiveRuns: vi.fn(), getExperimentVersionSource: vi.fn(),
  stopExperiment: vi.fn(), publishExperiment: vi.fn(), restoreExperiment: vi.fn() }));
vi.mock("@/pages/trace-detail", () => ({ default: function TraceProbe({ traceIdOverride, displayContextOverride }: {
  traceIdOverride?: string; displayContextOverride?: Record<string, unknown>;
}) {
  const { traceId } = useParams();
  return <div data-testid="embedded-trace" data-trace-id={traceIdOverride ?? traceId}
    data-display-context={JSON.stringify(displayContextOverride)}>运行详情</div>;
} }));
const api = vi.mocked(apiModule);
const sample = (id: string, overrides = {}) => ({ id, package_id: "story-a", package_title: "山城样本",
  task_type: "chapter_text", phase: "exploration", arm: "candidate", round: 2, repeat: 1, attempt: 1,
  status: "failed", completed: false, trace_ids: ["generation-2"], evaluation_trace_ids: ["evaluation-2"],
  checks: {}, scores: null, error: "未完整生成", artifacts: { "第10章.md": "第十章内容", "第2章.md": "第二章内容" },
  usage: {}, input_revision: "input-a", source_commit: "candidate-a", ...overrides });
const run = (name: string, overrides = {}) => ({ id: "id", name, task_type: "chapter_text", flow_version: 3,
  status: "running", phase: "exploration", current_round: 2, max_rounds: 3,
  baseline: { version: 1, commit: "base" }, best_candidate: null, settings: {}, report: {}, usage: {},
  rounds: [{ round: 2, status: "running", trace_ids: ["improvement-2"], report: {}, diff: "", regressions: {} }],
  samples: [sample("sample-2")], events: [], can_read_content: true, can_publish: false,
  publish_status: "not_started", created_at: "2026-11-08T01:00:00Z", updated_at: "2026-11-08T02:00:00Z", ...overrides });
beforeEach(() => {
  window.location.hash = "#/agents/text/bench";
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  api.getActiveRuns.mockResolvedValue([]);
});
afterEach(() => vi.mocked(window.scrollTo).mockRestore());

function openArchive(path = "/bench/experiments/id") {
  return render(<div className="shell-main"><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    <Route path="/bench/experiments/:experimentId/records/:sampleId/:section" element={<ExperimentRecordDetail />} />
    <Route path="/bench/experiments/:experimentId/rounds/:round" element={<ExperimentImprovementDetail />} />
    <Route path="/bench/experiments/:experimentId/comparison" element={<ExperimentComparison />} />
    <Route path="/traces/:traceId" element={<TraceDetailPage />} />
  </Routes></MemoryRouter></div>);
}

describe("实验观测 / REQ-20261108-191146、REQ-20261119-203159", () => {
  it("从题目旁的评分进入详情，返回后焦点仍在同一题的评分入口", async () => {
    api.getExperiment.mockResolvedValue(run("archive", { id: "archive", assessment_version: 1, status: "stopped",
      samples: [sample("first"), sample("second", { package_title: "第二题", completed: true, status: "completed",
        error: null, scores: { overall: 3.4, dimensions: { "人物": 3.4 }, reasons: { "人物": "人物动机缺少铺垫" } },
      })] }) as never);
    const { container } = openArchive("/bench/experiments/archive?agent=text");
    const shell = container.querySelector<HTMLElement>(".shell-main")!;
    const rating = await screen.findByRole("link", { name: /第二题.*3\.40/ });
    expect(screen.queryByText("人物动机缺少铺垫")).not.toBeInTheDocument();
    expect(rating.getAttribute("href")).toBe("/bench/experiments/archive/records/second/evaluation?agent=text");
    shell.scrollTop = 420;
    fireEvent.click(rating);
    expect(await screen.findByText("人物动机缺少铺垫")).toBeVisible();
    shell.scrollTop = 0;
    fireEvent.click(screen.getByRole("button", { name: /返回/ }));
    await waitFor(() => expect(document.activeElement).toHaveAttribute("href", rating.getAttribute("href")));
    expect(shell.scrollTop).toBe(420);
    expect(screen.queryByText("人物动机缺少铺垫")).not.toBeInTheDocument();
  });

  it("正文档案按题目版本及重复配对，可比较两轮的改动、终稿、分差与 Trace", async () => {
    const metrics = { completed: 1, total: 1, completion_rate: 1, quality: 4.5, evaluation_errors: 0 };
    const version = (round: number) => ({ round, status: "completed", source_commit: "v" + round,
      base_commit: "base", summary: "优化点" + round, design: "判断依据" + round,
      report: { baseline: { ...metrics, quality: 3 }, candidate: metrics, passed: true }, diff: "", trace_ids: ["evo-" + round] });
    const draft = (round: number, content: string, score: number, overrides = {}) => sample("s" + round + content, {
      arm: round ? "candidate" : "baseline", round, phase: round ? "exploration" : "baseline",
      target: { task_type: "chapter_text", chapter: 2, volume_ids: [1] }, status: "completed", completed: true,
      source_commit: round ? "v" + round : "base", artifacts: { "正文/第002章.md": "共同段落\n\n" + content, "人物卡/甲.md": "非正文" },
      scores: { overall: score, dimensions: { "章纲兑现": score }, reasons: {} }, trace_ids: ["gen-" + round], error: null, ...overrides });
    api.getExperiment.mockResolvedValue(run("新正文档案", { assessment_version: 1,
      rounds: [version(1), version(2)], samples: [draft(0, "原版段落", 3), draft(1, "首轮新段", 4),
        draft(2, "本轮新段", 4.5), draft(2, "异版题文", 1, { input_revision: "other" }),
        draft(2, "其他重复", 2, { repeat: 2 })] }) as never);
    openArchive();
    const evaluation = await screen.findByRole("region", { name: "评测 · 第 3 轮" });
    fireEvent.click(within(evaluation).getByRole("link", { name: /正文对比/ }));
    const comparison = await screen.findByRole("region", { name: "跨版本终稿对比" });
    const view = within(comparison);
    expect(view.getByLabelText("源版本")).toHaveValue("0");
    expect(view.getByLabelText("对照版本")).toHaveValue("2");
    expect(view.getByText("原版段落").tagName).toBe("DEL");
    expect(view.getByText("本轮新段").tagName).toBe("INS");
    expect(view.getByRole("row", { name: /章纲兑现/ })).toHaveTextContent("+1.500");
    expect(view.queryByText("异版题文")).not.toBeInTheDocument();
    expect(view.queryByText("其他重复")).not.toBeInTheDocument();
    expect(view.queryByText("非正文")).not.toBeInTheDocument();
    fireEvent.change(view.getByLabelText("源版本"), { target: { value: "1" } });
    expect(view.getByText("优化点1")).toBeInTheDocument();
    expect(view.getByText("判断依据2")).toBeInTheDocument();
    expect(view.getByText("首轮新段").tagName).toBe("DEL");
    expect(view.getByRole("row", { name: /章纲兑现/ })).toHaveTextContent("+0.500");
    fireEvent.click(view.getAllByRole("button", { name: "生成 Trace 1" })[1]);
    expect(await screen.findByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "gen-2");
  });

  it("新档案原题没有配对产物时显示失败阶段和原因", async () => {
    api.getExperiment.mockResolvedValue(run("失败档案", { assessment_version: 1, rounds: [
      { round: 2, status: "failed", summary: "增加助手", error: "助手装配失败", diff: "", report: {} }],
      samples: [sample("source", { arm: "baseline", round: 0, phase: "baseline", error: null })] }) as never);
    openArchive("/bench/experiments/id/comparison?round=2");
    const view = within(await screen.findByRole("region", { name: "跨版本终稿对比" }));
    expect(view.getByText("助手装配失败")).toBeInTheDocument();
    expect(view.getByText("此版本尚无同题同次的生成记录。")).toBeInTheDocument();
    expect(view.queryByText(/分差.*0\.000/)).not.toBeInTheDocument();
  });

  it("终稿保留文件前言、章名以及正文内部标题和引语", async () => {
    const content = "# 第002章 山门\n> 状态：初稿\n> 进度：已完成\n\n她看向黑板。\n# 未寄出的信\n> 场景和人物都由你决定。\n> 状态糟透了，她说。";
    api.getExperiment.mockResolvedValue(run("原文完整性", { assessment_version: 1,
      samples: [sample("source", { arm: "baseline", round: 0, phase: "baseline", completed: true,
        target: { chapter: 2 }, artifacts: { "正文/第002章.md": content } })] }) as never);
    openArchive("/bench/experiments/id/comparison");
    const view = within(await screen.findByRole("region", { name: "跨版本终稿对比" }));
    for (const line of content.split("\n").filter(Boolean)) expect(view.getByText(line)).toBeVisible();
  });

  it("按需读取版本文件，切换轮次后迟到的原版正文不能覆盖候选内容", async () => {
    const path = "prompts/text.md";
    let finishBase!: () => void;
    api.getExperiment.mockResolvedValue(run("归档源码", { assessment_version: 1,
      rounds: [{ round: 1, status: "passed", source_commit: "v1", base_commit: "base", report: {} }] }) as never);
    api.getExperimentVersionSource.mockImplementation(async (_id, round, name) => {
      const commit = round ? "v1" : "base";
      if (!name) return { commit, paths: [path] };
      if (!round) await new Promise<void>((resolve) => { finishBase = resolve; });
      return { commit, path, content: round ? "候选文件内容" : "迟到的原版内容" };
    });
    openArchive("/bench/experiments/id/rounds/1");
    expect(api.getExperimentVersionSource).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "查看原版文件" }));
    fireEvent.change(await screen.findByLabelText("选择版本文件"), { target: { value: path } });
    await waitFor(() => expect(api.getExperimentVersionSource).toHaveBeenCalledWith("id", 0, path));
    const current = screen.getByRole("button", { name: "查看本轮文件" });
    const currentPanel = within(current.closest("div")!);
    fireEvent.click(current);
    fireEvent.change(await currentPanel.findByLabelText("选择版本文件"), { target: { value: path } });
    expect(await currentPanel.findByText("候选文件内容")).toBeVisible();
    await act(async () => finishBase());
    expect(currentPanel.getByText("候选文件内容")).toBeVisible();
    expect(currentPanel.queryByText("迟到的原版内容")).not.toBeInTheDocument();
  });

  it("从进化依据直接打开本档案改进样本的终稿、评分和写作 Trace", async () => {
    api.getExperiment.mockResolvedValue(run("可核对的优化", { assessment_version: 1, settings: { optimization_ids: ["story-a"] },
      rounds: [{ round: 2, status: "completed", evidence_samples: ["basis", "holdout", "foreign"], trace_ids: ["improvement-2"] }],
      samples: [sample("basis", { target: { chapter: 2 }, status: "completed", completed: true,
        artifacts: { "正文/第002章.md": "依据原稿全文" }, scores: { overall: 3, dimensions: { "章纲兑现": 3 }, reasons: { "章纲兑现": "缺少争吵场景" } } }),
        sample("holdout", { package_title: "独立复测正文", package_id: "holdout", phase: "regression" })] }) as never);
    openArchive("/bench/experiments/id/rounds/2");
    fireEvent.click(await screen.findByText(/依据样本 · 山城样本/));
    expect(screen.getByText("依据原稿全文")).toBeVisible();
    expect(screen.getByText("缺少争吵场景")).toBeVisible();
    expect(screen.queryByText(/依据样本 · 独立复测/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看依据生成 Trace 1" }));
    await waitFor(() => expect(screen.getAllByTestId("embedded-trace").some(
      (trace) => trace.getAttribute("data-trace-id") === "generation-2")).toBe(true));
  });

  it("独立复测按所选轮次取原版生成，不借其他轮次的分数或题文", async () => {
    const record = (id: string, arm: string, round: number, score: number, attempt = 1) => sample(id, {
      arm, round, status: "completed", phase: "regression", attempt, completed: true, target: { chapter: 1 },
      artifacts: { "正文/第001章审讯.md": id }, source_commit: arm === "baseline" ? "b" : "v" + round,
      scores: { overall: score, dimensions: { "逻辑连续": score }, reasons: {} }, error: null,
    });
    api.getExperiment.mockResolvedValue(run("复测配对", { assessment_version: 1,
      rounds: [1, 2].map((round) => ({ round, status: "completed", report: {}, trace_ids: [] })),
      samples: [record("较早原稿", "baseline", 1, 5), record("当轮原稿", "baseline", 2, 3),
        record("首轮终稿", "candidate", 1, 4), record("本轮首次", "candidate", 2, 2), record("本轮重试", "candidate", 2, 4.5, 2)]
    }) as never);
    openArchive("/bench/experiments/id/comparison?round=2");
    const view = within(await screen.findByRole("region", { name: "跨版本终稿对比" }));
    expect(view.getByText("当轮原稿", { selector: "del" })).toBeInTheDocument();
    expect(view.getByText("本轮重试", { selector: "ins" })).toBeInTheDocument();
    expect(view.queryByText("较早原稿")).not.toBeInTheDocument();
    expect(view.getByRole("row", { name: /逻辑连续/ })).toHaveTextContent("+1.500");
    fireEvent.change(view.getAllByLabelText("生成尝试")[1], { target: { value: "本轮首次" } });
    expect(view.getByText("本轮首次", { selector: "ins" })).toBeInTheDocument();
    expect(view.getByRole("row", { name: /逻辑连续/ })).toHaveTextContent("-1.000");
  });

  it("受限档案保留分数但隐藏正文、结论全文和失败详情", async () => {
    api.getExperiment.mockResolvedValue(run("受限档案", { assessment_version: 1, can_read_content: false,
      rounds: [{ round: 2, status: "failed", summary: "私有改法", design: "私有依据", error: "私有错误", report: {}, diff: "" }],
      samples: [sample("private", { scores: { overall: 2.5, dimensions: {}, reasons: {} } })] }) as never);
    openArchive("/bench/experiments/id/comparison?round=2");
    const view = within(await screen.findByRole("region", { name: "跨版本终稿对比" }));
    expect(view.getByText("2.500")).toBeInTheDocument();
    expect(view.getByText(/没有正文读取权限/)).toBeInTheDocument();
    expect(view.queryByText(/私有|第二章内容|尚无目标章正文/)).not.toBeInTheDocument();
  });

  it("受限实验进化详情不显示版本文件操作", async () => {
    api.getExperiment.mockResolvedValue(run("受限档案", { can_read_content: false,
      rounds: [{ round: 2, status: "failed", base_commit: "base", source_commit: "candidate", trace_ids: [],
        summary: "私有改法", design: "私有依据", error: "私有错误", report: {}, diff: "" }] }) as never);
    openArchive("/bench/experiments/id/rounds/2");
    expect(await screen.findByText("查看版本文件需要正文读取权限。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看原版文件" })).not.toBeInTheDocument();
    expect(screen.queryByText(/私有/)).not.toBeInTheDocument();
    expect(api.getExperimentVersionSource).not.toHaveBeenCalled();
  });

  it("阶段分类内按新建时间倒序，旧记录活动更新不置顶", async () => {
    api.listExperiments.mockResolvedValue({ items: [run("旧正文", { id: "old", created_at: "2026-11-01", updated_at: "2026-11-09" }),
      run("新正文", { id: "new", created_at: "2026-11-08" }), run("卷纲实验", { id: "volume", task_type: "volume_outlines" })] } as never);
    render(<MemoryRouter><ExperimentRecords /></MemoryRouter>);
    await screen.findByRole("link", { name: "新正文" });
    const links = screen.getAllByRole("link").filter((link) => ["旧正文", "新正文"].includes(link.textContent ?? ""));
    expect(links.map((link) => link.textContent)).toEqual(["新正文", "旧正文"]);
    expect(screen.queryByRole("link", { name: "卷纲实验" })).not.toBeInTheDocument();
  });

  it("运行、评测和进化各自通过流程入口打开对应 Trace", async () => {
    api.getExperiment.mockResolvedValue(run("正文实验", {
      samples: [sample("sample-2", { completed: true, error: "最终评分失败" })],
    }) as never);
    openArchive();
    const writing = await screen.findByRole("link", { name: "山城样本" });
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    fireEvent.click(writing);
    expect(await screen.findByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "generation-2");
    expect(JSON.parse(screen.getByTestId("embedded-trace").getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "正文实验", sample_name: "山城样本", operation: "generation", round: 2, repeat: 1,
    });
    fireEvent.click(screen.getByRole("button", { name: /返回/ }));
    fireEvent.click(await screen.findByRole("link", { name: /山城样本.*评分失败/ }));
    expect(await screen.findByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "evaluation-2");
    expect(screen.getByText("最终评分失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /返回/ }));
    fireEvent.click(await screen.findByRole("link", { name: /第 2 次改版/ }));
    expect(await screen.findByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "improvement-2");
    expect(JSON.parse(screen.getByTestId("embedded-trace").getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "正文实验", operation: "improvement", round: 2,
    });
    expect(screen.queryByText(/generation-2|evaluation-2|improvement-2/)).not.toBeInTheDocument();
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
    openArchive();
    fireEvent.click(await screen.findByRole("link", { name: "山城样本" }));
    expect(api.getActiveRuns).toHaveBeenCalledWith("experiment");
    expect(await screen.findByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "live-before-save");
    expect(screen.queryByText(/foreign-experiment/)).not.toBeInTheDocument();
  });

  it("运行行只保留作品名、真实秒级时间和用途，Trace 仍使用完整快照", async () => {
    api.getExperiment.mockResolvedValue(run("已改名实验", {
      samples: [sample("sample-2", { package_title: "已改名样本 · 按章纲写正文", source_commit: "cb2415dffb0",
        status: "completed", completed: true, attempt: 2, trace_ids: ["generation-2", "generation-step-2"] })],
      trace_runs: {
        "generation-2": { trace_id: "generation-2", started_at: "2026-11-08T12:34:56Z", run_snapshot: {
          display_context: { experiment_name: "运行时实验", sample_name: "运行时样本", task_type: "chapter_text",
            operation: "generation", phase: "validation", arm: "candidate", round: 2, repeat: 3 },
        } },
        "generation-step-2": { trace_id: "generation-step-2", started_at: "2026-11-08T12:35:57Z" },
      },
    }) as never);
    openArchive();
    const saved = await screen.findByRole("link", { name: "已改名样本" });
    const row = saved.closest("li")!;
    expect(row).toHaveTextContent("独立终验");
    expect(row).toHaveTextContent("34:56");
    expect(row).not.toHaveTextContent(/按章纲写正文|正文生成|已生成|运行时实验|已改名实验|候选第2轮|重复|尝试|cb2415d|generation-2/);
    fireEvent.click(saved);
    expect(JSON.parse((await screen.findByTestId("embedded-trace")).getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "运行时实验", sample_name: "运行时样本", operation: "generation", repeat: 3,
    });
    fireEvent.change(screen.getByLabelText("选择 Trace"), { target: { value: "generation-step-2" } });
    expect(screen.getByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "generation-step-2");
  });

  it("运行中改版按真实轮次命名，不借用当前环节和样本", async () => {
    api.getExperiment.mockResolvedValue(run("正文实验", { phase: "validation", current_round: 3, rounds: [
      { round: 2, status: "evolving", trace_ids: [], report: {}, diff: "" },
      { round: 3, status: "failed", trace_ids: [], report: {}, diff: "" },
    ] }) as never);
    api.getActiveRuns.mockResolvedValue([{ trace_id: "live-improvement", run_purpose: "evolution_evolve",
      experiment_id: "id", external_refs: { experiment_id: "id", round: "2", phase: "improvement" },
      status: "running", started_at: "2026-11-08T12:34:56Z" }] as never[]);
    openArchive();
    await waitFor(() => expect(document.getElementById("record-improvement-2")).toBeInTheDocument());
    const row = document.getElementById("record-improvement-2")!;
    const entry = within(row).getByRole("link");
    expect(entry).toHaveAttribute("href", "/bench/experiments/id/rounds/2");
    expect(row).not.toHaveTextContent(/山城样本|独立终验|重复/);
    fireEvent.click(entry);
    expect(await screen.findByTestId("embedded-trace")).toHaveAttribute("data-trace-id", "live-improvement");
    expect(JSON.parse(screen.getByTestId("embedded-trace").getAttribute("data-display-context")!)).toMatchObject({
      experiment_name: "正文实验", operation: "improvement", phase: "improvement", round: 2,
    });
  });

  it("实时 Trace 查询迟迟不返回时，实验详情和产物仍可读取", async () => {
    api.getExperiment.mockResolvedValue(run("可用的实验详情") as never);
    api.getActiveRuns.mockReturnValue(new Promise(() => {}));
    openArchive();
    expect(await screen.findByRole("heading", { name: "可用的实验详情" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "山城样本" }));
    expect(await screen.findByText("第二章内容")).toBeInTheDocument();
  });

  it("无正文权限明确提示，不冒充没有产物", () => {
    render(<ExperimentArtifacts samples={[sample("private")] as never[]} canReadContent={false} />);
    expect(screen.getByText(/没有正文读取权限/)).toBeInTheDocument();
    expect(screen.queryByText("第二章内容")).not.toBeInTheDocument();
    expect(screen.queryByText(/尚未生成/)).not.toBeInTheDocument();
  });
});
