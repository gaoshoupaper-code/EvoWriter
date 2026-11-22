import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StagePackages } from "@/components/bench/StagePackages";
import { StageRules } from "@/components/bench/StageRules";
import { EvolutionExperiments } from "@/components/bench/EvolutionExperiments";
import ExperimentDetail from "@/pages/bench/ExperimentDetail";
import ExperimentReview from "@/pages/bench/ExperimentReview";
import * as apiModule from "@/lib/api";
import { downloadTextFile } from "@/lib/download";

vi.mock("@/lib/api", () => ({
  listExperimentPackages: vi.fn(), importExperimentPackage: vi.fn(),
  getExperimentPackageTemplates: vi.fn(), getExperimentPackage: vi.fn(),
  listExperimentRules: vi.fn(), saveExperimentRule: vi.fn(),
  getExperimentOptions: vi.fn(), listExperiments: vi.fn(), startExperiment: vi.fn(),
  getExperiment: vi.fn(), getActiveRuns: vi.fn(), stopExperiment: vi.fn(), publishExperiment: vi.fn(), restoreExperiment: vi.fn(),
}));
vi.mock("@/lib/download", () => ({ downloadTextFile: vi.fn() }));

const api = vi.mocked(apiModule);
const kinds = ["volume_outlines", "chapter_outlines", "chapter_text"] as const;
const labels = ["整本卷纲", "单章章纲", "单章正文"];
const rules = kinds.map((task_type, i) => ({
  id: `rule-${i}`, task_type, profile: "isolated-writing-v1", version: 1, name: `${labels[i]}规则`,
  dimensions: [{ key: "需求兑现", question: "是否兑现需求？", weight: 1,
    anchors: { "1": "严重缺失", "2": "多处缺失", "3": "基本完成", "4": "完整落实", "5": "充分落实" } }],
  discipline_rules: ["引用产物证据"], created_at: "2026-11-06T01:00:00Z",
}));
const pkg = (id: string, title: string, task_type: string, purpose: string) => ({
  id, title, task_type, purpose, suite: "isolated-writing-v1", target: { task_type, volume_ids: [1], ...(task_type !== "volume_outlines" ? { chapter: 1 } : {}) },
  input_revision: "fixed-input", created_at: "2026-11-06T01:00:00Z",
});
const packages = [
  pkg("opt-1", "优化题一", kinds[0], "optimization"),
  pkg("opt-2", "优化题二", kinds[0], "optimization"),
  pkg("val-1", "终验题一", kinds[0], "validation"),
  pkg("reg-1", "卷纲回归1", kinds[0], "regression"),
  pkg("reg-2", "卷纲回归2", kinds[0], "regression"),
];
const experiment = (overrides: Record<string, unknown> = {}) => ({
  id: "experiment-1", name: "卷纲优化", task_type: kinds[0], status: "awaiting_review", phase: "review",
  current_round: 1, max_rounds: 3, end_reason: null, settings: {},
  baseline: { version: 6, commit: "baseline123" },
  best_candidate: { commit: "candidate123", digest: "digest1", round: 1, quality: 4, completion_rate: 1 },
  rounds: [], samples: [], report: {}, events: [], usage: {}, can_read_content: true,
  can_publish: true, stale_reason: null, publish_status: "not_started", loaded_revision: null,
  load_error: null, rollback_status: null, created_at: "2026-11-06T01:00:00Z", updated_at: "2026-11-06T01:00:00Z",
  ...overrides,
});

beforeEach(() => {
  window.location.hash = "#/agents/volume/bench";
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  api.getActiveRuns.mockResolvedValue([]);
  api.listExperimentPackages.mockResolvedValue({ items: packages as never[], can_read_content: true });
  api.getExperimentPackageTemplates.mockResolvedValue({ items: [] });
  api.listExperimentRules.mockResolvedValue({ items: rules as never[] });
  api.listExperiments.mockResolvedValue({ items: [] });
  api.getExperimentOptions.mockResolvedValue({
    task_types: [...kinds], versions: [{ version: 6, commit: "baseline123", note: "当前生产" }], production_version: 6,
    models: [{ id: 11, name: "创作模型", model: "writer-model", base_url: "", is_active: true }],
    judge_groups: [{ id: 5, name: "异源评委", members: [{ config_id: 12, name: "评测模型", model: "judge-model" }] }],
  } as never);
});
afterEach(() => vi.mocked(window.scrollTo).mockRestore());

describe("进化端实验交互 / AC-001、AC-012、AC-020、AC-022", () => {
  it("从旧的手工候选链接启动正文实验时只提交自动进化配置", async () => {
    window.location.hash = "#/agents/text/bench";
    api.listExperimentPackages.mockResolvedValue({ items: [pkg("text-opt", "改进题", kinds[2], "optimization"),
      pkg("text-reg", "回归题", kinds[2], "regression")] as never[], can_read_content: true });
    api.startExperiment.mockResolvedValue(experiment() as never);
    render(<MemoryRouter initialEntries={["/bench?candidate=old-candidate&base=old-base"]}><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledOnce());
    expect(api.startExperiment.mock.calls[0][0]).not.toHaveProperty("candidate_commit");
    expect(screen.queryByText(/指定候选/)).not.toBeInTheDocument();
  });
  it("导入JSON资料包并展示固定输入版本", async () => {
    const payload = {
      title: "新卷纲题", purpose: "optimization", target: { task_type: kinds[0], volume_ids: [1, 2] },
      input: { demand_md: "完整小说需求", confirmation_rules: "普通取舍自行决定", files: {}, skills: [] },
    };
    api.importExperimentPackage.mockResolvedValue(pkg("new-1", "新卷纲题", kinds[0], "optimization") as never);
    render(<StagePackages />);
    const file = new File([JSON.stringify(payload)], "sample.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => JSON.stringify(payload) });
    fireEvent.change(screen.getByLabelText("选择资料包"), { target: { files: [file] } });
    await waitFor(() => expect(api.importExperimentPackage).toHaveBeenCalledWith(payload));
    expect((await screen.findAllByText("fixed-input")).length).toBeGreaterThan(0);
  });

  it("坏JSON只显示错误，不提交导入", async () => {
    render(<StagePackages />);
    const file = new File(["{"], "broken.json");
    Object.defineProperty(file, "text", { value: async () => "{" });
    fireEvent.change(screen.getByLabelText("选择资料包"), { target: { files: [file] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("JSON");
    expect(api.importExperimentPackage).not.toHaveBeenCalled();
  });

  it("编辑规则另存新版本，保留评分维度与档位", async () => {
    api.saveExperimentRule.mockResolvedValue({ ...rules[0], id: "rule-new", version: 2 } as never);
    render(<StageRules />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑规则" }));
    fireEvent.change(await screen.findByLabelText("需求兑现评分问题"), { target: { value: "是否落实全部明确要求？" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(api.saveExperimentRule).toHaveBeenCalledWith(expect.objectContaining({
      task_type: kinds[0], dimensions: [expect.objectContaining({ key: "需求兑现", question: "是否落实全部明确要求？" })],
    })));
  });

  it("在各维度档位前展示并编辑多行关注点", async () => {
    window.location.hash = "#/agents/text/bench";
    const original = "对照章纲要求。\n核对正文证据。";
    const updated = "逐项核对必须兑现的事件。\n引用对应的正文原句。";
    const dimension = rules[2].dimensions[0];
    const rule = { ...rules[2], profile: "chapter-text-outline-v3" as const,
      dimensions: [{ ...dimension, focus_points: original }, { ...dimension, key: "情节逻辑" }] };
    api.listExperimentRules.mockResolvedValue({ items: [rule] });
    api.saveExperimentRule.mockImplementation(async (payload) => ({ ...rule, ...payload, id: "rule-new", version: 2 }));
    render(<StageRules />);

    const focus = await screen.findByRole("heading", { name: "评分关注点" });
    expect(focus.nextElementSibling?.textContent).toBe(original);
    const firstAnchors = screen.getAllByText("查看 1–5 分判定标准")[0];
    expect(focus.compareDocumentPosition(firstAnchors) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "编辑规则" }));
    const field = screen.getByLabelText("需求兑现评分关注点");
    expect(field).toHaveValue(original);
    expect(screen.getByLabelText("情节逻辑评分关注点")).toHaveValue("");
    fireEvent.change(field, { target: { value: updated } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(api.saveExperimentRule).toHaveBeenCalledWith(expect.objectContaining({
      dimensions: [{ ...dimension, focus_points: updated }, { ...dimension, key: "情节逻辑" }],
    })));
    expect((await screen.findByRole("heading", { name: "评分关注点" })).nextElementSibling?.textContent).toBe(updated);
  });

  it("本套两个题组全部运行，并冻结本套规则和门槛", async () => {
    api.startExperiment.mockResolvedValue(experiment({ status: "pending" }) as never);
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("Harness 版本"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("执行模型"), { target: { value: "11" } });
    fireEvent.change(screen.getByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByText("高级设置"));
    expect(screen.queryByLabelText("完成率下限（%）")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      task_type: kinds[0], baseline_version: 6, model_config_id: 11, judge_group_id: 5,
      rule_versions: { volume_outlines: "rule-0" },
      optimization_ids: ["opt-1", "opt-2"],
      regression_ids: { volume_outlines: ["reg-1", "reg-2"] },
      thresholds: { completion_rate: 1, quality_gain: 0.1, regression_tolerance: { volume_outlines: 0 } },
      max_rounds: 3,
    })));
  });

  it("达标候选过期后可在审核页查看原因，无法发版", async () => {
    api.getExperiment.mockResolvedValue(experiment({ status: "stale", report: { passed: true },
      can_publish: false, stale_reason: "生产版本已变化" }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
      <Route path="/bench/experiments/:experimentId/review" element={<ExperimentReview />} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("link", { name: "进入审核" }));
    expect(await screen.findByText("生产版本已变化")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准并发布候选" })).not.toBeInTheDocument();
    expect(api.publishExperiment).not.toHaveBeenCalled();
  });

  it("沿用报告导出入口保存同题同次的运行计分及证据", async () => {
    const scoring_rule = { id: "rule-2", name: "正文规则", version: 2,
      dimension_weights: { 事件因果逻辑: 1, 人物行为逻辑: 1, 章纲兑现: 2, 语言清晰度: 1 } };
    const scores = { overall: 4, quality: 4, dimensions: { 事件因果逻辑: 4, 人物行为逻辑: 4, 章纲兑现: 4, 语言清晰度: 4, 运行时: 4 },
      weights: { quality: .7, runtime: .3, tools: .1, tokens: .1, skill: .1 }, runtime_scoring_version: 1,
      runtime: { score: 4, tools: { successes: 8, total: 10, rate: .8, score: 4.2 },
        tokens: { value: 72000, baseline: 80000, saving: .1, score: 4 }, skill: { score: 3.8 } },
      reasons: { 运行时: [{ judge_id: 1, score: 4, reason: "Skill 用于修订", evidence: ["trace-1:45"] }] } };
    const sample = { id: "candidate-1", package_id: "same-case", input_revision: "same-input", repeat: 2,
      round: 1, arm: "candidate", phase: "regression", status: "completed", package_title: "回归题", trace_ids: [], scores };
    api.getExperiment.mockResolvedValue(experiment({ task_type: "chapter_text", runtime_scoring_version: 1, scoring_rule,
      samples: [sample] }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1?agent=text"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "导出报告" }));
    const [filename, content] = vi.mocked(downloadTextFile).mock.calls[0];
    expect(filename).toBe("experiment-1-report.json");
    expect(JSON.parse(content)).toMatchObject({ runtime_scoring_version: 1, scoring_rule, samples: [sample] });
  });

  it("保留冻结双模型和单条状态，去掉批次标题和重复计数", async () => {
    const samples = ["completed", "completed", "scoring", "running", "pending", "pending"].map((status, index) => ({
      id: "reg-" + index, task_id: "reg-" + index, package_id: "p", package_title: `回归样本 ${index} · 生成第一卷卷纲`,
      task_type: "volume_outlines", source_commit: "candidate123", input_revision: "fixed-input",
      phase: "regression", arm: "candidate", round: 1, repeat: index + 1, attempt: 1, status,
      completed: index < 3, trace_ids: [], scores: index < 2 ? { overall: 4, dimensions: {}, reasons: {} } : null,
      checks: {}, error: null, artifacts: {}, usage: {},
    }));
    api.getExperiment.mockResolvedValue(experiment({ flow_version: 2, status: "running", phase: "regression",
      models: { executor: { id: 11, name: "冻结执行配置", model: "fixed-writer" },
        judges: [{ id: 12, name: "冻结评测配置", model: "fixed-judge" }], evolver: null },
      can_publish: false, samples,
      progress: { total: 20, done: 14, completed: 14,
        phase: { name: "regression", round: 1, total: 6, done: 2, completed: 2, active: 1 } },
    }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    const overview = within(await screen.findByLabelText("实验概况"));
    expect(overview.getByText("fixed-writer")).toBeInTheDocument();
    expect(overview.getByText("fixed-judge")).toBeInTheDocument();
    const timeline = within(screen.getByRole("region", { name: "实验流程" }));
    const writing = within(timeline.getByRole("region", { name: /运行 · 第 \d+ 轮/ }));
    const evaluation = within(timeline.getByRole("region", { name: /评测 · 第 \d+ 轮/ }));
    expect(writing.getByRole("link", { name: "回归样本 0" })).toBeInTheDocument();
    expect(writing.getByText(/运行中|生成中/)).toBeInTheDocument();
    const scored = evaluation.getByRole("link", { name: /回归样本 0.*4\.00/ });
    expect(scored.closest("li")).toHaveTextContent("fixed-judge");
    expect(evaluation.getByText("评分中")).toBeInTheDocument();
    expect(evaluation.queryByRole("link", { name: /回归样本 2/ })).not.toBeInTheDocument();
    expect(timeline.queryByRole("heading", { name: "本套回归" })).not.toBeInTheDocument();
    expect(timeline.queryAllByText(/生成第一卷卷纲|开始时间未记录|已生成|已结束 \d/)).toHaveLength(0);
  });

  it.each([
    ["chapter:25", "生成第25章章纲"],
    ["text:1", "生成第1章正文"],
    ["volume:2", "生成第2卷卷纲"],
    ["theme:", "生成主题"],
    ["custom:4", "执行阶段：custom:4"],
  ])("执行阶段 %s 在运行行显示一次", async (executionPhase, expected) => {
    api.getExperiment.mockResolvedValue(experiment({ flow_version: 2, status: "running", phase: "baseline", samples: [{
      id: "sample-active", task_id: "task-active", package_id: "package-active", package_title: "运行样本",
      task_type: "volume_outlines", phase: "baseline", arm: "baseline", round: 0, repeat: 1, attempt: 1,
      status: "running", execution_phase: executionPhase, completed: false, trace_ids: [],
      checks: {}, scores: null, error: null, artifacts: {}, usage: {}, input_revision: "fixed-input", source_commit: "baseline123",
    }] }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    const writing = within(await screen.findByRole("region", { name: "运行 · 第 1 轮" }));
    expect(writing.getByText(expected, { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText(expected, { exact: false })).toHaveLength(1);
  });

  it.each(["failed", "exhausted", "stopped"])("%s 且无候选时详情不再显示改版中", async (status) => {
    api.getExperiment.mockResolvedValue(experiment({ flow_version: 2, status, phase: "evolving",
      current_candidate: null, best_candidate: null, can_publish: false,
    }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    const fact = (await screen.findByText("Harness 原版 → 当前候选")).closest("div")!;
    expect(fact).toHaveTextContent("未生成候选");
    expect(fact).not.toHaveTextContent("改版中");
  });

  it("发布后未确认加载，不展示已生效", async () => {
    api.getExperiment.mockResolvedValue(experiment({ report: { passed: true } }) as never);
    const unconfirmed = experiment({ status: "publish_failed", can_publish: false,
      phase: "publish", report: { passed: true }, publish_status: "unconfirmed",
      load_error: "执行端尚未确认加载", loaded_revision: null });
    api.publishExperiment.mockImplementation(async () => {
      api.getExperiment.mockResolvedValue(unconfirmed as never);
      return unconfirmed as never;
    });
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
      <Route path="/bench/experiments/:experimentId/review" element={<ExperimentReview />} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("link", { name: "进入审核" }));
    expect(api.publishExperiment).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "批准并发布候选" }));
    await waitFor(() => expect(api.publishExperiment).toHaveBeenCalledWith("experiment-1"));
    expect(await screen.findByText("执行端尚未确认加载")).toBeInTheDocument();
    expect(screen.queryByText("已生效")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /返回/ }));
    const entry = await screen.findByRole("link", { name: "查看发布状态" });
    await waitFor(() => expect(entry).toHaveFocus());
  });
});
