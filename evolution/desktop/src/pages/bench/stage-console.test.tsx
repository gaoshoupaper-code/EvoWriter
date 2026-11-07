import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { EvolutionExperiments } from "@/components/bench/EvolutionExperiments";
import { StagePackages } from "@/components/bench/StagePackages";
import { StageRules } from "@/components/bench/StageRules";
import * as apiModule from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getExperimentOptions: vi.fn(), listExperimentPackages: vi.fn(), listExperimentRules: vi.fn(),
  listExperiments: vi.fn(), startExperiment: vi.fn(), saveExperimentRule: vi.fn(),
  getExperimentPackage: vi.fn(), getExperimentPackageTemplates: vi.fn(), importExperimentPackage: vi.fn(),
}));
vi.mock("@/lib/download", () => ({ downloadTextFile: vi.fn() }));

const api = vi.mocked(apiModule);
const kinds = ["volume_outlines", "chapter_outlines", "chapter_text"] as const;
const rules = kinds.map((task_type, index) => ({
  id: `rule-${index}`, task_type, profile: "stage-eval-v2", name: `${task_type}新规则`, version: 2,
  dimensions: [{ key: "需求兑现", question: "当前阶段是否兑现固定要求？", weight: 1,
    anchors: { "1": "目标缺失", "2": "两处实质缺陷", "3": "一处实质缺陷", "4": "仅局部不足", "5": "全部落实" } }],
  discipline_rules: ["引用产物证据"], created_at: "2026-11-07T01:00:00Z",
}));
const packages = kinds.flatMap((task_type, index) => ["optimization", "optimization", "validation", "regression"].map((purpose, n) => ({
  id: `${task_type}-${n}`, title: `${task_type}样本${n}`, task_type, purpose, suite: "stage-smoke-v1",
  target: { task_type, volume_ids: task_type === "volume_outlines" ? [1, 2, 3] : [1],
    ...(task_type !== "volume_outlines" ? { chapter: n + 1 } : { chapters_per_volume: 20 }) },
  input_revision: `revision-${index}-${n}`, created_at: "2026-11-07T01:00:00Z",
})));
const metrics = (quality: number) => ({ total: 2, completed: 2, completion_rate: 1, quality, evaluation_errors: 0 });
const report = { passed: true, baseline: metrics(3.6), candidate: metrics(4), quality_delta: 0.4,
  paired_count: 2, changes: [], new_failures: [], problems: [] };

beforeEach(() => {
  vi.clearAllMocks();
  api.listExperimentPackages.mockResolvedValue({ items: packages as never[], can_read_content: true });
  api.listExperimentRules.mockResolvedValue({ items: [{ ...rules[1], id: "old-rule", profile: undefined, version: 99 }, ...rules] as never[] });
  api.listExperiments.mockResolvedValue({ items: [] });
  api.getExperimentPackageTemplates.mockResolvedValue({ items: [] });
  api.getExperimentOptions.mockResolvedValue({
    task_types: [...kinds], versions: [{ version: 18, commit: "fixed-baseline", note: "当前生产" }], production_version: 18,
    models: [{ id: 11, name: "执行配置", model: "writer-frozen", is_active: true }],
    judge_groups: [{ id: 5, name: "异源评委", members: [{ config_id: 12, name: "评测配置", model: "judge-frozen" }] }],
    defaults: { max_rounds: 3, rule_versions: Object.fromEntries(kinds.map((kind, n) => [kind, `rule-${n}`])),
      thresholds: { completion_rate: 1, quality_gain: 0.1, regression_tolerance: Object.fromEntries(kinds.map((kind) => [kind, 0])) } },
    builtin_suite: { id: "stage-smoke-v1", total: 12 },
  } as never);
  api.startExperiment.mockResolvedValue({ id: "new-experiment" } as never);
});

describe("三阶段实验台 / REQ-20261107-172211", () => {
  it("仅四项常用选择，自动带入套件、规则与默认门槛", async () => {
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    await screen.findByLabelText("主攻阶段");
    expect(screen.getAllByRole("combobox")).toHaveLength(4);
    expect(screen.queryByRole("textbox", { name: "实验名称" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      task_type: "volume_outlines", baseline_version: 18, model_config_id: 11, judge_group_id: 5,
      optimization_ids: ["volume_outlines-0", "volume_outlines-1"], validation_ids: ["volume_outlines-2"],
      regression_ids: { chapter_outlines: ["chapter_outlines-3"], chapter_text: ["chapter_text-3"] },
      rule_versions: { volume_outlines: "rule-0", chapter_outlines: "rule-1", chapter_text: "rule-2" },
      thresholds: { completion_rate: 1, quality_gain: 0.1, regression_tolerance: { chapter_outlines: 0, chapter_text: 0 } }, max_rounds: 3,
    })));
  });

  it("切换单章章纲后重新匹配主攻和另外两类回归", async () => {
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("主攻阶段"), { target: { value: "chapter_outlines" } });
    fireEvent.change(screen.getByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      task_type: "chapter_outlines", optimization_ids: ["chapter_outlines-0", "chapter_outlines-1"],
      validation_ids: ["chapter_outlines-2"], regression_ids: { volume_outlines: ["volume_outlines-3"], chapter_text: ["chapter_text-3"] },
    })));
  });

  it("高级设置可选择导入的单章样本，仍自动保留另外两类回归", async () => {
    api.listExperimentPackages.mockResolvedValue({ can_read_content: true, items: [...packages,
      { ...packages[4], id: "custom-opt", title: "自备章纲优化题", suite: undefined },
      { ...packages[6], id: "custom-val", title: "自备章纲终验题", suite: undefined },
    ] as never[] });
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("主攻阶段"), { target: { value: "chapter_outlines" } });
    fireEvent.change(screen.getByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByText("高级设置"));
    fireEvent.click(screen.getByLabelText("自选样本"));
    for (const title of ["chapter_outlines样本0", "chapter_outlines样本1", "chapter_outlines样本2"]) fireEvent.click(screen.getByLabelText(title));
    fireEvent.click(screen.getByLabelText("自备章纲优化题"));
    fireEvent.click(screen.getByLabelText("自备章纲终验题"));
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      optimization_ids: ["custom-opt"], validation_ids: ["custom-val"],
      regression_ids: { volume_outlines: ["volume_outlines-3"], chapter_text: ["chapter_text-3"] },
    })));
  });

  it("实验行直接显示冻结模型和评分环节，旧章纲保留四卷含义", async () => {
    const base = { name: "章纲进化·11/07-01", task_type: "chapter_outlines", status: "running", phase: "regression",
      current_round: 2, max_rounds: 3, baseline: { version: 18, commit: "old" },
      best_candidate: { round: 2, commit: "new" }, report: { exploration: report },
      progress: { total: 4, done: 2, completed: 2 }, updated_at: "2026-11-07T01:00:00Z",
      models: { executor: { name: "冻结执行", model: "writer-frozen" }, judges: [{ name: "冻结评测", model: "judge-frozen" }] } };
    api.listExperiments.mockResolvedValue({ items: [{ ...base, id: "new", flow_version: 2 },
      { ...base, id: "old", name: "旧四卷实验", flow_version: undefined }] as never[] });
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    const newRow = (await screen.findByRole("link", { name: "章纲进化·11/07-01" })).closest("tr")!;
    expect(within(newRow).getByText("单章章纲")).toBeInTheDocument();
    expect(newRow).toHaveTextContent("writer-frozen");
    expect(newRow).toHaveTextContent("judge-frozen");
    expect(newRow).toHaveTextContent("优化");
    expect(newRow).toHaveTextContent("独立终验");
    expect((screen.getByRole("link", { name: "旧四卷实验" })).closest("tr")).toHaveTextContent("4 卷章纲");
  });

  it("四组数据独立展示，回归集不混入三个阶段主集", async () => {
    render(<StagePackages />);
    const volumeTab = await screen.findByRole("tab", { name: /整本卷纲/ });
    expect(volumeTab).toHaveTextContent("3");
    expect(screen.getByRole("tab", { name: /单章章纲/ })).toHaveTextContent("3");
    expect(screen.getByRole("tab", { name: /单章正文/ })).toHaveTextContent("3");
    const regression = screen.getByRole("tab", { name: /回归集/ });
    expect(regression).toHaveTextContent("3");
    fireEvent.click(regression);
    expect(await screen.findByText("volume_outlines样本3")).toBeInTheDocument();
    expect(screen.queryByText("volume_outlines样本0")).not.toBeInTheDocument();
  });

  it("无候选的终态实验行显示未生成，只有活动实验显示改版中", async () => {
    api.listExperiments.mockResolvedValue({ items: ["failed", "exhausted", "stopped", "running"].map((status) => ({
      id: status, name: "无候选-" + status, task_type: "volume_outlines", flow_version: 2, status, phase: "evolving",
      current_round: 2, max_rounds: 3, baseline: { version: 18, commit: "fixed-baseline" },
      current_candidate: null, best_candidate: null, report: {}, progress: { done: 0, total: 0, completed: 0 },
      updated_at: "2026-11-07T01:00:00Z",
    })) as never[] });
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    await screen.findByRole("link", { name: "无候选-running" });
    for (const status of ["failed", "exhausted", "stopped"]) {
      const row = screen.getByRole("link", { name: "无候选-" + status }).closest("tr")!;
      expect(row).toHaveTextContent("未生成候选");
      expect(row).not.toHaveTextContent("改版中");
    }
    expect(screen.getByRole("link", { name: "无候选-running" }).closest("tr")).toHaveTextContent(/第\s*2\s*轮改版中/);
  });

  it("规则默认供阅读，明确进入编辑后才出现表单", async () => {
    api.saveExperimentRule.mockResolvedValue({ ...rules[0], id: "saved-rule", version: 3 } as never);
    render(<StageRules />);
    expect(await screen.findByText("当前阶段是否兑现固定要求？")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑规则" }));
    fireEvent.change(screen.getByLabelText("需求兑现评分问题"), { target: { value: "只检查当前阶段固定目标" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(api.saveExperimentRule).toHaveBeenCalledWith(expect.objectContaining({
      dimensions: [expect.objectContaining({ question: "只检查当前阶段固定目标" })],
    })));
  });
});
