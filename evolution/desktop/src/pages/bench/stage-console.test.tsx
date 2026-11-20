import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { EvolutionExperiments } from "@/components/bench/EvolutionExperiments";
import { StagePackages } from "@/components/bench/StagePackages";
import { StageRules } from "@/components/bench/StageRules";
import * as apiModule from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getExperimentOptions: vi.fn(), listExperimentPackages: vi.fn(), listExperimentRules: vi.fn(),
  listExperiments: vi.fn(), startExperiment: vi.fn(), saveExperimentRule: vi.fn(),
  getExperimentPackage: vi.fn(), getExperimentPackageTemplates: vi.fn(), importExperimentPackage: vi.fn(),
  replaceExperimentPackage: vi.fn(), deleteExperimentPackage: vi.fn(),
}));
vi.mock("@/lib/download", () => ({ downloadTextFile: vi.fn() }));

const api = vi.mocked(apiModule);
const kinds = ["volume_outlines", "chapter_outlines", "chapter_text"] as const;
const rules = kinds.map((task_type, index) => ({
  id: `rule-${index}`, task_type, profile: task_type === "chapter_text" ? "chapter-text-outline-v2" : "isolated-writing-v1",
  name: `${task_type}新规则`, version: 2,
  dimensions: [{ key: "需求兑现", question: "当前阶段是否兑现固定要求？", weight: 1,
    anchors: { "1": "目标缺失", "2": "两处实质缺陷", "3": "一处实质缺陷", "4": "仅局部不足", "5": "全部落实" } }],
  discipline_rules: ["引用产物证据"], created_at: "2026-11-07T01:00:00Z",
}));
const packages = kinds.flatMap((task_type, index) => ["optimization", "optimization", "regression", "regression"].map((purpose, n) => ({
  id: `${task_type}-${n}`, title: `${task_type}样本${n}`, task_type, purpose, suite: "isolated-writing-v1",
  target: { task_type, volume_ids: task_type === "volume_outlines" ? [1, 2, 3] : [1],
    ...(task_type !== "volume_outlines" ? { chapter: n + 1 } : { chapters_per_volume: 20 }) },
  input_revision: `revision-${index}-${n}`, created_at: "2026-11-07T01:00:00Z",
})));
const metrics = (quality: number) => ({ total: 2, completed: 2, completion_rate: 1, quality, evaluation_errors: 0 });
const report = { passed: true, baseline: metrics(3.6), candidate: metrics(4), quality_delta: 0.4,
  paired_count: 2, changes: [], new_failures: [], problems: [] };

beforeEach(() => {
  window.location.hash = "#/agents/volume/bench";
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
    builtin_suite: { id: "isolated-writing-v1", total: 12 },
  } as never);
  api.startExperiment.mockResolvedValue({ id: "new-experiment" } as never);
});

describe("三阶段实验台 / REQ-20261107-172211", () => {
  it("仅四项常用选择，自动带入套件、规则与默认门槛", async () => {
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    await screen.findByLabelText("Harness 版本");
    expect(screen.getAllByRole("combobox")).toHaveLength(3);
    expect(screen.queryByRole("textbox", { name: "实验名称" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      task_type: "volume_outlines", baseline_version: 18, model_config_id: 11, judge_group_id: 5,
      optimization_ids: ["volume_outlines-0", "volume_outlines-1"],
      regression_ids: { volume_outlines: ["volume_outlines-2", "volume_outlines-3"] },
      rule_versions: { volume_outlines: "rule-0" },
      thresholds: { completion_rate: 1, quality_gain: 0.1, regression_tolerance: { volume_outlines: 0 } }, max_rounds: 3,
    })));
  });

  it("章纲入口只提交章纲题，包含自备题目", async () => {
    window.location.hash = "#/agents/chapter/bench";
    api.listExperimentPackages.mockResolvedValue({can_read_content: true, items: [...packages,
      {...packages[4], id: "custom-opt", title: "自备题", suite: undefined}] as never[]});
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("评测模型"), {target: {value: "5"}});
    fireEvent.click(screen.getByRole("button", {name: "启动自进化实验"}));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      task_type: "chapter_outlines", optimization_ids: ["chapter_outlines-0", "chapter_outlines-1", "custom-opt"],
      regression_ids: {chapter_outlines: ["chapter_outlines-2", "chapter_outlines-3"]},
    })));
  });

  it("运行概况显示当前环节并排除旧实验", async () => {
    window.location.hash = "#/agents/chapter/bench";
    const base = { name: "章纲进化·11/07-01", task_type: "chapter_outlines", status: "running", phase: "regression",
      current_round: 2, max_rounds: 3, baseline: { version: 18, commit: "old" },
      best_candidate: { round: 2, commit: "new" }, report: { exploration: report },
      progress: { total: 4, done: 2, completed: 2 }, updated_at: "2026-11-07T01:00:00Z",
      models: { executor: { name: "冻结执行", model: "writer-frozen" }, judges: [{ name: "冻结评测", model: "judge-frozen" }] } };
    api.listExperiments.mockResolvedValue({ items: [{ ...base, id: "new", flow_version: 3 },
      { ...base, id: "old", name: "旧四卷实验", flow_version: undefined }] as never[] });
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    const newRow = (await screen.findByRole("link", { name: "章纲进化·11/07-01" })).closest("article")!;
    expect(within(newRow).getByText("章纲")).toBeInTheDocument();
    expect(newRow).toHaveTextContent("本套回归");
    expect(newRow).toHaveTextContent("改版 2 / 3 轮");
    expect(screen.queryByRole("link", { name: "旧四卷实验" })).not.toBeInTheDocument();
  });

  it("仅展示当前套改进题与回归题", async () => {
    render(<StagePackages />);
    const improvement = await screen.findByRole("tab", {name: /改进题/});
    expect(improvement).toHaveTextContent("2");
    const regression = screen.getByRole("tab", {name: /回归题/});
    expect(regression).toHaveTextContent("2");
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    fireEvent.click(regression);
    expect(await screen.findByText("volume_outlines样本3")).toBeInTheDocument();
    expect(screen.queryByText("volume_outlines样本0")).not.toBeInTheDocument();
  });

  it("正文题可导入 Markdown 技能并生成新版本", async () => {
    window.location.hash = "#/agents/text/bench";
    const sample = packages.find((item) => item.task_type === "chapter_text")!;
    const input = { demand_md: "", files: { "章纲/第001章.md": "本章章纲" }, skills: [],
      confirmation_rules: "直接写", input_revision: sample.input_revision };
    api.getExperimentPackage.mockResolvedValue({ ...sample, input } as never);
    api.replaceExperimentPackage.mockResolvedValue(sample as never);
    render(<StagePackages />);
    const row = (await screen.findByText(sample.title)).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "查看输入" }));
    const upload = await screen.findByLabelText("导入技能并替换");
    const content = "---\nname: 对白\n---\n用对白推进冲突。";
    const file = new File([content], "SKILL.md");
    Object.defineProperty(file, "text", { value: async () => content });
    fireEvent.change(upload, { target: { files: [file] } });
    await waitFor(() => expect(api.replaceExperimentPackage).toHaveBeenCalledWith(sample.id, expect.objectContaining({
      input: { ...input, input_revision: undefined, skills: [{ stage: "text", name: "对白", content }] },
    })));
  });

  it("页内编辑保存新版本并保留用途、目标和技能", async () => {
    window.location.hash = "#/agents/text/bench";
    const sample = packages.find((item) => item.task_type === "chapter_text")!;
    const path = "章纲/第001章.md";
    const input = { demand_md: "原需求", files: { [path]: "旧章纲" }, skills: [{ name: "对白" }],
      confirmation_rules: "第一条\r\n第二条", input_revision: sample.input_revision };
    const revision = { ...sample, id: "revised-text", title: "新题目", input_revision: "new-revision" };
    api.getExperimentPackage.mockResolvedValue({ ...sample, input } as never);
    api.replaceExperimentPackage.mockResolvedValue(revision as never);
    api.listExperimentPackages.mockResolvedValueOnce({ items: [sample] as never[], can_read_content: true })
      .mockResolvedValueOnce({ items: [revision] as never[], can_read_content: true });
    render(<StagePackages />);
    const row = (await screen.findByText(sample.title)).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "编辑" }));
    fireEvent.change(await screen.findByLabelText("题目名称"), { target: { value: revision.title } });
    fireEvent.change(screen.getByLabelText("完整需求"), { target: { value: "新需求" } });
    fireEvent.change(screen.getByLabelText(path), { target: { value: "新章纲" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(api.replaceExperimentPackage).toHaveBeenCalledWith(sample.id, {
      title: revision.title, purpose: sample.purpose, target: sample.target,
      input: { ...input, demand_md: "新需求", files: { [path]: "新章纲" }, input_revision: undefined },
    }));
    expect(await screen.findByText(revision.title)).toBeInTheDocument();
    expect(screen.queryByText(sample.title)).not.toBeInTheDocument();
  });

  it("确认删除后刷新题库并显示空组提示", async () => {
    const sample = packages[0];
    api.listExperimentPackages.mockResolvedValueOnce({ items: [sample] as never[], can_read_content: false })
      .mockResolvedValueOnce({ items: [], can_read_content: false });
    api.deleteExperimentPackage.mockResolvedValue({ ok: true });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    try {
      render(<StagePackages />);
      const row = (await screen.findByText(sample.title)).closest("tr")!;
      expect(within(row).getByRole("button", { name: "编辑" })).toBeDisabled();
      const remove = within(row).getByRole("button", { name: "删除" });
      fireEvent.click(remove);
      expect(api.deleteExperimentPackage).not.toHaveBeenCalled();
      fireEvent.click(remove);
      expect(api.deleteExperimentPackage).toHaveBeenCalledWith(sample.id);
      expect(await screen.findByText(/本组暂无题目/)).toBeInTheDocument();
      expect(api.listExperimentPackages).toHaveBeenCalledTimes(2);
      expect(screen.queryByText(sample.title)).not.toBeInTheDocument();
    } finally { confirm.mockRestore(); }
  });

  it("切换 Agent 时不提交仍在读取的导入文件", async () => {
    let finishRead!: (value: string) => void;
    const content = new Promise<string>((resolve) => { finishRead = resolve; });
    const file = new File([], "sample.json");
    Object.defineProperty(file, "text", { value: () => content });
    const page = render(<StagePackages />);
    fireEvent.change(screen.getByLabelText("选择资料包"), { target: { files: [file] } });
    window.location.hash = "#/agents/text/bench";
    page.unmount();
    await act(async () => { finishRead("{}"); });
    expect(api.importExperimentPackage).not.toHaveBeenCalled();
  });

  it("工作台只展示未结束实验，历史实验从实验记录查看", async () => {
    api.listExperiments.mockResolvedValue({ items: ["failed", "exhausted", "stopped", "running"].map((status) => ({
      id: status, name: "无候选-" + status, task_type: "volume_outlines", flow_version: 3, status, phase: "evolving",
      current_round: 2, max_rounds: 3, baseline: { version: 18, commit: "fixed-baseline" },
      current_candidate: null, best_candidate: null, report: {}, progress: { done: 0, total: 0, completed: 0 },
      updated_at: "2026-11-07T01:00:00Z",
    })) as never[] });
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    await screen.findByRole("link", { name: "无候选-running" });
    expect(api.listExperiments).toHaveBeenCalledWith(true);
    for (const status of ["failed", "exhausted", "stopped"]) {
      expect(screen.queryByRole("link", { name: "无候选-" + status })).not.toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "无候选-running" }).closest("article")).toHaveTextContent("正在修改 Harness");
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
