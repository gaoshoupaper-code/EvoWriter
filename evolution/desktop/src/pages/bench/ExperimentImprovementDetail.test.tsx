import { act, render, screen } from "@testing-library/react";
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
      rounds: [{ round: 2, status: "evolving", design: "减少重复对话" }] } as unknown as Experiment);
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
    expect(screen.getByText("等待 Trace 同步…")).toBeVisible();
    expect(screen.getByText("减少重复对话")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("live-2")).toBeVisible();
    expect(screen.queryByText("wrong")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /返回实验详情/ })).toHaveAttribute("href",
      "/bench/experiments/run?agent=text&anchor=record-improvement-2");
  } finally {
    vi.useRealTimers();
  }
});
