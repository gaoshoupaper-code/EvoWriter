/**
 * 评测板块组件测试（REQ-20260921-135543）。
 *
 * 覆盖 AC 的可自动化部分：
 * - AC-001 侧栏「评测」分组 4 入口、旧入口移除（Shell）
 * - AC-003 case 子集勾选 → runBenchmark payload.case_ids（Workbench）
 * - AC-004/005 批次列表渲染 + 趋势 tab 五维（Workbench/LeaderboardTab）
 * - AC-007 低分条目点击直达 case 明细（BatchDetail + CaseRunsPanel focusCase）
 * - AC-008/009/010 评分区：概览/维度卡片理由折叠/交付跳转按钮/seed 极差高亮
 * - AC-012 三件套降级徽章（DeliveriesView）
 * - AC-013 规则页五维卡片（Rules）
 * - AC-014 数据集 golden 列表 + rerun（Dataset）
 * - AC-016 产物库三级下钻（Artifacts）
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
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

function makeRun(overrides: Partial<{
  id: number; case_id: string; seed: number | null; status: string;
  scores: BenchmarkRunRow["scores"]; trace_id: string | null;
}> = {}): BenchmarkRunRow {
  return {
    id: 1,
    case_id: "case-001",
    seed: 1,
    harness_version: 7,
    trace_id: "trace-1",
    status: "done",
    retries: 0,
    error: null,
    rubric_version: "v3",
    ran_at: null,
    finished_at: null,
    scores: {
      rubric_version: "v3",
      scores: { 需求兑现: 4, 设定自洽: 3, 人物塑造: 4, 情节构造: 3, 节奏结构: 3 },
      tags: {},
      reasons: { 需求兑现: "核心需求全部兑现。" },
      overall: 3.4,
      rule_delivery: { key: "delivery_complete", passed: true, problems: [] },
    },
    ...overrides,
  };
}

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
  api.getDatasetCases.mockResolvedValue({ cases: [], total: 0 });
}

beforeAll(() => {
  // jsdom 无 scrollIntoView（DeliveriesView 定位效果依赖）
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  vi.clearAllMocks();
  setupCommonMocks();
});

// ── AC-001：侧栏评测分组 ──

describe("Shell 导航（AC-001）", () => {
  it("评测分组含 4 入口；旧评测/数据集入口不在原分组", async () => {
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>,
    );
    for (const label of ["评测工作台", "评测规则", "评测数据集", "评测产物"]) {
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

describe("工作台（AC-003/004/005）", () => {
  it("勾选 case 子集后触发，payload 携带所选 case_ids", async () => {
    api.getDatasetCases.mockResolvedValue({
      cases: [
        { case_id: "case-001", title: "热血升级", layer: "golden", source_trace_id: null, demand_revision: null, promoted_at: null, created_by: "manual", has_reference: false },
        { case_id: "case-002", title: "凡人流", layer: "golden", source_trace_id: null, demand_revision: null, promoted_at: null, created_by: "manual", has_reference: false },
      ],
      total: 2,
    });
    api.runBenchmark.mockResolvedValue({ batch_id: "b-new", status: "running", progress: { total: 2, done: 0, failed: 0, active: 2 }, golden_revision: "rev" });

    render(
      <MemoryRouter>
        <Workbench />
      </MemoryRouter>,
    );

    // 打开勾选器
    fireEvent.click(await screen.findByText(/选择 case 子集/));
    const boxes = await screen.findAllByRole("checkbox");
    fireEvent.click(boxes[0]); // 勾 case-001

    fireEvent.click(screen.getByText(/触发评测/));
    await waitFor(() => expect(api.runBenchmark).toHaveBeenCalled());
    const payload = api.runBenchmark.mock.calls[0][0];
    expect(payload.case_ids).toEqual(["case-001"]);
  });

  it("不勾选触发 = 全量（payload 无 case_ids）", async () => {
    api.runBenchmark.mockResolvedValue({ batch_id: "b2", status: "running", progress: { total: 6, done: 0, failed: 0, active: 6 }, golden_revision: "rev" });
    render(
      <MemoryRouter>
        <Workbench />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText(/触发全量评测/));
    await waitFor(() => expect(api.runBenchmark).toHaveBeenCalled());
    expect(api.runBenchmark.mock.calls[0][0].case_ids).toBeUndefined();
  });

  it("批次列表渲染进度，趋势 tab 渲染五维均分列", async () => {
    api.listBenchmarkBatches.mockResolvedValue({
      batches: [{
        batch_id: "batch-xyz-1111", status: "done",
        progress: { done: 12, total: 12, failed: 0, active: 0 },
        harness_version: 8, rubric_version: "v3", concurrency: 3, judge_fp: "jfp",
        triggered_at: "2026-09-21T10:00:00", golden_revision: "rev",
      }],
    });
    api.getLeaderboard.mockResolvedValue({
      revision: "rev123",
      versions: [{
        version: 8, case_count: 2, avg_score: 3.67,
        dimension_means: { 需求兑现: 4.0, 设定自洽: 3.2 },
        cases: [],
      }],
      case_count: 2,
    });

    render(
      <MemoryRouter>
        <Workbench />
      </MemoryRouter>,
    );

    // 批次列表（AC-004）：id 取 slice(0,8)
    expect(await screen.findByText("batch-xy")).toBeInTheDocument();
    expect(screen.getByText("12/12")).toBeInTheDocument();

    // 趋势 tab（AC-005）—— Radix Tabs 以 onMouseDown 切换
    fireEvent.mouseDown(screen.getByRole("tab", { name: "版本趋势" }));
    expect(await screen.findByText("3.670")).toBeInTheDocument();
    expect(screen.getByText("需求兑现")).toBeInTheDocument();
    expect(screen.getByText("4.00")).toBeInTheDocument();
  });
});

// ── AC-006/007 + 008/009/010：批次详情 + 评分区 ──

describe("批次详情（AC-006/007/008/009/010）", () => {
  it("报告低分行点击直达对应 case 明细；评分区含概览/折叠理由/交付跳转；seed 极差高亮", async () => {
    api.getBenchmarkReport.mockResolvedValue({
      batch_id: "batch-1",
      status: "ok", calibration: "uncalibrated", anchor_status: "draft",
      dimensions: [{ dimension: "需求兑现", mean: 3.1, n: 6 }],
      tag_hits: [{ tag: "设定前后矛盾", hits: 2 }],
      low_cases: [{
        case_id: "case-002", seed: 1, overall: 2.4,
        scores: { 需求兑现: 2, 设定自洽: 2, 人物塑造: 3, 情节构造: 2, 节奏结构: 3 },
        tags: { 设定自洽: ["设定前后矛盾"] }, rule_delivery_passed: false,
      }],
      failed_rows: [], rule_delivery_failed: 1,
    });
    api.listBenchmarkRuns.mockResolvedValue({
      batch_id: "batch-1",
      items: [
        makeRun({ id: 11, case_id: "case-002", seed: 1, scores: {
          rubric_version: "v3",
          scores: { 需求兑现: 2, 设定自洽: 2, 人物塑造: 3, 情节构造: 2, 节奏结构: 3 },
          tags: { 设定自洽: ["设定前后矛盾"] },
          reasons: { 设定自洽: "世界观规则前后冲突。" },
          overall: 2.4,
          rule_delivery: { key: "delivery_complete", passed: false, problems: ["大纲字数不足"] },
        } }),
        makeRun({ id: 12, case_id: "case-002", seed: 2 }),
        makeRun({ id: 13, case_id: "case-002", seed: 3, scores: {
          rubric_version: "v3",
          scores: { 需求兑现: 4, 设定自洽: 4, 人物塑造: 4, 情节构造: 3, 节奏结构: 3 },
          tags: {}, reasons: {},
          overall: 3.6,
          rule_delivery: { key: "delivery_complete", passed: true, problems: [] },
        } }),
      ],
      total: 3,
    });
    api.getBenchmarkDeliveries.mockResolvedValue({
      trace_id: "trace-1", can_read_content: true,
      groups: [{ display: "世界观 worldview", files: [] }],
    });

    render(
      <MemoryRouter initialEntries={["/bench/batches/batch-1"]}>
        <Routes>
          <Route path="/bench/batches/:batchId" element={<BatchDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    // 报告区块（AC-006）：挂路由后 batchId 生效，getBenchmarkReport 真实被调用
    await waitFor(() => expect(api.getBenchmarkReport).toHaveBeenCalledWith("batch-1"));
    expect(await screen.findByText("弱点报告")).toBeInTheDocument();

    // 低分行点击（AC-007）：从报告低分表内定位 case-002 行（区别于下方 case 卡片）
    const lowTable = await waitFor(() => {
      const el = document.querySelector(".bench-lowcases-table");
      expect(el).toBeTruthy();
      return el as HTMLElement;
    });
    fireEvent.click(within(lowTable).getByText("case-002"));
    await waitFor(() => {
      expect(screen.getByText(/行明细 · case-002/)).toBeInTheDocument();
    });
    // seed 对比表存在（AC-009）
    expect(screen.getByText(/seed 对比/)).toBeInTheDocument();
    // 极差 ≥2 的维度整行数值格高亮（需求兑现/设定自洽 2↔4）：2.0 格带不稳定样式
    const unstableCells = document.querySelectorAll(".bench-seed-unstable");
    expect(unstableCells.length).toBeGreaterThanOrEqual(2);
    const unstableTwo = screen
      .getAllByText("2.0")
      .find((el) => el.classList.contains("bench-seed-unstable"));
    expect(unstableTwo).toBeTruthy();
    // 五维概览（AC-008）
    expect(document.querySelectorAll(".bench-score-overview-row").length).toBeGreaterThanOrEqual(5);
    // 维度卡片：理由默认折叠、按钮存在（AC-008）
    expect(screen.queryByText("世界观规则前后冲突。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("展开评分理由"));
    expect(screen.getByText("世界观规则前后冲突。")).toBeInTheDocument();
    // 交付跳转按钮（AC-010）：设定自洽 → 世界观。点击后定位效果真实生效
    // （review rev1 finding 1 回归锚点：nonce 拼接曾使关键词永不匹配）
    fireEvent.click(screen.getByText("查看相关交付（世界观）"));
    await waitFor(() => {
      const focused = document.querySelector(".bench-delivery-focus");
      expect(focused).toBeTruthy();
      expect(focused!.textContent).toContain("世界观");
    });
    // 三件套空组降级（AC-012 缺失徽章）
    expect(await screen.findByText("缺失")).toBeInTheDocument();
  });
  it("A/B 对比样本不足（insufficient）时渲染 reason 不崩溃", async () => {
    // review rev1 finding 5：后端 insufficient 只回 verdict/reason/n_*，
    // 曾因统计字段必填声明 + toFixed 直接调用而白屏
    api.getBenchmarkReport.mockResolvedValue({
      batch_id: "batch-1", status: "ok", calibration: "uncalibrated", anchor_status: "draft",
      dimensions: [], tag_hits: [], low_cases: [], failed_rows: [],
    });
    api.listBenchmarkRuns.mockResolvedValue({ batch_id: "batch-1", items: [], total: 0 });
    api.listBenchmarkBatches.mockResolvedValue({
      batches: [{
        batch_id: "batch-2", status: "done",
        progress: { done: 1, total: 1, failed: 0, active: 0 },
        harness_version: 8, rubric_version: "v3", concurrency: 1, judge_fp: "jfp",
        triggered_at: null, golden_revision: "rev",
      }],
    });
    api.compareBatches.mockResolvedValue({
      comparable: true,
      total: { verdict: "insufficient", reason: "有效评分不足", n_candidate: 1, n_production: 1 },
    });

    render(
      <MemoryRouter initialEntries={["/bench/batches/batch-1"]}>
        <Routes>
          <Route path="/bench/batches/:batchId" element={<BatchDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText("版本对比（CI 三态）");

    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "batch-2" } });
    fireEvent.click(screen.getByText("对比"));

    expect(await screen.findByText("样本不足")).toBeInTheDocument();
    expect(screen.getByText(/有效评分不足/)).toBeInTheDocument();
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

// ── AC-013：规则页 ──

describe("规则页（AC-013）", () => {
  it("渲染版本、校准状态、五维卡片与评分制说明", async () => {
    api.getBenchmarkRubric.mockResolvedValue({
      rubric_version: "v3-outline-5dim-uncalibrated",
      calibration_status: "uncalibrated",
      anchor_status: "draft",
      low_score_threshold: 2,
      dimensions: [
        { key: "需求兑现", question: "大纲是否兑现需求？", anchors: { "5": "全兑现", "3": "大部分", "1": "偏离" }, defect_tags: ["核心冲突缺位"] },
        { key: "设定自洽", question: "设定是否自洽？", anchors: { "5": "自洽", "3": "小出入", "1": "矛盾" }, defect_tags: ["设定前后矛盾"] },
      ],
      rule_delivery: { key: "delivery_complete", description: "三件套齐全且无占位符" },
    });
    render(
      <MemoryRouter>
        <Rules />
      </MemoryRouter>,
    );
    expect(await screen.findByText("v3-outline-5dim-uncalibrated")).toBeInTheDocument();
    expect(screen.getByText("需求兑现")).toBeInTheDocument();
    expect(screen.getByText("大纲是否兑现需求？")).toBeInTheDocument();
    expect(screen.getByText(/核心冲突缺位/)).toBeInTheDocument();
    expect(screen.getByText(/评分制说明/)).toBeInTheDocument();
  });
});

// ── AC-014：数据集页 ──

describe("数据集页（AC-014）", () => {
  it("golden 列表 + revision 条 + 受控新增 + 重跑入口", async () => {
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
    expect(screen.getByText("重跑最近 3 个版本")).toBeInTheDocument();
  });
});

// ── AC-016：产物库三级下钻 ──

describe("产物库（AC-016）", () => {
  it("批次 → case → seed 三级下钻到产物视图", async () => {
    api.listBenchmarkBatches.mockResolvedValue({
      batches: [{
        batch_id: "batch-aaa-bbbb", status: "done",
        progress: { done: 3, total: 3, failed: 0, active: 0 },
        harness_version: 7, rubric_version: "v3", concurrency: 3, judge_fp: "jfp",
        triggered_at: null, golden_revision: "rev",
      }],
    });
    api.listBenchmarkRuns.mockResolvedValue({
      batch_id: "batch-aaa-bbbb",
      items: [makeRun({ id: 21, case_id: "case-001", seed: 1 })],
      total: 1,
    });
    api.getBenchmarkDeliveries.mockResolvedValue({
      trace_id: "trace-1", can_read_content: true,
      groups: [{ display: "人物 character", files: [] }],
    });

    render(
      <MemoryRouter>
        <Artifacts />
      </MemoryRouter>,
    );

    // 第一级：批次（id slice(0,8)）
    fireEvent.click(await screen.findByText("batch-aa"));
    // 第二级：case 卡片
    await waitFor(() => expect(screen.getByText("case-001")).toBeInTheDocument());
    fireEvent.click(screen.getByText("case-001"));
    // 第三级：seed chip → 产物视图（人物分组空 = 缺失徽章）
    await waitFor(() => expect(screen.getByText("#1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("#1"));
    expect(await screen.findByText("人物 character")).toBeInTheDocument();
    // 面包屑四段齐全
    expect(screen.getByText(/seed #1/)).toBeInTheDocument();
  });
});
