import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { listExperiments } from "@/lib/api";
import type { ExperimentSummary } from "@/lib/experiment-types";
import ExperimentRecords from "./ExperimentRecords";

vi.mock("@/lib/api", () => ({ listExperiments: vi.fn(), getExperiment: vi.fn() }));

it("每条实验优先显示状态、当前阶段和已有评分", async () => {
  window.location.hash = "#/bench/experiments?agent=text";
  const record = { task_type: "chapter_text", flow_version: 3, judge_repeats: 3,
    baseline: { version: 18 }, max_rounds: 5, created_at: "2026-11-20T08:00:00Z" };
  const metrics = { total: 2, completed: 2, completion_rate: 1, evaluation_errors: 0 };
  vi.mocked(listExperiments).mockResolvedValue({ items: [
    { ...record, id: "running", name: "尚未出分", status: "running", phase: "baseline", current_round: 0, report: {} },
    { ...record, id: "partial", name: "已有优化评分", status: "running", phase: "regression", current_round: 2,
      report: { exploration_round: 2, exploration: { baseline: { ...metrics, quality: 3 },
        candidate: { ...metrics, quality: 4 }, quality_delta: 1, passed: true } } },
  ] as ExperimentSummary[] });

  render(<MemoryRouter><ExperimentRecords /></MemoryRouter>);

  const running = within((await screen.findByRole("link", { name: "尚未出分" })).closest("article")!);
  expect(running.getByText("运行中")).toBeVisible();
  expect(running.getByText("原版评测")).toBeVisible();
  expect(running.getByText("暂无汇总评分")).toBeVisible();
  expect(running.queryByRole("group", { name: "已有评分" })).not.toBeInTheDocument();

  const partial = within(screen.getByRole("link", { name: "已有优化评分" }).closest("article")!);
  expect(partial.getByText("本套回归")).toBeVisible();
  expect(within(partial.getByRole("group", { name: "已有评分" })).getByText("3.000 → 4.000")).toBeVisible();
  expect(partial.queryByText("待开始")).not.toBeInTheDocument();
  expect(partial.getByRole("link", { name: "查看评分明细" })).toHaveAttribute("href",
    "/bench/experiments?scoring=partial&round=2&phase=regression&task=chapter_text&agent=text");
});
