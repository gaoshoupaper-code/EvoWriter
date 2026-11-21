import { describe, expect, it } from "vitest";
import type { Experiment, ExperimentSample } from "./experiment-types";
import { experimentTimeline, evaluationFinished, hasExperimentReview } from "./experiment-timeline";

const sample = (phase: string, round: number, status = "completed") => ({
  id: `${phase}-${round}`, phase, round, status, completed: true, scores: null,
  evaluation_trace_ids: [],
} as unknown as ExperimentSample);
const improvement = (round: number, status = "passed") => ({ round, status, report: {}, trace_ids: [] });
const experiment = (samples: ExperimentSample[], rounds: ReturnType<typeof improvement>[]) =>
  ({ samples, rounds } as unknown as Experiment);

describe("实验页面展示轮次", () => {
  it("原版、复测和同候选回归各占一轮，不制造额外进化", () => {
    const rows = experimentTimeline(experiment([
      sample("baseline", 0), sample("exploration", 1), sample("regression", 1),
    ], [improvement(1)]));
    expect(rows.map(row => [row.kind, row.number])).toEqual([
      ["writing", 1], ["evaluation", 1], ["improvement", 1],
      ["writing", 2], ["evaluation", 2], ["writing", 3], ["evaluation", 3],
    ]);
    expect(rows.filter(row => row.kind === "writing").map(row => row.phase)).toEqual([
      "baseline", "exploration", "regression",
    ]);
  });

  it("连续改版失败均保留，只有实际新运行批次增加展示轮次", () => {
    const rows = experimentTimeline(experiment([
      sample("baseline", 0), sample("exploration", 3), sample("regression", 3), sample("exploration", 4),
    ], [improvement(1, "failed"), improvement(2, "failed"), improvement(3), improvement(4)]));
    expect(rows.filter(row => row.kind === "improvement").map(row => [row.number, row.improvement?.round])).toEqual([
      [1, 1], [1, 2], [1, 3], [3, 4],
    ]);
    expect(rows.filter(row => row.kind === "writing").map(row => row.number)).toEqual([1, 2, 3, 4]);
  });

  it("缺轮次记录的旧样本及独立终验仍可见", () => {
    const data = experiment([sample("baseline", 0), sample("exploration", 2), sample("regression", 2), sample("validation", 2)], []);
    expect(experimentTimeline(data).filter(row => row.kind === "writing").map(row => row.phase))
      .toEqual(["baseline", "exploration", "regression", "validation"]);
  });
});

describe("单条评分详情开放条件", () => {
  it("评分中、重试中即使有部分评分也不能提前打开", () => {
    const partial = { ...sample("exploration", 1, "scoring"), scores: { overall: 4, dimensions: {}, reasons: {} } };
    expect(evaluationFinished(partial)).toBe(false);
    expect(evaluationFinished({ ...partial, status: "evaluation_error", score_retry_at: 1 })).toBe(false);
  });

  it("已完成和最终评分失败可开详情，生成失败不伪造评分", () => {
    expect(evaluationFinished(sample("baseline", 0))).toBe(true);
    expect(evaluationFinished(sample("baseline", 0, "failed"))).toBe(true);
    expect(evaluationFinished({ ...sample("baseline", 0, "failed"), completed: false })).toBe(false);
  });
});

it("停止和仍在执行的实验不因旧报告误开审核，已发布或候选过期可查状态", () => {
  const data = { report: { passed: true }, best_candidate: { round: 1 }, publish_status: "not_started", can_publish: false };
  expect(hasExperimentReview({ ...data, status: "stopped" } as Experiment)).toBe(false);
  expect(hasExperimentReview({ ...data, status: "running" } as Experiment)).toBe(false);
  expect(hasExperimentReview({ ...data, status: "stale" } as Experiment)).toBe(true);
  expect(hasExperimentReview({ ...data, status: "activated", publish_status: "activated" } as Experiment)).toBe(true);
});
