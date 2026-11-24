import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { getActiveRuns, getExperiment, type ActiveRun, type Experiment } from "@/lib/api";
import { useTracePolling } from "@/hooks/useTraceStream";
import ExperimentImprovementDetail from "./ExperimentImprovementDetail";

vi.mock("@/lib/api", () => ({ getActiveRuns: vi.fn(), getExperiment: vi.fn() }));
vi.mock("@/pages/trace-detail", () => ({ default: ({ traceIdOverride }: { traceIdOverride: string }) => <p>{traceIdOverride}</p> }));
vi.mock("@/hooks/useTraceStream", () => ({ useTracePolling: vi.fn(() => ({ detail: null, isStale: false })) }));

it("改进轮次等待 Trace 同步，随后只打开同一实验同一轮的实时 Trace", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(getExperiment).mockResolvedValue({ id: "run", name: "正文实验", status: "running", phase: "evolving",
      current_round: 2, can_read_content: true, task_type: "chapter_text", baseline: { commit: "base" },
      rounds: [{ round: 2, status: "evolving" }] } as unknown as Experiment);
    const live = { run_purpose: "evolution_evolve", trace_id: "live-2", external_refs: {
      experiment_id: "run", round: "2", phase: "improvement",
    } } as unknown as ActiveRun;
    vi.mocked(getActiveRuns).mockResolvedValueOnce([]).mockResolvedValue([
      { ...live, trace_id: "wrong", external_refs: { ...live.external_refs, round: "3" } }, live,
    ]);
    vi.mocked(useTracePolling).mockImplementation(traceId => ({ isStale: false, detail: traceId === "live-2" ? {
      run: { trace_id: traceId }, nodes: [{ kind: "tool", tool_name: "edit_source", status: "running" }],
    } : null }) as ReturnType<typeof useTracePolling>);
    render(<MemoryRouter initialEntries={["/bench/experiments/run/rounds/2?agent=text"]}><Routes>
      <Route path="/bench/experiments/:experimentId/rounds/:round" element={<ExperimentImprovementDetail />} />
    </Routes></MemoryRouter>);
    await act(async () => {});
    expect(screen.getByText("等待执行进度")).toBeVisible();
    expect(screen.queryByText("live-2")).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("修改文件")).toBeVisible();
    expect(screen.queryByText("edit_source")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("运行日志"));
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    expect(screen.getByText("live-2")).toBeVisible();
    expect(screen.queryByText("wrong")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /返回实验详情/ })).toHaveAttribute("href",
      "/bench/experiments/run?agent=text&anchor=record-improvement-2");
  } finally {
    vi.useRealTimers();
  }
});

it("首屏按文件显示真实增删，将进化 Trace 留在可展开的运行详情中", async () => {
  const diff = [
    "diff --git a/prompts/creative_text.md b/prompts/creative_text.md",
    "index abc123..def456 100644",
    "--- a/prompts/creative_text.md",
    "+++ b/prompts/creative_text.md",
    "@@ -4,2 +4,2 @@",
    "-旧规则",
    "+新增关键行动因果核验",
    " 保留现有章纲",
  ].join("\n");
  vi.mocked(getExperiment).mockResolvedValue({ id: "run", name: "正文实验", status: "completed", can_read_content: true,
    task_type: "chapter_text", baseline: { commit: "base" }, rounds: [{ round: 1, status: "completed", diff,
      summary: "---\nexecuted_at: '2026-11-21'\n---", trace_ids: ["improvement-1"] }] } as unknown as Experiment);
  render(<MemoryRouter initialEntries={["/bench/experiments/run/rounds/1"]}><Routes>
    <Route path="/bench/experiments/:experimentId/rounds/:round" element={<ExperimentImprovementDetail />} />
  </Routes></MemoryRouter>);
  const changes = await screen.findByRole("region", { name: "文件差异" });
  expect(within(changes).getByText("prompts/creative_text.md")).toBeVisible();
  expect(within(changes).getByText("旧规则", { selector: "del" })).toBeVisible();
  expect(within(changes).getByText("新增关键行动因果核验", { selector: "ins" })).toBeVisible();
  expect(screen.queryByText(/executed_at|abc123/)).not.toBeInTheDocument();
  const runtime = screen.getByText("运行日志");
  expect(runtime.closest("details")).not.toHaveAttribute("open");
  expect(changes.compareDocumentPosition(runtime) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByText("improvement-1")).not.toBeInTheDocument();
  fireEvent.click(runtime);
  expect(await screen.findByText("improvement-1")).toBeVisible();
});

it("把改版起点、失败复盘和决定放在首屏，按需展开去重后的证据", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ id: "run", name: "正文实验", status: "completed", can_read_content: true,
    task_type: "chapter_text", baseline: { version: 10, commit: "base" },
    settings: { optimization_ids: ["case"] },
    rounds: [{ round: 1, source_commit: "improvement-1" }, { round: 3, status: "completed", base_commit: "improvement-1",
      design: "详细的改动方案", trace_ids: ["improvement-3"], evidence_samples: ["previous"], failure_review: {
        round: 2, decision: "change_direction", reason: "上一轮改动漏掉了动作衔接。", evidence_refs: ["baseline", "previous"],
        manuscript_location: "正文/第002章.md:35-43",
      } }],
    samples: [{ id: "baseline", arm: "baseline", round: 0, package_id: "case", package_title: "改进题", phase: "baseline",
      artifacts: {}, trace_ids: [] }, { id: "previous", arm: "candidate", round: 2, package_id: "case", package_title: "改进题",
      phase: "exploration", artifacts: {}, trace_ids: [] }],
  } as unknown as Experiment);
  render(<MemoryRouter initialEntries={["/bench/experiments/run/rounds/3"]}><Routes>
    <Route path="/bench/experiments/:experimentId/rounds/:round" element={<ExperimentImprovementDetail />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByText(/起点 第 1 轮候选/)).toBeVisible();
  const process = within(screen.getByRole("region", { name: "执行过程" }));
  expect(process.getByText("复盘第 2 轮")).toBeVisible();
  expect(process.getByText("调整方向")).toBeVisible();
  expect(process.getByText("上一轮改动漏掉了动作衔接。")).toBeVisible();
  expect(process.getByText("正文/第002章.md:35-43")).toBeVisible();
  expect(process.getByText("查看方案与证据").closest("details")).not.toHaveAttribute("open");
  fireEvent.click(process.getByText("查看方案与证据"));
  expect(process.getByText("详细的改动方案")).toBeVisible();
  expect(process.getAllByText(/依据样本 · 改进题/)).toHaveLength(2);
});
