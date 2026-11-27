import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChapterComparison } from "@/components/bench/ChapterComparison";
import { EvolutionExperiments } from "@/components/bench/EvolutionExperiments";
import { SampleScoreDetails } from "@/components/bench/ExperimentEvidence";
import { StagePackages } from "@/components/bench/StagePackages";
import { StageRules } from "@/components/bench/StageRules";
import * as apiModule from "@/lib/api";
import type { ExperimentSample } from "@/lib/experiment-types";
import ExperimentRecords from "./ExperimentRecords";

vi.mock("@/lib/api", () => ({
  getExperimentOptions: vi.fn(), listExperimentPackages: vi.fn(), listExperimentRules: vi.fn(),
  getExperiment: vi.fn(), listExperiments: vi.fn(), startExperiment: vi.fn(), saveExperimentRule: vi.fn(),
  getExperimentPackage: vi.fn(), getExperimentPackageTemplates: vi.fn(), importExperimentPackage: vi.fn(),
  replaceExperimentPackage: vi.fn(), deleteExperimentPackage: vi.fn(),
}));
vi.mock("@/lib/download", () => ({ downloadTextFile: vi.fn() }));

const api = vi.mocked(apiModule);
const kinds = ["volume_outlines", "chapter_outlines", "chapter_text", "master_outline"] as const;
const rules = kinds.map((task_type, index) => ({
  id: `rule-${index}`, task_type, profile: task_type === "chapter_text" ? "chapter-text-outline-v3" : "isolated-writing-v1",
  name: `${task_type}新规则`, version: 2,
  dimensions: [{ key: "需求兑现", question: "当前阶段是否兑现固定要求？", weight: 1,
    anchors: { "1": "目标缺失", "2": "两处实质缺陷", "3": "一处实质缺陷", "4": "仅局部不足", "5": "全部落实" } }],
  discipline_rules: ["引用产物证据"], created_at: "2026-11-07T01:00:00Z",
}));
const packages = kinds.flatMap((task_type, index) => ["optimization", "optimization", "regression", "regression"].map((purpose, n) => ({
  id: `${task_type}-${n}`, title: `${task_type}样本${n}`, task_type, purpose, suite: "isolated-writing-v1",
  target: { task_type, volume_ids: task_type === "volume_outlines" ? [1, 2, 3] : task_type === "master_outline" ? [] : [1],
    ...(task_type === "volume_outlines" ? { chapters_per_volume: 20 } : task_type === "master_outline" ? {} : { chapter: n + 1 }) },
  input_revision: `revision-${index}-${n}`, created_at: "2026-11-07T01:00:00Z",
})));
const metrics = (quality: number) => ({ total: 2, completed: 2, completion_rate: 1, quality, evaluation_errors: 0 });
const report = { passed: true, baseline: metrics(3.6), candidate: metrics(4), quality_delta: 0.4,
  paired_count: 2, changes: [], new_failures: [], problems: [] };
const options = {
  task_types: [...kinds], versions: [{ version: 18, commit: "fixed-baseline", note: "当前生产" }], production_version: 18,
  models: [{ id: 11, name: "执行配置", model: "writer-frozen", is_active: true }],
  judge_groups: [{ id: 5, name: "异源评委", members: [{ config_id: 12, name: "评测配置", model: "judge-frozen" }] }],
  defaults: { max_rounds: 3, rule_versions: Object.fromEntries(kinds.map((kind, n) => [kind, `rule-${n}`])),
    thresholds: { completion_rate: 1, quality_gain: 0.1, regression_tolerance: Object.fromEntries(kinds.map((kind) => [kind, 0])) } },
  builtin_suite: { id: "isolated-writing-v1", total: packages.length },
};

function markdownFile(name: string, content: string) {
  const file = new File([content], name, { type: "text/markdown" });
  Object.defineProperty(file, "text", { value: async () => content });
  return file;
}

beforeEach(() => {
  window.location.hash = "#/agents/volume/bench";
  vi.clearAllMocks();
  api.listExperimentPackages.mockResolvedValue({ items: packages as never[], can_read_content: true });
  api.listExperimentRules.mockResolvedValue({ items: [{ ...rules[1], id: "old-rule", profile: undefined, version: 99 }, ...rules] as never[] });
  api.listExperiments.mockResolvedValue({ items: [] });
  api.getExperimentPackageTemplates.mockResolvedValue({ items: [] });
  api.getExperimentOptions.mockResolvedValue(options as never);
  api.startExperiment.mockResolvedValue({ id: "new-experiment" } as never);
});

describe("三阶段实验台 / REQ-20261107-172211", () => {
  it("仅四项常用选择，自动带入套件、规则与默认门槛", async () => {
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    await screen.findByLabelText("Harness 版本");
    await screen.findByText(/当前没有正在运行的实验/);
    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual([
      "启动自动进化", "正在运行的实验",
    ]);
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

  it.each([
    ["outline", "master_outline"], ["volume", "volume_outlines"],
    ["chapter", "chapter_outlines"], ["text", "chapter_text"],
  ])("%s 工作台默认提交全量，自选从空白开始", async (agent, kind) => {
    window.location.hash = `#/agents/${agent}/bench`;
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    const all = await screen.findByRole("radio", { name: "全量评测" });
    const custom = screen.getByRole("radio", { name: "自选题目" });
    const summary = screen.getByLabelText("启动前配置摘要");
    expect(all).toBeChecked();
    expect(summary).toHaveTextContent("改进题 2/2 道 · 回归题 2/2 道");

    fireEvent.click(custom);
    expect(summary).toHaveTextContent("改进题 0/2 道 · 回归题 0/2 道");
    expect(screen.getAllByRole("checkbox")).toHaveLength(4);
    expect(screen.queryByRole("checkbox", { checked: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: `${kind}样本0` }));
    expect(screen.getByRole("button", { name: "启动自进化实验" })).toBeDisabled();

    fireEvent.click(all);
    fireEvent.click(custom);
    expect(screen.queryByRole("checkbox", { checked: true })).not.toBeInTheDocument();
    fireEvent.click(all);
    fireEvent.change(screen.getByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledOnce());
    const payload = api.startExperiment.mock.calls[0][0];
    expect(payload).toMatchObject({ task_type: kind,
      optimization_ids: [`${kind}-0`, `${kind}-1`], regression_ids: { [kind]: [`${kind}-2`, `${kind}-3`] },
    });
    expect(payload).not.toHaveProperty("case_selection");
  });

  it("自选模式提交两组所选题和原有指定候选", async () => {
    render(<MemoryRouter initialEntries={["/bench?candidate=known-candidate&base=fixed-baseline"]}>
      <EvolutionExperiments />
    </MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("radio", { name: "自选题目" }));
    const start = screen.getByRole("button", { name: "启动自进化实验" });
    fireEvent.click(screen.getByRole("checkbox", { name: "volume_outlines样本3" }));
    expect(start).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "volume_outlines样本1" }));
    expect(screen.getByLabelText("启动前配置摘要")).toHaveTextContent("改进题 1/2 道 · 回归题 1/2 道");
    fireEvent.click(start);
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      case_selection: "custom", candidate_commit: "known-candidate", optimization_ids: ["volume_outlines-1"],
      regression_ids: { volume_outlines: ["volume_outlines-3"] },
    })));
  });

  it("同名改进题显示各自的目标和输入版本", async () => {
    const first = { ...packages[0], title: "同名改进题", input_revision: "first-case-a-full" };
    const second = { ...packages[1], title: first.title, input_revision: "other-case-b-full",
      target: { ...packages[1].target, volume_ids: [1, 2, 3, 4] } };
    api.listExperimentPackages.mockResolvedValueOnce({
      items: [first, second, ...packages.slice(2)] as never[], can_read_content: true,
    });
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("radio", { name: "自选题目" }));
    expect(screen.getByRole("checkbox", { name: /同名改进题.*整本 3 卷.*输入版本 first-case-a/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /同名改进题.*整本 4 卷.*输入版本 other-case-b/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "volume_outlines样本2" }));
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      case_selection: "custom", optimization_ids: [second.id], regression_ids: { volume_outlines: ["volume_outlines-2"] },
    })));
  });

  it("刷新发现所选题已替换时，须主动重新选择", async () => {
    api.startExperiment.mockRejectedValueOnce(new Error("所选题目已不在对应题组，请刷新题库后核对"));
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("评测模型"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("radio", { name: "自选题目" }));
    for (const suffix of [0, 1, 2]) fireEvent.click(screen.getByRole("checkbox", { name: `volume_outlines样本${suffix}` }));
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    expect(await screen.findByText(/请刷新题库后核对/)).toBeInTheDocument();

    api.listExperimentPackages.mockResolvedValueOnce({
      items: [{ ...packages[0], id: "replacement", title: "替换后的改进题" }, ...packages.slice(1)] as never[],
      can_read_content: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "刷新配置" }));
    expect(await screen.findByRole("checkbox", { name: "替换后的改进题" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "启动自进化实验" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新选题" }));
    expect(screen.queryByRole("checkbox", { checked: true })).not.toBeInTheDocument();
    for (const name of ["替换后的改进题", "volume_outlines样本2"]) {
      fireEvent.click(screen.getByRole("checkbox", { name }));
    }
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenLastCalledWith(expect.objectContaining({
      case_selection: "custom", optimization_ids: ["replacement"], regression_ids: { volume_outlines: ["volume_outlines-2"] },
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
    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual([
      "正在运行的实验", "启动自动进化",
    ]);
    expect(within(newRow).getByText("章纲")).toBeInTheDocument();
    expect(newRow).toHaveTextContent("本套回归");
    expect(newRow).toHaveTextContent("改版 2 / 3 轮");
    expect(screen.queryByRole("link", { name: "旧四卷实验" })).not.toBeInTheDocument();
  });

  it("运行概况读取失败时有错误提示，期间不报告没有活动实验", async () => {
    let rejectRuns!: (error: Error) => void;
    api.listExperiments.mockReturnValueOnce(new Promise((_, reject) => { rejectRuns = reject; }));
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    expect(screen.getByText(/正在读取运行概况/)).toBeInTheDocument();
    expect(screen.queryByText(/当前没有正在运行的实验/)).not.toBeInTheDocument();
    await act(async () => { rejectRuns(new Error("实验服务不可用")); });
    expect(screen.getByRole("alert")).toHaveTextContent("实验服务不可用");
    expect(screen.queryByText(/当前没有正在运行的实验/)).not.toBeInTheDocument();
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

  it("未切换到回归题也能看见缺项，不能把读取失败当成缺项", async () => {
    api.listExperimentPackages.mockResolvedValueOnce({ items: [packages[0]] as never[], can_read_content: true });
    const loaded = render(<StagePackages />);
    expect(await screen.findByRole("tab", { name: /改进题.*已齐备/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /回归题.*缺少题目/ })).toBeInTheDocument();
    loaded.unmount();

    api.listExperimentPackages.mockRejectedValueOnce(new Error("无法连接数据集"));
    render(<StagePackages />);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接数据集");
    expect(screen.queryByRole("tab", { name: /回归题/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/本组暂无题目/)).not.toBeInTheDocument();
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

  it("导入正文题时可以随章纲添加多张人物卡", async () => {
    window.location.hash = "#/agents/text/bench";
    const sample = packages.find((item) => item.task_type === "chapter_text")!;
    api.importExperimentPackage.mockResolvedValue(sample as never);
    render(<StagePackages />);
    await screen.findByText(sample.title);
    fireEvent.click(screen.getByRole("button", { name: "导入章纲" }));
    fireEvent.change(screen.getByLabelText("章纲文件"), {
      target: { files: [markdownFile("夜渡.md", "本章章纲")] },
    });
    fireEvent.change(screen.getByLabelText("人物卡文件（可多选）"), {
      target: { files: [markdownFile("沈棠.md", "# 沈棠\r\n不说谎。"), markdownFile("凌川.MD", "# 凌川")] },
    });
    fireEvent.submit(screen.getByRole<HTMLButtonElement>("button", { name: "保存题目" }).form!);
    await waitFor(() => expect(api.importExperimentPackage).toHaveBeenCalledWith({
      title: "夜渡", purpose: "optimization", target: { task_type: "chapter_text", chapter: 1, volume_ids: [] },
      input: { demand_md: "", confirmation_rules: "按本章章纲直接完成，不等待确认。", skills: [],
        files: { "章纲/第001章.md": "本章章纲", "人物/沈棠.md": "# 沈棠\r\n不说谎。", "人物/凌川.md": "# 凌川" } },
    }));
  });

  it("人物卡可单独查看，编辑时可增删改并保留其他固定输入", async () => {
    window.location.hash = "#/agents/text/bench";
    const sample = packages.find((item) => item.task_type === "chapter_text")!;
    const input = { demand_md: "", confirmation_rules: "直接写", input_revision: sample.input_revision,
      skills: [{ stage: "text", name: "对白", content: "写有冲突的对白。" }],
      files: { "章纲/第001章.md": "本章章纲", "章纲/第002章.md": "旁章章纲", "人物/沈棠.md": "守诺。",
        "人物/凌川.md": "等潮水。", "人物/秋禾.md": "# 秋禾\r\n从南方来。", "人物/旧卡.md": "" } };
    api.getExperimentPackage.mockResolvedValue({ ...sample, input } as never);
    api.replaceExperimentPackage.mockResolvedValue({ ...sample, id: "new-cards", input_revision: "new-revision" } as never);
    render(<StagePackages />);
    const row = (await screen.findByText(sample.title)).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "查看输入" }));
    const preview = await screen.findByRole("region", { name: "人物卡" });
    expect(within(preview).getByText("人物/沈棠.md")).toBeVisible();
    expect(within(preview).queryByText("章纲/第001章.md")).not.toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "编辑" }));
    const editor = await screen.findByRole("group", { name: "人物卡" });
    fireEvent.click(within(editor).getByText("沈棠", { selector: "summary" }));
    fireEvent.change(within(editor).getByLabelText("沈棠的人物卡内容"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("人物卡「沈棠」内容不能为空。");
    expect(api.replaceExperimentPackage).not.toHaveBeenCalled();
    fireEvent.change(within(editor).getByLabelText("沈棠的人物卡内容"), { target: { value: "守诺，怕水。" } });
    fireEvent.click(within(editor).getByText("凌川", { selector: "summary" }));
    fireEvent.click(within(editor).getByRole("checkbox", { name: "保存时移除凌川的人物卡" }));
    fireEvent.change(within(editor).getByLabelText("导入人物卡文件（可多选）"), {
      target: { files: [markdownFile("陆望.md", "# 陆望\r\n老水手。")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(api.replaceExperimentPackage).toHaveBeenCalledWith(sample.id, {
      title: sample.title, purpose: sample.purpose, target: sample.target,
      input: { ...input, input_revision: undefined,
        files: { "章纲/第001章.md": "本章章纲", "章纲/第002章.md": "旁章章纲", "人物/沈棠.md": "守诺，怕水。",
          "人物/秋禾.md": "# 秋禾\r\n从南方来。", "人物/旧卡.md": "", "人物/陆望.md": "# 陆望\r\n老水手。" } },
    }));
  });

  it("页内编辑保存新版本并保留用途、目标和技能", async () => {
    window.location.hash = "#/agents/text/bench";
    const sample = packages.find((item) => item.task_type === "chapter_text")!;
    const path = "章纲/第001章.md";
    const previous = "章纲/第002章.md";
    const input = { demand_md: "原需求", files: { [path]: "旧章纲", [previous]: "其他章纲" }, skills: [{ name: "对白" }],
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
    expect(screen.queryByLabelText("完整需求")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(previous)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(path), { target: { value: "新章纲" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(api.replaceExperimentPackage).toHaveBeenCalledWith(sample.id, {
      title: revision.title, purpose: sample.purpose, target: sample.target,
      input: { ...input, files: { ...input.files, [path]: "新章纲" }, input_revision: undefined },
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
      expect(within(row).getByRole("button", { name: "查看输入" })).toBeDisabled();
      expect(within(row).getByLabelText("替换资料包")).toBeEnabled();
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
    const dimension = screen.getByRole("heading", { name: "需求兑现" });
    expect(within(dimension.parentElement!).getByText("100% 权重")).toBeVisible();
    const completionCheck = screen.getByRole("heading", { name: /完成检查/ });
    expect(dimension.compareDocumentPosition(completionCheck) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑规则" }));
    fireEvent.change(screen.getByLabelText("需求兑现评分问题"), { target: { value: "只检查当前阶段固定目标" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(api.saveExperimentRule).toHaveBeenCalledWith(expect.objectContaining({
      dimensions: [expect.objectContaining({ question: "只检查当前阶段固定目标" })],
    })));
  });
});

describe("正文三项评测 / REQ-20261120-161028", () => {
  const judges = [{ id: 12, name: "评测配置", model: "judge-frozen" }];
  const reason = { judge_id: 12, repeat: 1, score: 4, reason: "主线依据充分，局部承接略弱。", evidence: ["她跟着铃声回头。"] };
  const sample: ExperimentSample = { id: "sample", task_type: "chapter_text", package_id: "p", package_title: "正文题", input_revision: "r1",
    phase: "baseline", round: 0, arm: "baseline", repeat: 1, attempt: 1, artifacts: {}, trace_ids: [],
    completed: true, status: "failed", scores: null, task_id: "task", checks: {}, error: null, usage: {}, source_commit: "frozen" };

  it("规则页默认打开六维新版，旧维度规则保留五档只读", async () => {
    window.location.hash = "#/bench/rules?agent=text";
    const template = rules[2];
    const previous = { ...template, id: "chapter-v6", version: 6,
      dimensions: ["事件因果逻辑", "人物行为逻辑", "章纲兑现", "语言清晰度"].map((key) => ({ ...template.dimensions[0], key })) };
    const current = { ...previous, id: "chapter-v7", version: 7,
      dimensions: [...previous.dimensions, ...["人物语言", "场景空间"].map((key) => ({ ...template.dimensions[0], key }))] };
    api.listExperimentRules.mockResolvedValue({ items: [
      { ...template, id: "chapter-v2", profile: "chapter-text-outline-v2", name: "正文历史规则", version: 1 }, previous, current,
    ] as never[] });
    render(<StageRules />);
    const versions = await screen.findByLabelText("规则版本");
    expect(versions).toHaveValue(current.id);
    expect(screen.getByRole("heading", { name: "场景空间" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑规则" })).toBeInTheDocument();
    expect(within(versions).getByRole("option", { name: /历史 · v6/ })).toBeInTheDocument();
    fireEvent.change(versions, { target: { value: previous.id } });
    expect(screen.getByText(/历史规则只读/)).toBeInTheDocument();
    expect(screen.getAllByText("全部落实")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "编辑规则" })).not.toBeInTheDocument();
    fireEvent.change(versions, { target: { value: "chapter-v2" } });
    expect(screen.getByText(/历史规则只读/)).toBeInTheDocument();
    expect(screen.getByText("全部落实")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑规则" })).not.toBeInTheDocument();
  });

  it("正文实验启动前显示每模型次数，并使用新版规则", async () => {
    window.location.hash = "#/bench?agent=text";
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("评测模型"), { target: { value: "5" } });
    expect(screen.getByText(/每个模型评 3 次/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "启动自进化实验" }));
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledWith(expect.objectContaining({
      task_type: "chapter_text", judge_group_id: 5, rule_versions: { chapter_text: "rule-2" },
    })));
  });

  it.each(["全量", "自选"])("正文自动发布的%s实验只提交改进题", async (mode) => {
    window.location.hash = "#/bench?agent=text";
    api.getExperimentOptions.mockResolvedValueOnce({ ...options, auto_publish: true } as never);
    if (mode === "全量") {
      const withoutRegression = packages.filter((item) => item.task_type === "chapter_text" && item.purpose === "optimization");
      api.listExperimentPackages.mockResolvedValueOnce({ items: withoutRegression as never[], can_read_content: true });
    }
    render(<MemoryRouter><EvolutionExperiments /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("评测模型"), { target: { value: "5" } });
    const summary = screen.getByLabelText("启动前配置摘要");
    const start = screen.getByRole("button", { name: "启动自进化实验" });
    expect(summary).toHaveTextContent("改进题 2/2 道");
    expect(summary).toHaveTextContent("达标后自动发布");
    expect(summary).not.toHaveTextContent("回归");
    if (mode === "自选") {
      fireEvent.click(screen.getByRole("radio", { name: "自选题目" }));
      expect(screen.getAllByRole("checkbox")).toHaveLength(2);
      expect(start).toBeDisabled();
      fireEvent.click(screen.getByRole("checkbox", { name: "chapter_text样本1" }));
      expect(summary).toHaveTextContent("改进题 1/2 道");
    }
    expect(start).toBeEnabled();
    fireEvent.click(start);
    await waitFor(() => expect(api.startExperiment).toHaveBeenCalledOnce());
    const payload = api.startExperiment.mock.calls[0][0];
    expect(payload).toMatchObject({ task_type: "chapter_text", auto_publish: true,
      optimization_ids: mode === "自选" ? ["chapter_text-1"] : ["chapter_text-0", "chapter_text-1"],
    });
    expect(payload.regression_ids).toEqual({});
    expect(payload.thresholds.regression_tolerance).toEqual({});
    expect(payload.case_selection).toBe(mode === "自选" ? "custom" : undefined);
  });

  it("完整评分可独立核对每次分数、理由和依据，历史评语仍可读", () => {
    render(<SampleScoreDetails sample={{ ...sample, scores: { overall: 4, dimensions: { 剧情逻辑: 4 },
      reasons: { 剧情逻辑: [reason, { ...reason, repeat: 2, score: 5, reason: "前后自洽。" },
        { judge_id: 12, score: 4, 达标: ["历史达标依据"], 不足: ["历史不足"] }] } } }} judges={judges} />);
    expect(screen.getByText(/评测配置 · 第 1 次 · 4/)).toBeInTheDocument();
    expect(screen.getByText(/评测配置 · 第 2 次 · 5/)).toBeInTheDocument();
    expect(screen.getByText(reason.reason)).toBeInTheDocument();
    expect(screen.getAllByText(reason.evidence[0])).toHaveLength(2);
    expect(screen.getByText("历史达标依据")).toBeInTheDocument();
  });

  it("部分评分显示已完成的评语和冻结设置，维持无汇总分", () => {
    const run = { rounds: [], baseline: { version: 1, commit: "frozen" }, can_read_content: true,
      models: { executor: null, judges }, judge_repeats: 2, scoring_rule: { id: "frozen-rule", name: "冻结规则", version: 8 },
      samples: [{ ...sample, score_progress: { completed: 1, total: 2, reasons: { 剧情逻辑: [reason] } } }] };
    const view = render(<ChapterComparison experiment={run} onTrace={vi.fn()} initialRound={null} />);
    expect(screen.getByText(/冻结规则.*v8/)).toBeInTheDocument();
    expect(screen.getByText(/每个模型评 2 次/)).toBeInTheDocument();
    expect(screen.getByText(/已完成 1 \/ 2 份评分/)).toBeInTheDocument();
    expect(screen.getByText(reason.reason)).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "质量变化" })).getAllByText("未评分")).toHaveLength(2);

    view.rerender(<ChapterComparison experiment={{ ...run, can_read_content: false }} onTrace={vi.fn()} initialRound={null} />);
    expect(screen.getByText(/已完成 1 \/ 2 份评分/)).toBeInTheDocument();
    expect(screen.queryByText(reason.reason)).not.toBeInTheDocument();
    expect(screen.queryByText(reason.evidence[0])).not.toBeInTheDocument();
  });

  it("新正文实验无总体对比结果时也能从记录打开评分明细", async () => {
    window.location.hash = "#/bench/experiments?agent=text";
    const record = { id: "archive", task_type: "chapter_text", flow_version: 3, name: "未评齐的正文实验",
      status: "failed", phase: "baseline", report: {}, baseline: { version: 1 }, current_round: 0, max_rounds: 3, judge_repeats: 3,
      created_at: "2026-11-20T00:00:00Z" };
    api.listExperiments.mockResolvedValue({ items: [record,
      { ...record, id: "explore", name: "未评齐的优化复测", phase: "exploration", current_round: 3 },
      { ...record, id: "regress", name: "未评齐的回归", phase: "regression", current_round: 2 },
      { ...record, id: "old", name: "历史正文实验", judge_repeats: undefined }] as never[] });
    api.getExperiment.mockResolvedValue({ ...record, can_read_content: true, rounds: [], models: { executor: null, judges },
      baseline: { version: 1, commit: "frozen" }, samples: [{ ...sample,
        score_progress: { completed: 1, total: 2, reasons: { 剧情逻辑: [reason] } },
      }] } as never);
    render(<MemoryRouter initialEntries={["/bench/experiments?agent=text"]}><ExperimentRecords /></MemoryRouter>);
    await screen.findByRole("link", { name: "未评齐的正文实验" });
    for (const [name, href] of [
      ["未评齐的正文实验", "/bench/experiments?scoring=archive&agent=text"],
      ["未评齐的优化复测", "/bench/experiments?scoring=explore&round=3&phase=exploration&agent=text"],
      ["未评齐的回归", "/bench/experiments?scoring=regress&round=2&phase=regression&task=chapter_text&agent=text"],
    ]) {
      expect(within(screen.getByRole("link", { name }).closest("article")!).getByRole("link", { name: "查看评分明细" }))
        .toHaveAttribute("href", href);
    }
    expect(within(screen.getByRole("link", { name: "历史正文实验" }).closest("article")!)
      .queryByRole("link", { name: "查看评分明细" })).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("link", { name: "未评齐的正文实验" }).closest("article")!)
      .getByRole("link", { name: "查看评分明细" }));
    expect(await screen.findByText(/已完成 1 \/ 2 份评分/)).toBeInTheDocument();
    expect(screen.getByText(reason.reason)).toBeInTheDocument();
    expect(api.getExperiment).toHaveBeenCalledWith("archive");
    fireEvent.click(screen.getByRole("link", { name: "返回实验记录" }));
    expect(await screen.findByRole("link", { name: "未评齐的正文实验" })).toBeInTheDocument();
  });
});
