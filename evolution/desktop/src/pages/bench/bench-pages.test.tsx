/**
 * 评测板块组件测试（REQ-20260921-135543）。
 *
 * 覆盖 AC 的可自动化部分：
 * - AC-001 侧栏「评测」分组 4 入口、旧入口移除（Shell）
 * - AC-003 case 子集勾选 → runBenchmark payload.case_ids（Workbench）
 * - AC-004/005 批次列表渲染 + 趋势 tab 五维（Workbench/LeaderboardTab）
 * - AC-007 低分条目点击直达 case 明细（BatchDetail + CaseRunsPanel focusCase）
 * - AC-008/009/010 评分区：概览/两段理由默认展开分色/交付跳转按钮/seed 极差高亮
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
import type { BenchmarkBatchSummary, BenchmarkRunRow, GoldenRevision } from "@/lib/api";
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
  error: string | null; rubric_version: string;
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
    rubric_version: "v4-outline-5dim-anchored",
    ran_at: null,
    finished_at: null,
    scores: {
      rubric_version: "v4-outline-5dim-anchored",
      scores: { 需求兑现: 4, 设定自洽: 3, 人物塑造: 4, 情节构造: 3, 节奏结构: 3 },
      reasons: {
        需求兑现: { 达标: ["承诺点全部兑现"], 不足: ["配角 B 交代潦草"] },
        设定自洽: { 达标: ["力量体系闭环"], 不足: ["代价边界模糊"] },
        人物塑造: { 达标: ["主角动机清晰"], 不足: ["弧线后段偏快"] },
        情节构造: { 达标: ["因果链完整"], 不足: ["第二幕转折铺垫不足"] },
        节奏结构: { 达标: ["阶段划分清晰"], 不足: ["中段爽点密度偏低"] },
      },
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
  it("报告低分行点击直达对应 case 明细；两段理由默认展开分色；seed 极差高亮；旧格式理由兼容", async () => {
    api.getBenchmarkReport.mockResolvedValue({
      batch_id: "batch-1",
      status: "ok", calibration: "uncalibrated", anchor_status: "draft",
      dimensions: [{ dimension: "需求兑现", mean: 3.1, n: 6 }],
      low_cases: [{
        case_id: "case-002", seed: 1, overall: 2.4,
        scores: { 需求兑现: 2, 设定自洽: 2, 人物塑造: 3, 情节构造: 2, 节奏结构: 3 },
        rule_delivery_passed: false,
      }],
      failed_rows: [], rule_delivery_failed: 1,
    });
    api.listBenchmarkRuns.mockResolvedValue({
      batch_id: "batch-1",
      items: [
        // v3 旧格式行：字符串理由 → 「旧版理由」标签展示（FR-008 双轨兼容）
        makeRun({ id: 11, case_id: "case-002", seed: 1, scores: {
          rubric_version: "v3",
          scores: { 需求兑现: 2, 设定自洽: 2, 人物塑造: 3, 情节构造: 2, 节奏结构: 3 },
          reasons: { 设定自洽: "世界观规则前后冲突。" },
          overall: 2.4,
          rule_delivery: { key: "delivery_complete", passed: false, problems: ["大纲字数不足"] },
        } }),
        // v4 新格式行：两段对象理由默认展开（DEC-011）
        makeRun({ id: 12, case_id: "case-002", seed: 2 }),
        makeRun({ id: 13, case_id: "case-002", seed: 3, scores: {
          rubric_version: "v4-outline-5dim-anchored",
          scores: { 需求兑现: 4, 设定自洽: 4, 人物塑造: 4, 情节构造: 3, 节奏结构: 3 },
          reasons: {
            需求兑现: { 达标: ["承诺点兑现"], 不足: ["未发现不足"] },
            设定自洽: { 达标: ["规则闭环"], 不足: ["边界模糊"] },
            人物塑造: { 达标: ["动机清晰"], 不足: ["弧线偏快"] },
            情节构造: { 达标: ["因果完整"], 不足: ["铺垫不足"] },
            节奏结构: { 达标: ["结构清晰"], 不足: ["密度偏低"] },
          },
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
    // 默认选中 seed #1（v3 旧格式行）：字符串理由 → 「旧版理由」标签兼容渲染（FR-008）
    expect(await screen.findByText("旧版理由")).toBeInTheDocument();
    expect(screen.getByText(/世界观规则前后冲突。/)).toBeInTheDocument();
    // 切到 seed #2（v4 新格式行）：两段理由默认展开（DEC-011），无需点击；达标/不足分色块均在
    // （case 卡片区与 seed 对比表头都含 "#2"，在抽屉 seed 行内精确定位）
    const drawer = document.querySelector(".bench-case-sheet") as HTMLElement;
    const seedRow = drawer.querySelector(".bench-seed-row-drawer") as HTMLElement;
    fireEvent.click(within(seedRow).getByText("#2"));
    expect(await screen.findAllByText("✓ 达标")).toHaveLength(5);
    expect(screen.getAllByText("⚠ 不足")).toHaveLength(5);
    expect(screen.getByText("承诺点全部兑现")).toBeInTheDocument();
    expect(screen.getByText("配角 B 交代潦草")).toBeInTheDocument();
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
      dimensions: [], low_cases: [], failed_rows: [],
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

describe("产物库批次列表（FR-002）", () => {
  function makeBatch(over: Partial<BenchmarkBatchSummary> = {}) {
    return {
      batch_id: "batch-aaa-bbbb", status: "done",
      progress: { done: 3, total: 3, failed: 0, active: 0 },
      harness_version: 7, rubric_version: "v4", concurrency: 3, judge_fp: "jfp",
      triggered_at: null, golden_revision: "abcdef123456",
      avg_overall: 3.5,
      ...over,
    };
  }

  it("显示数据集指纹与均分；无 golden_revision/无 done 显示 —", async () => {
    api.listBenchmarkBatches.mockResolvedValue({
      total: 2,
      batches: [
        makeBatch(),
        makeBatch({ batch_id: "batch-ccc-dddd", golden_revision: null, avg_overall: null }),
      ],
    });
    render(
      <MemoryRouter>
        <Artifacts />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("golden @ abcdef1")).toBeInTheDocument());
    expect(screen.getByText("3.50")).toBeInTheDocument();
    const dashes = await screen.findAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(2); // 数据集 + 均分各一个 —
  });

  it("total 超过已加载数时出现加载更多，点击以更大 limit 重拉", async () => {
    api.listBenchmarkBatches.mockImplementation(async (limit?: number) => ({
      total: 25,
      batches: Array.from({ length: Math.min(limit ?? 20, 25) }, (_, i) =>
        makeBatch({ batch_id: `batch-${String(i).padStart(4, "0")}-xxxx` })),
    }));
    render(
      <MemoryRouter>
        <Artifacts />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getAllByText("golden @ abcdef1").length).toBeGreaterThan(0),
    );
    const more = screen.getByRole("button", { name: /加载更多/ });
    fireEvent.click(more);
    await waitFor(() =>
      expect(api.listBenchmarkBatches).toHaveBeenLastCalledWith(40),
    );
  });
});

// ── FR-003（REQ-20260923-131103）：case 层标题与排序 ──

describe("产物库 case 层（FR-003）", () => {
  it("主显标题 + case_id 小字；无均分置前、其余均分升序", async () => {
    api.listBenchmarkBatches.mockResolvedValue({
      total: 1,
      batches: [{
        batch_id: "batch-aaa-bbbb", status: "done",
        progress: { done: 5, total: 5, failed: 0, active: 0 },
        harness_version: 7, rubric_version: "v4", concurrency: 3, judge_fp: "jfp",
        triggered_at: null, golden_revision: "rev", avg_overall: 3.5,
      }],
    });
    api.listBenchmarkRuns.mockResolvedValue({
      batch_id: "batch-aaa-bbbb",
      items: [
        // case-001 均分 3.0；case-003 均分 4.5；case-002 全失败（无均分、无标题）
        makeRun({ id: 1, case_id: "case-001", seed: 1, scores: { ...makeRun().scores!, overall: 3.0 } }),
        makeRun({ id: 3, case_id: "case-003", seed: 1, scores: { ...makeRun().scores!, overall: 4.5 } }),
        makeRun({ id: 5, case_id: "case-002", seed: 1, status: "failed", scores: null, trace_id: null, error: "boom" }),
      ],
      total: 3,
    });
    api.getDatasetCases.mockResolvedValue({
      cases: [
        { case_id: "case-001", title: "穿越者复仇记", layer: "golden", source_trace_id: null, demand_revision: null, promoted_at: null, created_by: "x", has_reference: false },
        { case_id: "case-003", title: "废土拾荒指南", layer: "golden", source_trace_id: null, demand_revision: null, promoted_at: null, created_by: "x", has_reference: false },
      ],
      total: 2,
    });

    const { container } = render(
      <MemoryRouter>
        <Artifacts />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("batch-aa"));
    await waitFor(() =>
      expect(screen.getByText("穿越者复仇记")).toBeInTheDocument(),
    );
    const cards = container.querySelectorAll(".bench-case-card");
    expect(cards.length).toBe(3);
    // 排序：无均分（case-002）→ 3.0（case-001）→ 4.5（case-003）
    expect(cards[0].textContent).toContain("case-002");
    expect(cards[1].textContent).toContain("穿越者复仇记");
    expect(cards[2].textContent).toContain("废土拾荒指南");
    // 标题主显时 case_id 降为小字副标（class 存在性）
    expect(cards[1].querySelector(".bench-case-id-sub")).not.toBeNull();
  });
});

// ── FR-004/005/006（REQ-20260923-131103）：seed 详情分栏 + 需求折叠条 ──

describe("产物库 seed 详情（FR-004/005/006）", () => {
  // golden 可覆写指纹一致态；caseContentError 覆写需求拉取失败态（AC-005 边界）
  type SeedPageOpts = { golden?: GoldenRevision; caseContentError?: Error };

  function mockSeedPage(items: ReturnType<typeof makeRun>[], opts: SeedPageOpts = {}) {
    api.listBenchmarkBatches.mockResolvedValue({
      total: 1,
      batches: [{
        batch_id: "batch-aaa-bbbb", status: "done",
        progress: { done: items.length, total: items.length, failed: 0, active: 0 },
        harness_version: 7, rubric_version: "v4", concurrency: 3, judge_fp: "jfp",
        triggered_at: null, golden_revision: "rev-old", avg_overall: 3.5,
      }],
    });
    api.listBenchmarkRuns.mockResolvedValue({
      batch_id: "batch-aaa-bbbb", items, total: items.length,
    });
    api.getBenchmarkDeliveries.mockResolvedValue({
      trace_id: "trace-1", can_read_content: true,
      groups: [{ display: "人物 character", files: [] }],
    });
    api.getGoldenRevision.mockResolvedValue(
      opts.golden ?? {
        revision: "rev-new", locked: true, intact: true, case_count: 6, cases: [],
      },
    );
    if (opts.caseContentError) {
      api.getCaseContent.mockRejectedValue(opts.caseContentError);
    } else {
      api.getCaseContent.mockResolvedValue({
        case_id: "case-001", title: "穿越者复仇记", layer: "golden",
        demand_md: "# 需求标题\n\n主角必须完成复仇。",
        reference_md: null, source_trace_id: null, demand_revision: "rev-new",
        promoted_at: null, created_by: "x", status: "active",
      });
    }
  }

  async function renderToSeed(
    items: ReturnType<typeof makeRun>[],
    opts: SeedPageOpts = {},
  ) {
    mockSeedPage(items, opts);
    const view = render(
      <MemoryRouter>
        <Artifacts />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("batch-aa"));
    fireEvent.click(await screen.findByText("case-001"));
    fireEvent.click(await screen.findByText("#1"));
    // 等产物区出现
    await screen.findByText("人物 character");
    return view;
  }

  it("左右分栏：左评分（五维条+维度卡片+seed对比折叠）右产物", async () => {
    const { container } = await renderToSeed([
      makeRun({ id: 1, case_id: "case-001", seed: 1 }),
      makeRun({ id: 2, case_id: "case-001", seed: 2 }),
    ]);
    const split = container.querySelector(".bench-run-split");
    expect(split).not.toBeNull();
    expect(split!.querySelector(".bench-run-score .bench-score-overview")).not.toBeNull();
    expect(split!.querySelectorAll(".bench-dim-card").length).toBe(5);
    // seed 对比默认折叠：按钮在、表不在
    expect(screen.getByRole("button", { name: /seed 对比/ })).toBeInTheDocument();
    expect(container.querySelector(".bench-seed-compare")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /seed 对比/ }));
    expect(container.querySelector(".bench-seed-compare")).not.toBeNull();
    // 右栏含产物
    expect(split!.querySelector(".bench-run-product .bench-delivery")).not.toBeNull();
  });

  it("需求条默认收起；展开渲染 demand.md；指纹不一致显示提示", async () => {
    await renderToSeed([makeRun({ id: 1, case_id: "case-001", seed: 1 })]);
    // 默认收起：正文未渲染
    expect(screen.queryByRole("heading", { level: 1, name: "需求标题" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /需求（demand）/ }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "需求标题" }),
    ).toBeInTheDocument();
    // 批次 rev-old ≠ 当前 rev-new → 提示条
    expect(screen.getByText(/数据集已更新/)).toBeInTheDocument();
  });

  it("旧 rubric 行：评分区「明细不可用」，产物区照常（FR-006）", async () => {
    await renderToSeed([
      makeRun({
        id: 1, case_id: "case-001", seed: 1,
        rubric_version: "v3", scores: null,
      }),
    ]);
    expect(screen.getByText(/明细不可用/)).toBeInTheDocument();
    expect(screen.getByText("人物 character")).toBeInTheDocument();
  });

  it("进行中批次（全部无均分）case 按 id 稳定排序（AC-003 边界）", async () => {
    mockSeedPage([
      makeRun({ id: 2, case_id: "case-002", seed: 1, status: "running", scores: null, trace_id: null }),
      makeRun({ id: 1, case_id: "case-001", seed: 1, status: "pending", scores: null, trace_id: null }),
    ]);
    const { container } = render(
      <MemoryRouter>
        <Artifacts />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("batch-aa"));
    await waitFor(() =>
      expect(container.querySelectorAll(".bench-case-card").length).toBe(2),
    );
    const cards = container.querySelectorAll(".bench-case-card");
    expect(cards[0].textContent).toContain("case-001");
    expect(cards[1].textContent).toContain("case-002");
  });

  it("需求指纹一致无提示；拉取失败不阻断产物（AC-005 边界）", async () => {
    await renderToSeed(
      [makeRun({ id: 1, case_id: "case-001", seed: 1 })],
      {
        golden: { revision: "rev-old", locked: true, intact: true, case_count: 6, cases: [] },
        caseContentError: new Error("接口 500"),
      },
    );
    fireEvent.click(screen.getByRole("button", { name: /需求（demand）/ }));
    expect(await screen.findByText(/读取需求失败/)).toBeInTheDocument();
    expect(screen.queryByText(/数据集已更新/)).toBeNull();
    // 产物区不受需求失败影响
    expect(screen.getByText("人物 character")).toBeInTheDocument();
  });
});

// ── AC-013：规则页 ──

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
