import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import ExperimentRecordDetail from "./ExperimentRecordDetail";
import { getActiveRuns, getExperiment, getTraceEvents, type ActiveRun, type Experiment } from "@/lib/api";
import type { ExperimentSample } from "@/lib/experiment-types";
import { useTracePolling } from "@/hooks/useTraceStream";

vi.mock("@/hooks/useTraceStream", () => ({ useTracePolling: vi.fn() }));
vi.mock("@/lib/api", () => ({ getActiveRuns: vi.fn(), getExperiment: vi.fn(), getExperimentPackage: vi.fn(), getTraceEvents: vi.fn(), getTraceContext: vi.fn(),
  getTraceIntegrity: vi.fn(), resolveTrace: vi.fn(), stopActiveSession: vi.fn() }));

const sample: ExperimentSample = { id: "sample-1", package_id: "package-1", task_id: "task-1", task_type: "chapter_text",
  target: { task_type: "chapter_text", chapter: 1, volume_ids: [1] }, round: 0, arm: "baseline",
  phase: "baseline", repeat: 1, attempt: 1, package_title: "港口夜雨", status: "completed", completed: true,
  checks: {}, error: null, usage: {}, input_revision: "input", source_commit: "base",
  artifacts: { "正文/第001章.md": "她把船票叠成四折。", "正文/第002章.md": "另一章的正文" },
  trace_ids: ["writing-1"], evaluation_trace_ids: ["judge-a", "judge-b"],
  scores: { overall: 3.5, dimensions: { 因果: 3.5 }, reasons: { 因果: [
    { judge_id: 1, score: 4, 达标: ["动作推动转折"], 不足: ["结尾太急"] },
    { judge_id: 2, score: 3, 达标: ["伏笔有回应"], 不足: ["动机欠充分"] },
  ] } } };
const experiment = { id: "run", status: "exhausted", name: "码头实验", can_read_content: true, samples: [sample],
  models: { judges: [{ id: 1, name: "第一评委", model: "model-a" }, { id: 2, name: "第二评委", model: "model-b" }] } } as unknown as Experiment;
function Source() { const location = useLocation(); return <p>来源地址 {location.search}</p>; }
function open(section: string, fromRecords = false, id = "sample-1") {
  const search = "?agent=text&filter=mine";
  const origin = "/bench/experiments/run" + search;
  const dest = { pathname: "/bench/experiments/run/records/" + id + "/" + section, search,
    state: fromRecords ? { fromRecords: true, sampleId: id } : null };
  return render(<MemoryRouter initialEntries={fromRecords ? [origin, dest] : [dest]}><Routes>
    <Route path="/bench/experiments/:experimentId/records/:sampleId/:section" element={<ExperimentRecordDetail />} />
    <Route path="/bench/experiments/:experimentId" element={<Source />} /></Routes></MemoryRouter>);
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getExperiment).mockResolvedValue(experiment);
  vi.mocked(getActiveRuns).mockResolvedValue([]);
  vi.mocked(useTracePolling).mockReturnValue({
    detail: { run: { trace_id: "writing-1", workspace_id: "ws", thread_id: "t", session_name: "任务",
      workspace_path: "", endpoint: "", status: "completed", started_at: "2026-11-19", event_count: 2, path: "" },
    nodes: [{ node_id: "tool-review", label: "审稿", kind: "tool", tool_name: "task", depth: 0,
      status: "completed", raw_event_ids: ["start-review", "end-review"] }], todos: [], metrics: {
        input_tokens: 3, output_tokens: 4, total_tokens: 7, average_duration_ms: 1,
        input_partial: false, output_partial: false, total_partial: false } },
    refresh: vi.fn(), refreshing: false, availability: "available", loading: false, isLive: false,
    isStale: false, lastSyncedAt: null, activeSession: null, error: null,
  } as ReturnType<typeof useTracePolling>);
});

it("深链按实验样本读取正文并展开真实工具参数和审稿结果", async () => {
  vi.mocked(getTraceEvents).mockResolvedValue([
    { event_id: "start-review", type: "tool_start", tool_args: { subagent_type: "review", description: "核对船票线索" } },
    { event_id: "end-review", type: "tool_end", tool_output: "建议让船票折痕呼应上一章。" },
  ] as Awaited<ReturnType<typeof getTraceEvents>>);
  open("writing");
  expect(await screen.findByText("她把船票叠成四折。")).toBeVisible();
  expect(screen.queryByText("另一章的正文")).not.toBeInTheDocument();
  expect(getExperiment).toHaveBeenCalledWith("run");
  fireEvent.click(screen.getByRole("button", { name: /task/i }));
  expect(await screen.findByText(/核对船票线索/, {}, { timeout: 3000 })).toBeVisible();
  expect(screen.getByText("建议让船票折痕呼应上一章。")).toBeVisible();
  expect(getTraceEvents).toHaveBeenCalledWith("writing-1", ["start-review", "end-review"]);
  expect(screen.getByRole("link", { name: /返回运行记录/ })).toHaveAttribute("href",
    "/bench/experiments/run?agent=text&filter=mine&anchor=record-writing-sample-1&sample=sample-1&section=writing");
});

it("保留每名评委的分数、评语和所有评分 Trace，返回来源历史", async () => {
  open("evaluation", true);
  expect(await screen.findByText("3.50 / 5")).toBeVisible();
  for (const reason of ["动作推动转折", "结尾太急", "伏笔有回应", "动机欠充分"])
    expect(screen.getByText(reason)).toBeVisible();
  expect(screen.getByText(/第一评委.*4.000/)).toBeVisible();
  expect(screen.getByText(/第二评委.*3.000/)).toBeVisible();
  expect(useTracePolling).toHaveBeenCalledWith("judge-b");
  fireEvent.change(screen.getByRole("combobox", { name: "选择 Trace" }), { target: { value: "judge-a" } });
  expect(useTracePolling).toHaveBeenCalledWith("judge-a");
  fireEvent.click(screen.getByRole("button", { name: /返回运行记录/ }));
  expect(await screen.findByText("来源地址 ?agent=text&filter=mine")).toBeVisible();
});

it("评分用尽重试后同时保留已通过的评语和最终失败原因", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, samples: [{ ...sample,
    status: "failed", scores: null, error: "第 3 次尝试后仍有评分失败。",
    score_progress: { completed: 1, total: 2, reasons: { 因果: [
      { judge_id: 1, repeat: 1, score: 4, reason: "动机清楚", evidence: ["她把船票叠成四折。"] },
      { judge_id: 2, repeat: 1, score: null, error: "评分缺少正文依据" },
    ] } },
  }] });
  open("evaluation");
  expect(await screen.findByRole("alert")).toHaveTextContent("第 3 次尝试后仍有评分失败。");
  expect(screen.getByText(/已完成 1 \/ 2 份评分.*尚无汇总评分/)).toBeVisible();
  expect(screen.getByText(/第一评委.*4.000/)).toBeVisible();
  expect(screen.getByText("动机清楚")).toBeVisible();
  expect(screen.getByText(/第二评委.*评分失败/)).toBeVisible();
  expect(screen.getByText("评分缺少正文依据")).toBeVisible();
});

it("等待自动重试评分时显示进度和已有评分，沿用评语读取权限", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, status: "running", can_read_content: false,
    samples: [{ ...sample, status: "evaluation_error", scores: null, score_retry_at: 1,
      error: "上次失败原文", score_progress: { completed: 1, total: 2, reasons: {
        因果: [{ judge_id: 1, score: 4, reason: "受限的评语", evidence: [] }],
      } },
    }],
  });
  open("evaluation");
  expect(await screen.findByText("等待自动重试评分。")).toBeVisible();
  expect(screen.getByText(/已完成 1 \/ 2 份评分/)).toBeVisible();
  expect(screen.queryByText("受限的评语")).not.toBeInTheDocument();
  expect(screen.queryByText("上次失败原文")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("评分 Trace 可以读取评委实际调用的消息数组和原始回复", async () => {
  const snapshot = vi.mocked(useTracePolling).getMockImplementation()!("judge-b");
  vi.mocked(useTracePolling).mockReturnValue({ ...snapshot, detail: { ...snapshot.detail!,
    nodes: [{ node_id: "llm", label: "因果", kind: "llm", model_name: "judge-model", depth: 0,
      status: "completed", raw_event_ids: ["judge-start", "judge-end"] }] } });
  vi.mocked(getTraceEvents).mockResolvedValue([
    { event_id: "judge-start", type: "llm_start", input: [{ role: "user", content: "请评价稿件中的因果关系。" }] },
    { event_id: "judge-end", type: "llm_end", output: '{"score":4,"达标":["动机清楚"]}' },
  ] as Awaited<ReturnType<typeof getTraceEvents>>);
  open("evaluation");
  fireEvent.click(await screen.findByRole("button", { name: /judge-model/ }));
  await waitFor(() => expect(getTraceEvents).toHaveBeenCalledWith("judge-b", ["judge-start", "judge-end"]));
  fireEvent.click(screen.getByRole("button", { name: "查看全部上下文" }));
  expect(await screen.findByText("请评价稿件中的因果关系。")).toBeVisible();
  expect(screen.getByText(/"score":4/)).toBeVisible();
});

it("无正文权限只显示分数，不拉取 Trace、评语和错误原文", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, can_read_content: false,
    samples: [{ ...experiment.samples[0], error: "错误含正文片段" }] });
  open("evaluation");
  expect(await screen.findByText("3.50 / 5")).toBeVisible();
  expect(screen.getByText("因果")).toBeVisible();
  expect(screen.queryByText("结尾太急")).not.toBeInTheDocument();
  expect(screen.queryByText("错误含正文片段")).not.toBeInTheDocument();
  expect(useTracePolling).not.toHaveBeenCalled();
});

it.each([true, false])("评分详情核对运行数据并保护 Skill 证据，正文权限 %s", async (canRead) => {
  const scored = { ...sample, scores: { overall: 4, quality: 4, dimensions: { 因果: 4, 运行时: 4 },
    weights: { quality: .7, runtime: .3, tools: .1, tokens: .1, skill: .1 }, runtime_scoring_version: 1,
    runtime: { score: 4, tools: { successes: 8, total: 10, rate: .8, score: 4.2 },
      tokens: { value: 72000, baseline: 80000, saving: .1, score: 4 }, skill: { score: 3.8 } },
    reasons: { 运行时: [{ judge_id: 1, score: 4, reason: "Skill 确实用于修订", evidence: ["writing-1:45"],
      evidence_details: [{ ref: "writing-1:45", text: "受限证据摘要" }] }] } } };
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, can_read_content: canRead, runtime_scoring_version: 1,
    scoring_rule: { id: "rule-2", name: "正文规则", version: 2, dimension_weights: { 因果: 2 } }, samples: [scored] });
  open("evaluation");
  expect(await screen.findByText("工具调用成功率")).toBeVisible();
  expect(screen.getByText(/8 \/ 10/)).toBeVisible();
  expect(screen.getByText(/72,000/)).toBeVisible();
  expect(screen.getByText(/80,000/)).toBeVisible();
  expect(screen.getByText(/正文规则.*v2/)).toBeVisible();
  const runtime = within(screen.getByLabelText("运行时评分依据"));
  for (const [label, score] of [
    ["工具分", "4.200 / 5"], ["Token 分", "4.000 / 5"], ["Skill 分", "3.800 / 5"], ["运行时综合分", "4.000 / 5"],
  ]) {
    expect(runtime.getByText(label).nextElementSibling).toHaveTextContent(score);
  }
  expect(screen.queryByText("运行时")).not.toBeInTheDocument();
  if (canRead) {
    expect(screen.getByRole("heading", { name: /^Skill 评委评分/ })).toHaveTextContent("3.800 / 5");
    expect(screen.getByText("Skill 确实用于修订")).toBeVisible();
    expect(screen.getByText(/writing-1:45/)).toBeVisible();
    expect(screen.getByText(/受限证据摘要/)).toBeVisible();
  } else {
    expect(screen.queryByText("Skill 确实用于修订")).not.toBeInTheDocument();
    expect(screen.queryByText(/writing-1:45/)).not.toBeInTheDocument();
    expect(screen.queryByText(/受限证据摘要/)).not.toBeInTheDocument();
  }
});

it("缺原版用量时保留候选 Token 与已评结果，不显示汇总分", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, can_read_content: false, runtime_scoring_version: 1,
    samples: [{ ...sample, scores: null, status: "failed", runtime_metrics: { tokens: { value: 72000 } },
      score_progress: { completed: 0, total: 1, reasons: { 运行时: [{ judge_id: 1, score: 4 }] } } }] });
  open("evaluation");
  expect(await screen.findByText("72,000")).toBeVisible();
  expect(screen.getByText("同题同次原版 Token").nextElementSibling).toHaveTextContent("未知");
  expect(screen.getByRole("heading", { name: "Skill 评委评分" })).toBeVisible();
  expect(screen.getByText(/第一评委 · 4\.000/)).toBeVisible();
  expect(screen.getByText(/尚无汇总评分/)).toBeVisible();
  expect(screen.queryByText("Token 分")).not.toBeInTheDocument();
  expect(screen.queryByText("运行时综合分")).not.toBeInTheDocument();
});

it("读未归档的本样本评分 Trace，始终显示评测模型", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, status: "running",
    samples: [{ ...experiment.samples[0], status: "scoring", scores: null, evaluation_trace_ids: ["archived"] }] });
  const run: ActiveRun = { trace_id: "incoming", run_purpose: "evolution_eval", experiment_id: "run",
    external_refs: { sample_id: "sample-1", experiment_id: "run" }, workspace_id: "ws", thread_id: null,
    endpoint: null, status: "running", started_at: null, duration_ms: null, event_count: 0, session_name: null,
    ingested: true, workload: "evaluation", integrity_status: "pending", coverage: {},
    skill_activation_count: 0, middleware_intervention_count: 0, hitl_count: 0 };
  vi.mocked(getActiveRuns).mockResolvedValue([run, { ...run, trace_id: "archived" },
    { ...run, trace_id: "other-writing", run_purpose: "optimization" },
    { ...run, trace_id: "foreign", experiment_id: "foreign-run" },
    { ...run, trace_id: "foreign-sample", external_refs: { sample_id: "other" } },
    { ...run, trace_id: "conflict", external_refs: { ...run.external_refs, experiment_id: "other" } },
    { ...run, trace_id: "wrong-stage", display_context: { operation: "improvement" } }]);
  open("evaluation");
  const chooser = await screen.findByRole("combobox", { name: "选择 Trace" });
  expect(getActiveRuns).toHaveBeenCalledWith("experiment");
  expect(screen.getByText("model-a")).toBeVisible();
  expect(screen.getByText("model-b")).toBeVisible();
  expect(Array.from((chooser as HTMLSelectElement).options, option => option.value)).toEqual(["archived", "incoming"]);
  expect(chooser).toHaveValue("incoming");
  expect(useTracePolling).toHaveBeenCalledWith("incoming");
});

it("失败可重试，其他档案的样本编号不可打开", async () => {
  vi.mocked(getExperiment).mockRejectedValueOnce(new Error("网络暂不可用"));
  open("writing", false, "foreign");
  expect(await screen.findByText("网络暂不可用")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  await waitFor(() => expect(screen.getByText("本实验中没有这条样本记录。")).toBeVisible());
  expect(useTracePolling).not.toHaveBeenCalled();
});

it("运行已经开始但 Trace 尚未同步时可进入详情，归档更新后自动展示", async () => {
  vi.useFakeTimers();
  try {
    const running = { ...sample, completed: false, status: "running", trace_ids: [] };
    vi.mocked(getExperiment)
      .mockResolvedValueOnce({ ...experiment, status: "running", samples: [running] })
      .mockResolvedValue({ ...experiment, samples: [{ ...running, trace_ids: ["later"] }] });
    open("writing");
    await act(async () => {});
    expect(screen.getByText("等待 Trace 同步…")).toBeVisible();
    expect(useTracePolling).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(useTracePolling).toHaveBeenCalledWith("later");
  } finally {
    vi.useRealTimers();
  }
});

it("历史运行缺少 Trace 时仍能查阅对应的非正文产物", async () => {
  vi.mocked(getExperiment).mockResolvedValue({ ...experiment, samples: [{ ...sample, task_type: "master_outline",
    trace_ids: [], artifacts: { "总纲.md": "小说从渡口停航开始。" }, checks: { complete: true } }] });
  open("writing");
  expect(await screen.findByText("小说从渡口停航开始。")).toBeVisible();
  expect(screen.getByText("尚无关联 Trace。")).toBeVisible();
  expect(screen.getByText("完成检查")).toBeVisible();
});
