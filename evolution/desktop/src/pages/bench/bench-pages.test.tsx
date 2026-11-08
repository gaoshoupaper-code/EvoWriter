/** 评测共享能力回归与旧版入口退役验证。旧批次页面行为由新实验观测测试接替。 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, beforeAll, describe, expect, it, vi } from "vitest";

// ── api mock（vi.mock 工厂被提升，内部不能引用顶层变量；此处只定义 mock 函数，
//    测试通过 vi.mocked(apiModule) 使用同一批实例） ──
vi.mock("@/lib/api", () => ({
  fetchMeOrNull: vi.fn(),
  logout: vi.fn(),
  runBenchmark: vi.fn(),
  stopBenchmark: vi.fn(),
  listBenchmarkBatches: vi.fn(),
  getBenchmarkReport: vi.fn(),
  compareBatches: vi.fn(),
  getBenchmarkRubric: vi.fn(),
  listJudgeCandidates: vi.fn(),
  listJudgeGroups: vi.fn(),
  createJudgeGroup: vi.fn(),
  updateJudgeGroup: vi.fn(),
  deleteJudgeGroup: vi.fn(),
  rerunGolden: vi.fn(),
  createGoldenCase: vi.fn(),
  getDatasetCases: vi.fn(),
  getCaseContent: vi.fn(),
  getGoldenRevision: vi.fn(),
  getBenchmarkVersions: vi.fn(),
  getLeaderboard: vi.fn(),
  listBenchmarkRuns: vi.fn(),
  getBenchmarkDeliveries: vi.fn(),
  getArtifactRevisionContent: vi.fn(),
  exportTraceContent: vi.fn(),
  listExperimentPackages: vi.fn(),
  getExperimentPackageTemplates: vi.fn(),
  listExperimentRules: vi.fn(),
  getExperimentOptions: vi.fn(),
  listExperiments: vi.fn(),
}));

import * as apiModule from "@/lib/api";
import type { BenchmarkRunRow } from "@/lib/api";
import Shell from "@/components/Shell";
import Workbench from "@/pages/bench/Workbench";
import BatchDetail from "@/pages/bench/BatchDetail";
import Rules from "@/pages/bench/Rules";
import Dataset from "@/pages/bench/Dataset";
import Artifacts from "@/pages/bench/Artifacts";

const api = vi.mocked(apiModule);

// ── 造数助手 ──

function setupCommonMocks() {
  api.fetchMeOrNull.mockResolvedValue({
    user_id: "u1", username: "tester", is_admin: true,
    is_super_admin: true, has_api_key: false,
  });
  api.listBenchmarkBatches.mockResolvedValue({ batches: [] });
  api.getBenchmarkVersions.mockResolvedValue({ items: [], production_version: 7, total: 0 });
  api.listJudgeCandidates.mockResolvedValue({
    judges: [],
    default: { scope: "eval", model: "test-model", fingerprint: "fp", degraded: false },
  });
  // 评测组默认一组（REQ-20260930-162207/FR-002：触发必选组）
  api.listJudgeGroups.mockResolvedValue({
    groups: [{
      id: 1, name: "评审团",
      members: [{
        config_id: 11, name: "glm", model: "glm-4.7", scope: "eval",
        is_active: true, has_key: true, same_family_as_executor: false,
      }],
    }],
  });
  api.getDatasetCases.mockResolvedValue({ cases: [], total: 0 });
}

beforeAll(() => {
  // jsdom 无 scrollIntoView（DeliveriesView 定位效果依赖）
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  vi.clearAllMocks();
  setupCommonMocks();
  api.listExperimentPackages.mockResolvedValue({ items: [], can_read_content: false });
  api.getExperimentPackageTemplates.mockResolvedValue({ items: [] });
  api.listExperimentRules.mockResolvedValue({ items: [] });
  api.getExperimentOptions.mockResolvedValue({ task_types: ["volume_outlines", "chapter_outlines", "chapter_text"], versions: [], production_version: null, models: [], judge_groups: [] });
  api.listExperiments.mockResolvedValue({ items: [] });
});

// ── AC-001：侧栏评测分组 ──

describe("Shell 导航（AC-001）", () => {
  it("评测分组含 4 入口；旧评测/数据集入口不在原分组", async () => {
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    for (const label of ["评测工作台", "评测规则", "评测数据集", "实验记录"]) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    // 系统资产分组下不再有旧「数据集」项；质量闭环下不再有旧「评测」项
    const nav = document.querySelector(".shell-nav")!;
    const sysAssets = [...nav.querySelectorAll(".shell-nav-group")]
      .find((g) => g.textContent?.includes("系统资产"))!;
    expect(sysAssets.textContent).not.toContain("数据集");
    const quality = [...nav.querySelectorAll(".shell-nav-group")]
      .find((g) => g.textContent?.includes("质量闭环"))!;
    expect(quality.textContent).not.toContain("评测");
  });
});

// ── AC-003/004/005：工作台 ──

describe("维度卡组评分（AC-006/007）", () => {
  const judgeGroup = {
    group_id: 1,
    group_name: "三人评审团",
    members: [
      {
        config_id: 11, name: "glm", model: "glm-4.7", fingerprint: "a",
        scores: { 需求兑现: 5 }, reasons: { 需求兑现: { 达标: ["glm 达标理由"], 不足: ["未发现不足"] } },
      },
      {
        config_id: 22, name: "kimi", model: "kimi-k2", fingerprint: "b",
        scores: { 需求兑现: 2 }, reasons: { 需求兑现: { 达标: ["kimi 达标理由"], 不足: ["kimi 不足理由"] } },
      },
      {
        // 该维缺席（评分失败，DEC-003）
        config_id: 33, name: "deepseek", model: "deepseek-v3", fingerprint: "c",
        scores: { 需求兑现: null }, reasons: { 需求兑现: null },
      },
    ],
  };

  it("judge 极差 ≥2 高亮 + 悬停显示各 judge 分；理由按 judge 折叠分组；缺席标注", async () => {
    const { DimensionCard } = await import("@/components/bench/ScoreDetail");
    render(
      <DimensionCard
        dim="需求兑现"
        value={3.5}
        judgeGroup={judgeGroup}
        onJump={() => {}}
      />,
    );
    // 分歧高亮（5 vs 2 极差 3 ≥2）：分数徽章带不稳定类（DEC-007）
    const scoreBadge = document.querySelector(".bench-dim-score");
    expect(scoreBadge).toBeTruthy();
    expect(scoreBadge!.classList.contains("bench-seed-unstable")).toBe(true);
    expect(scoreBadge!.textContent).toContain("3.5/5");
    expect(scoreBadge!.getAttribute("title")).toContain("glm: 5");
    expect(scoreBadge!.getAttribute("title")).toContain("deepseek: 缺席");
    // per-judge 分组默认折叠（DEC-006）：3 个折叠块均在
    const blocks = document.querySelectorAll(".bench-judge-block");
    expect(blocks.length).toBe(3);
    // 缺席标注（DEC-003）
    expect(screen.getByText(/缺席（评分失败，未计入均分）/)).toBeInTheDocument();
    // 展开一个 judge 看到其两段理由
    blocks[1].querySelector("summary")!.dispatchEvent(new MouseEvent("click"));
    expect(await screen.findByText("kimi 达标理由")).toBeInTheDocument();
    expect(screen.getByText("kimi 不足理由")).toBeInTheDocument();
  });

  it("极差 <2 不高亮；旧数据（无 judgeGroup）走原两段理由路径（AC-007 回归）", async () => {
    const { DimensionCard } = await import("@/components/bench/ScoreDetail");
    const { container } = render(
      <div>
        <DimensionCard
          dim="设定自洽"
          value={3.0}
          judgeGroup={{
            group_id: 1, group_name: "二人组",
            members: [
              { config_id: 1, name: "a", model: "m1", scores: { 设定自洽: 3 }, reasons: { 设定自洽: { 达标: ["x"], 不足: ["y"] } } },
              { config_id: 2, name: "b", model: "m2", scores: { 设定自洽: 3 }, reasons: { 设定自洽: { 达标: ["x"], 不足: ["y"] } } },
            ],
          }}
          onJump={() => {}}
        />
        <DimensionCard
          dim="人物塑造"
          value={4}
          reason={{ 达标: ["旧数据达标"], 不足: ["旧数据不足"] }}
          onJump={() => {}}
        />
      </div>,
    );
    const badges = container.querySelectorAll(".bench-dim-score");
    expect(badges[0]!.classList.contains("bench-seed-unstable")).toBe(false);
    // 旧路径：无 judge 分组块，理由直接展开
    expect(container.querySelectorAll(".bench-judge-block").length).toBe(2); // 仅组卡有
    expect(screen.getByText("旧数据达标")).toBeInTheDocument();
    expect(screen.getByText("旧数据不足")).toBeInTheDocument();
  });
});

// ── 评测组页（REQ-20260930-162207/FR-001/AC-001）──

describe("评测组页（AC-001）", () => {
  it("列表渲染组与成员；无组时引导创建", async () => {
    const judgeGroupModule = await import("@/pages/config/JudgeGroupPage");
    const JudgeGroupPage = judgeGroupModule.default;
    api.listJudgeGroups.mockResolvedValue({ groups: [] });
    api.listJudgeCandidates.mockResolvedValue({
      judges: [
        { config_id: 11, name: "glm", model: "glm-4.7", base_url: "http://x", scope: "eval", is_active: true, has_key: true, same_family_as_executor: false },
        { config_id: 22, name: "kimi", model: "kimi-k2", base_url: "http://y", scope: "evolution", is_active: false, has_key: true, same_family_as_executor: true },
      ],
      default: { scope: "eval", model: "glm-4.7", fingerprint: "fp", degraded: false },
    });
    render(
      <MemoryRouter>
        <JudgeGroupPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/还没有评测组/)).toBeInTheDocument();

    // 新建：选 2 个成员 + 同家族标记可见（DEC-011）
    fireEvent.click(screen.getByText("+ 新建评测组"));
    const boxes = await screen.findAllByRole("checkbox");
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);
    expect(screen.getByText(/⚠ 同家族/)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/三人评审团/), { target: { value: "评审团" } });
    api.createJudgeGroup.mockResolvedValue({ group_id: 1, status: "created" });
    api.listJudgeGroups.mockResolvedValue({
      groups: [{
        id: 1, name: "评审团",
        members: [
          { config_id: 11, name: "glm", model: "glm-4.7", scope: "eval", is_active: true, has_key: true, same_family_as_executor: false },
          { config_id: 22, name: "kimi", model: "kimi-k2", scope: "evolution", is_active: false, has_key: true, same_family_as_executor: true },
        ],
      }],
    });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => expect(api.createJudgeGroup).toHaveBeenCalled());
    expect(api.createJudgeGroup.mock.calls[0][0]).toEqual({
      name: "评审团", member_config_ids: [11, 22],
    });
    expect(await screen.findByText("2 人")).toBeInTheDocument();
  });
});

// ── AC-012：无权降级 ──

describe("DeliveriesView 降级（AC-012）", () => {
  it("无权时条目可见 + 无权徽章，不显示空白", async () => {
    const { DeliveriesView } = await import("@/components/bench/DeliveriesView");
    api.getBenchmarkDeliveries.mockResolvedValue({
      trace_id: "t1", can_read_content: false,
      groups: [{
        display: "主线 storyline",
        files: [{ logical_key: "storyline.md", content_hash: "h", artifact_revision_id: "r1", size_bytes: 10, expires_at: null, available: true }],
      }],
    });
    render(<DeliveriesView traceId="t1" />);
    await waitFor(() => expect(screen.getByText("主线 storyline")).toBeInTheDocument());
    expect(screen.getByText("无权")).toBeInTheDocument();
    expect(screen.getByText(/无权查看正文/)).toBeInTheDocument();
  });
});

// ── AC-001（REQ-20260923-131103）：产物正文 Markdown 渲染 ──

describe("产物正文 Markdown 渲染（FR-001）", () => {
  const FILE = {
    logical_key: "storyline.md", content_hash: "h",
    artifact_revision_id: "r1", size_bytes: 10, expires_at: null, available: true,
  };

  async function openDeliveries(content: unknown) {
    const { DeliveriesView } = await import("@/components/bench/DeliveriesView");
    api.getBenchmarkDeliveries.mockResolvedValue({
      trace_id: "t1", can_read_content: true,
      groups: [{ display: "主线 storyline", files: [FILE] }],
    });
    api.getArtifactRevisionContent.mockResolvedValue({
      artifact_revision_id: "r1", content,
    });
    render(<DeliveriesView traceId="t1" />);
    fireEvent.click(await screen.findByText("storyline.md"));
  }

  it("内容为 {content: md} 对象时剥壳渲染 Markdown（线上实际形状）", async () => {
    await openDeliveries({ content: "# 主线大纲\n\n|甲|乙|\n|---|---|\n|1|2|" });
    expect(
      await screen.findByRole("heading", { level: 1, name: "主线大纲" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByText(/content/)).toBeNull();
  });

  it("内容为裸字符串时照常渲染（回归）", async () => {
    await openDeliveries("# 裸字符串标题");
    expect(
      await screen.findByRole("heading", { level: 1, name: "裸字符串标题" }),
    ).toBeInTheDocument();
  });

  it("内容为其他对象时回退 JSON 展示，不白屏", async () => {
    await openDeliveries({ foo: "bar" });
    await waitFor(() => expect(screen.getByText(/foo/)).toBeInTheDocument());
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });
});

// ── FR-002（REQ-20260923-131103）：批次列表数据集列/均分列/加载更多 ──

describe("规则页（AC-013）", () => {
  it("渲染版本、校准状态、纪律条款、五维卡片五档锚点与评分制说明", async () => {
    api.getBenchmarkRubric.mockResolvedValue({
      rubric_version: "v4-outline-5dim-anchored",
      calibration_status: "uncalibrated",
      anchor_status: "draft",
      discipline_rules: [
        "证据先行：只依据可指认的原文判定。",
        "先列缺陷再定档：缺陷清点未完成不得打分。",
        "高分举证：给 4 分及以上必须逐条论证。",
        "5 分稀缺：存在任何可指认缺陷即不得给 5 分。",
        "禁止整体印象迁移：不得因整体感觉好而抬分。",
        "两可取低：两档之间犹豫时取低档。",
      ],
      dimensions: [
        {
          key: "需求兑现", question: "大纲是否兑现需求？",
          anchors: { "5": "全兑现", "4": "轻微弱化一处", "3": "个别落空", "2": "一条整条落空", "1": "偏离" },
        },
        {
          key: "设定自洽", question: "设定是否自洽？",
          anchors: { "5": "自洽", "4": "一处边界模糊", "3": "小出入", "2": "多处矛盾", "1": "矛盾" },
        },
      ],
      rule_delivery: { key: "delivery_complete", description: "三件套齐全且无占位符" },
    });
    render(
      <MemoryRouter>
        <Rules />
      </MemoryRouter>,
    );
    expect(await screen.findByText("v4-outline-5dim-anchored")).toBeInTheDocument();
    expect(screen.getByText("需求兑现")).toBeInTheDocument();
    expect(screen.getByText("大纲是否兑现需求？")).toBeInTheDocument();
    // 五档锚点逐档渲染（DEC-001）
    for (const text of ["全兑现", "轻微弱化一处", "个别落空", "一条整条落空", "偏离"]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    // 纪律条款区（DEC-016）
    expect(screen.getByText("评分纪律（judge 每次评分必须遵守）")).toBeInTheDocument();
    expect(screen.getByText(/两可取低/)).toBeInTheDocument();
    expect(screen.getByText(/评分制说明/)).toBeInTheDocument();
    // 两段理由说明（DEC-002）
    expect(screen.getByText(/两段式理由/)).toBeInTheDocument();
  });
});

// ── AC-014：数据集页 ──

describe("数据集页（AC-014）", () => {
  it("golden 输入与 revision 保留，旧版重跑入口移除", async () => {
    api.getDatasetCases.mockResolvedValue({
      cases: [{ case_id: "case-001", title: "热血升级", layer: "golden", source_trace_id: null, demand_revision: "r", has_reference: false, promoted_at: null, created_by: "manual" }],
      total: 1,
    });
    api.getGoldenRevision.mockResolvedValue({
      revision: "rev1234567890", locked: true, intact: true, case_count: 1, cases: ["case-001"],
    });
    render(
      <MemoryRouter>
        <Dataset />
      </MemoryRouter>,
    );
    expect(await screen.findByText("case-001")).toBeInTheDocument();
    expect(screen.getByText("已锁定")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/粘贴新 case 的 demand.md 全文/)).toBeInTheDocument();
    expect(screen.queryByText("重跑最近 3 个版本")).not.toBeInTheDocument();
  });
});

// ── AC-016：产物库三级下钻 ──


describe("旧版评测入口退役 / REQ-20261108-191146", () => {
  it.each([Artifacts, BatchDetail])("旧地址只说明退役并提供实验记录入口，不读取旧历史", async (Page) => {
    render(<MemoryRouter><Page /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "旧版批次评测已下线" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看实验记录" })).toHaveAttribute("href", "/bench/experiments");
    expect(api.listBenchmarkBatches).not.toHaveBeenCalled();
    expect(api.getBenchmarkReport).not.toHaveBeenCalled();
  });
  it("工作台保留当前实验入口，移除旧历史启动和趋势", async () => {
    render(<MemoryRouter><Workbench /></MemoryRouter>);
    await screen.findByRole("heading", { name: "正在运行的实验" });
    expect(screen.getByRole("link", { name: "全部实验记录" })).toBeInTheDocument();
    expect(screen.queryByText("历史评测与版本趋势")).not.toBeInTheDocument();
    expect(screen.queryByText("触发全量评测")).not.toBeInTheDocument();
    expect(api.listBenchmarkBatches).not.toHaveBeenCalled();
  });
});
