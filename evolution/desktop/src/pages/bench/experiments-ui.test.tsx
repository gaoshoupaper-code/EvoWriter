import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StagePackages } from "@/components/bench/StagePackages";
import { StageRules } from "@/components/bench/StageRules";
import { EvolutionExperiments } from "@/components/bench/EvolutionExperiments";
import ExperimentDetail from "@/pages/bench/ExperimentDetail";
import * as apiModule from "@/lib/api";

vi.mock("@/lib/api", () => ({
  listExperimentPackages: vi.fn(), importExperimentPackage: vi.fn(),
  getExperimentPackageTemplates: vi.fn(), getExperimentPackage: vi.fn(),
  listExperimentRules: vi.fn(), saveExperimentRule: vi.fn(),
  getExperimentOptions: vi.fn(), listExperiments: vi.fn(), startExperiment: vi.fn(),
  getExperiment: vi.fn(), stopExperiment: vi.fn(), publishExperiment: vi.fn(), restoreExperiment: vi.fn(),
}));
vi.mock("@/lib/download", () => ({ downloadTextFile: vi.fn() }));

const api = vi.mocked(apiModule);
const kinds = ["volume_outlines", "chapter_outlines", "chapter_text"] as const;
const labels = ["整本卷纲", "单章章纲", "单章正文"];
const rules = kinds.map((task_type, i) => ({
  id: `rule-${i}`, task_type, profile: "stage-eval-v2", version: 1, name: `${labels[i]}规则`,
  dimensions: [{ key: "需求兑现", question: "是否兑现需求？", weight: 1,
    anchors: { "1": "严重缺失", "2": "多处缺失", "3": "基本完成", "4": "完整落实", "5": "充分落实" } }],
  discipline_rules: ["引用产物证据"], created_at: "2026-11-06T01:00:00Z",
}));
const pkg = (id: string, title: string, task_type: string, purpose: string) => ({
  id, title, task_type, purpose, suite: "stage-smoke-v1", target: { task_type, volume_ids: [1], ...(task_type !== "volume_outlines" ? { chapter: 1 } : {}) },
  input_revision: "fixed-input", created_at: "2026-11-06T01:00:00Z",
});
const packages = [
  pkg("opt-1", "优化题一", kinds[0], "optimization"),
  pkg("opt-2", "优化题二", kinds[0], "optimization"),
  pkg("val-1", "终验题一", kinds[0], "validation"),
  pkg("reg-1", "章纲回归", kinds[1], "regression"),
  pkg("reg-2", "正文回归", kinds[2], "regression"),
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
  vi.clearAllMocks();
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

describe("进化端实验交互 / AC-001、AC-012、AC-020、AC-022", () => {
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

  it("高级设置可改门槛，自动匹配各用途样本并冻结三类规则", async () => {
    api.startExperiment.mockResolvedValue(experiment({ status: "pending" }) as never);
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("Harness 版本"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("执行模型"), { target: { value: "11" } });
    fireEvent.change(screen.getByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByText("高级设置"));
    fireEvent.change(screen.getByLabelText("完成率下限（%）"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("主攻最低涨幅"), { target: { value: "0.2" } });
    fireEvent.change(screen.getByLabelText("单章章纲退化容忍"), { target: { value: "0.1" } });
    fireEvent.change(screen.getByLabelText("单章正文退化容忍"), { target: { value: "0.1" } });
    fireEvent.change(screen.getByLabelText("最大迭代轮数"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      task_type: kinds[0], baseline_version: 6, model_config_id: 11, judge_group_id: 5,
      rule_versions: { volume_outlines: "rule-0", chapter_outlines: "rule-1", chapter_text: "rule-2" },
      optimization_ids: ["opt-1", "opt-2"], validation_ids: ["val-1"],
      regression_ids: { chapter_outlines: ["reg-1"], chapter_text: ["reg-2"] },
      thresholds: { completion_rate: 0.9, quality_gain: 0.2, regression_tolerance: { chapter_outlines: 0.1, chapter_text: 0.1 } },
      max_rounds: 3,
    })));
  });

  it("过期候选展示原因并禁用发布", async () => {
    api.getExperiment.mockResolvedValue(experiment({ status: "stale", can_publish: false, stale_reason: "生产版本已变化" }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    expect(await screen.findByText("生产版本已变化")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准并发布候选" })).toBeDisabled();
    expect(api.publishExperiment).not.toHaveBeenCalled();
  });

  it("详情展示冻结双模型，进度条使用当前环节的任务总数", async () => {
    api.getExperiment.mockResolvedValue(experiment({ flow_version: 2, status: "running", phase: "regression",
      models: { executor: { id: 11, name: "冻结执行配置", model: "fixed-writer" },
        judges: [{ id: 12, name: "冻结评测配置", model: "fixed-judge" }], evolver: null },
      progress: { total: 20, done: 14, completed: 14,
        phase: { name: "regression", round: 1, total: 6, done: 2, completed: 2, active: 1 } },
    }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    expect(await screen.findByText("fixed-writer")).toBeInTheDocument();
    expect(screen.getByText("fixed-judge")).toBeInTheDocument();
    const progress = screen.getByRole("progressbar", { name: "当前环节进度" });
    expect(progress).toHaveAttribute("max", "6");
    expect(progress).toHaveAttribute("value", "2");
  });

  it.each([
    ["chapter:25", "生成第25章章纲"],
    ["text:1", "生成第1章正文"],
    ["volume:2", "生成第2卷卷纲"],
    ["theme:", "生成主题"],
    ["custom:4", "执行阶段：custom:4"],
  ])("执行阶段 %s 在当前任务和逐题表中均显示为中文", async (executionPhase, expected) => {
    api.getExperiment.mockResolvedValue(experiment({ flow_version: 2, status: "running", phase: "baseline", samples: [{
      id: "sample-active", task_id: "task-active", package_id: "package-active", package_title: "运行样本",
      task_type: "volume_outlines", phase: "baseline", arm: "baseline", round: 0, repeat: 1, attempt: 1,
      status: "running", execution_phase: executionPhase, completed: false, trace_ids: [],
      checks: {}, scores: null, error: null, artifacts: {}, usage: {}, input_revision: "fixed-input", source_commit: "baseline123",
    }] }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    expect(await screen.findAllByText(expected)).toHaveLength(2);
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
    api.getExperiment.mockResolvedValue(experiment() as never);
    api.publishExperiment.mockResolvedValue(experiment({ status: "publish_failed", can_publish: false,
      publish_status: "unconfirmed", load_error: "执行端尚未确认加载", loaded_revision: null }) as never);
    render(<MemoryRouter initialEntries={["/bench/experiments/experiment-1"]}><Routes>
      <Route path="/bench/experiments/:experimentId" element={<ExperimentDetail />} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "批准并发布候选" }));
    await waitFor(() => expect(api.publishExperiment).toHaveBeenCalledWith("experiment-1"));
    expect(await screen.findByText("执行端尚未确认加载")).toBeInTheDocument();
    expect(screen.queryByText("已生效")).not.toBeInTheDocument();
  });
});
