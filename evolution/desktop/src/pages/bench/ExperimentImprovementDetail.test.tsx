import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { getActiveRuns, getExperiment, type ActiveRun, type Experiment } from "@/lib/api";
import ExperimentImprovementDetail from "./ExperimentImprovementDetail";

vi.mock("@/lib/api", () => ({ getActiveRuns: vi.fn(), getExperiment: vi.fn() }));
vi.mock("@/pages/trace-detail", () => ({ default: ({ traceIdOverride }: { traceIdOverride: string }) => <p>{traceIdOverride}</p> }));

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
    render(<MemoryRouter initialEntries={["/bench/experiments/run/rounds/2?agent=text"]}><Routes>
      <Route path="/bench/experiments/:experimentId/rounds/:round" element={<ExperimentImprovementDetail />} />
    </Routes></MemoryRouter>);
    await act(async () => {});
    fireEvent.click(screen.getByText("进化运行详情"));
    expect(screen.getByText("等待 Trace 同步…")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
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
  const runtime = screen.getByText("进化运行详情");
  expect(runtime.closest("details")).not.toHaveAttribute("open");
  expect(changes.compareDocumentPosition(runtime) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(runtime);
  expect(screen.getByText("improvement-1")).toBeVisible();
});
