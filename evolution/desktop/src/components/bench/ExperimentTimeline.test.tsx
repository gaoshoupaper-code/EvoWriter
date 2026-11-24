import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Experiment, ExperimentSample } from "@/lib/experiment-types";
import { ExperimentTimeline } from "./ExperimentTimeline";

const sample = (id: string, overrides = {}) => ({ id, package_title: id, package_id: id,
  task_id: id, checks: {}, error: null, artifacts: {}, usage: {}, input_revision: "input", source_commit: "base",
  phase: "baseline", round: 0, repeat: 1, attempt: 1, task_type: "chapter_text", arm: "baseline",
  status: "completed", completed: true, trace_ids: [], evaluation_trace_ids: [],
  scores: { overall: 4, dimensions: {}, reasons: {} }, ...overrides } as ExperimentSample);
const experiment = (samples: ExperimentSample[], overrides = {}) => ({
  id: "exp", name: "潮线邮局实验", samples, rounds: [], report: {}, can_read_content: true,
  models: { judges: [{ name: "评测", model: "judge-model" }] }, ...overrides,
} as unknown as Experiment);
beforeEach(() => { vi.clearAllMocks(); });
function open(data: Experiment) {
  return render(<MemoryRouter initialEntries={["/bench/experiments/exp?agent=text"]}>
    <ExperimentTimeline experiment={data} liveRuns={[]} onOpenRecord={vi.fn()} />
  </MemoryRouter>);
}

it("两类记录分行展示，左侧无操作且评分逐条开放", () => {
  open(experiment([sample("已结束"), sample("还在评测", { status: "scoring", scores: null }),
    sample("最终失败", { status: "failed", scores: null })]));
  const writing = within(screen.getByRole("region", { name: "运行 · 第 1 轮" }));
  const scoring = within(screen.getByRole("region", { name: "评测 · 第 1 轮" }));
  expect(writing.getByRole("link", { name: "已结束" })).toHaveAttribute("href",
    "/bench/experiments/exp/records/已结束/writing?agent=text".replace("已结束", encodeURIComponent("已结束")));
  expect(scoring.getByRole("link", { name: /已结束.*4.00/ })).toBeVisible();
  expect(scoring.queryByRole("link", { name: /还在评测/ })).not.toBeInTheDocument();
  expect(scoring.getByRole("link", { name: /最终失败.*评分失败/ })).toBeVisible();
  expect(scoring.getAllByText(/judge-model/)).toHaveLength(3);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("评分中或等待重试时可查看已记录的评委结果", () => {
  open(experiment([
    sample("已评分项", { status: "scoring", scores: null, score_progress: { completed: 0, total: 2,
      reasons: { 因果: [{ judge_id: 1, repeat: 1, score: 4, reason: "动机清楚", evidence: [] }] } } }),
    sample("已留 Trace", { status: "evaluation_error", scores: null, score_retry_at: 1, evaluation_trace_ids: ["trace"] }),
  ]));
  const scoring = within(screen.getByRole("region", { name: "评测 · 第 1 轮" }));
  expect(scoring.getByRole("link", { name: /已评分项.*评分中/ })).toBeVisible();
  expect(scoring.getByRole("link", { name: /已留 Trace.*等待重试评分/ })).toBeVisible();
});

it("生成失败不伪造评分，等待运行无Trace仍保留状态", () => {
  open(experiment([sample("failed", { status: "failed", completed: false, scores: null }),
    sample("pending", { status: "pending", completed: false, scores: null }),
    sample("running", { status: "running", completed: false, scores: null, execution_phase: "text:1" })]));
  expect(screen.getByText("生成失败，未评测")).toBeVisible();
  expect(screen.queryByRole("link", { name: "pending" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "running" })).toBeVisible();
  expect(screen.getByText("生成第1章正文")).toBeVisible();
});

it("运行行只留作品名、秒级时间和用途，移除批次标题及计数", () => {
  open(experiment([sample("story", { package_title: "潮线邮局 · 按章纲与文风创作第1章正文",
    source_commit: "da8beb1", trace_ids: ["trace"] })], {
    trace_runs: { trace: { started_at: "2026-11-19T11:17:50Z" } },
  }));
  const section = within(screen.getByRole("region", { name: "运行 · 第 1 轮" }));
  expect(section.getByRole("link", { name: "潮线邮局" })).toHaveAttribute("href", "/bench/experiments/exp/records/story/writing?agent=text");
  const row = document.getElementById("record-writing-story")!;
  expect(row.querySelector("time")).toHaveAttribute("datetime", "2026-11-19T11:17:50Z");
  expect(row).toHaveTextContent("17:50");
  expect(row).toHaveTextContent("原版基线");
  expect(row).not.toHaveTextContent(/潮线邮局实验|正文生成|按章纲|已生成|固定原版|次重复|次尝试|da8beb1/);
  expect(section.queryByRole("heading", { name: "原版评测" })).not.toBeInTheDocument();
  expect(section.queryByText(/已生成\s*\d+\s*\//)).not.toBeInTheDocument();
});

it("作品名中的中点保持完整，未知时间不会被实验日期替代", () => {
  open(experiment([sample("story", { package_title: "艾米·林的信 · 生成正文", trace_ids: [] })]));
  const section = within(screen.getByRole("region", { name: "运行 · 第 1 轮" }));
  expect(section.getByRole("link", { name: "艾米·林的信" })).toBeVisible();
  expect(document.getElementById("record-writing-story")!.querySelector("time")).toBeNull();
});

it("无正文权限时保留进化状态及入口，摘要全文不会泄露", () => {
  open(experiment([sample("base")], { can_read_content: false, rounds: [
    { round: 1, status: "evolving", summary: "私有正文改法", error: "私有错误",
      failure_review: { decision: "change_direction", reason: "私有复盘理由" } },
  ] }));
  const improvement = within(screen.getByRole("region", { name: "进化 · 第 1 轮" }));
  expect(improvement.getByRole("link")).toHaveAttribute("href", "/bench/experiments/exp/rounds/1?agent=text");
  expect(improvement.getByText("改版中")).toBeVisible();
  expect(improvement.queryByText("调整方向")).not.toBeInTheDocument();
  expect(screen.queryByText(/私有/)).not.toBeInTheDocument();
});

it("进化行展示复盘决策与真实状态，不显示机器格式的旧摘要", () => {
  open(experiment([sample("base")], { rounds: [
    { round: 1, status: "not_improved", summary: "executed_at: 2026-11-23\n# 旧改动" },
    { round: 2, status: "completed", outcome: "no_change", summary: "旧运行报告",
      failure_review: { decision: "stop", reason: "没有新的证据支持继续修改。" } },
  ] }));
  expect(screen.getByText("未达改进门槛")).toBeVisible();
  expect(screen.getByText("无改动")).toBeVisible();
  expect(screen.getByText("停止改进")).toBeVisible();
  expect(screen.getByText(/没有新的证据支持继续修改/)).toBeVisible();
  expect(screen.queryByText(/executed_at|旧改动|旧运行报告/)).not.toBeInTheDocument();
});

it("旧流程跨写作类型的回归报告仍有独立对比入口", () => {
  const report = { passed: false, baseline: { quality: 3 }, candidate: { quality: 2.5 }, quality_delta: -0.5 };
  open(experiment([sample("legacy", { phase: "regression", round: 1, task_type: "chapter_outlines" })], {
    flow_version: 2, rounds: [{ round: 1, status: "passed", regressions: { chapter_outlines: report } }],
  }));
  const link = screen.getByRole("link", { name: "查看对比详情" });
  expect(link.getAttribute("href")).toContain("task=chapter_outlines");
  expect(screen.getByText("未达标")).toBeVisible();
});
