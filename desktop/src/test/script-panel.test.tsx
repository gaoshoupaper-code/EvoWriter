/**
 * ScriptPanel（大纲全景）行为测试——FR-003/AC-003/004/009（REQ-20260930-163019）
 * + FR-006 节奏区块（REQ-20261010-000638：张力曲线/爽点标记/许诺进度/降级）。
 *
 * - v2：页头「大纲全景」+ 故事核心 + 跨线全景表（七列、原 T 号、交汇全参与线）
 * - legacy：降级为按线分区块视图
 * - v2 解析失败（panorama 空）：降级渲染 markdown 原文，不白屏
 * - v2 + rhythm：SVG 曲线 + 爽点标记 + 许诺进度 + 曲线切换 + 点击联动全景表
 * - v2 无 rhythm（旧大纲）：显示「无节奏数据」降级提示（DEC-015）
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ScriptPanel } from "@/components/workspace/ScriptPanel";
import type { PanoramaEvent, RhythmData, StorylineEntry } from "@/lib/types";

vi.mock("react-markdown", () => ({
  // markdown 渲染对行为测试是黑盒：直接输出原文便于断言
  default: ({ children }: { children: string }) => <div data-testid="md">{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

const V2_MD = `# 故事核心

- **Logline**：测试

## 复仇线 · 主线 · 活跃

| 时序 | 事件 | 类型 | 阶段 | 地点 | 角色 | 交汇 | 描述 |
|------|------|------|------|------|------|------|------|
| T1 | 灭门之夜 | 冲突 | 发展 | 青云宗 | 林寒 | | 一夜之间家破人亡。 |
`;

const ENTRIES: StorylineEntry[] = [
  { filename: "storyline.md", title: "复仇线", markdown: "## 复仇线 · 主线 · 活跃" },
];

const PANORAMA: PanoramaEvent[] = [
  {
    t: "T2.5",
    name: "祭祖大典的闯入",
    type: "冲突",
    storylines: ["复仇线", "感情线"],
    characters: "林寒",
    location: "青云宗",
    desc: "当众闯坛，两线同时改写。",
  },
];

describe("ScriptPanel 大纲全景（FR-003）", () => {
  it("AC-003：v2 显示页头「大纲全景」+ 故事核心 + 全景表（原 T 号、交汇全参与线）", () => {
    render(
      <ScriptPanel
        storylineMarkdown={V2_MD}
        storylineEntries={ENTRIES}
        storylinePanorama={PANORAMA}
        storylineFormat="v2"
        activeStorylineFilename=""
        onSelectStoryline={() => {}}
      />,
    );
    expect(screen.getByRole("heading", { name: "大纲全景" })).toBeInTheDocument();
    expect(screen.getByText("故事核心")).toBeInTheDocument();
    expect(screen.getByText(/Logline/)).toBeInTheDocument();

    const table = screen.getByRole("table");
    const header = within(table).getAllByRole("columnheader").map((th) => th.textContent);
    expect(header).toEqual(["时序", "所属线", "事件", "类型", "角色", "地点", "描述"]);
    const row = within(table).getAllByRole("row")[1];
    expect(row.textContent).toContain("T2.5");
    expect(row.textContent).toContain("复仇线、感情线");
    expect(row.textContent).toContain("祭祖大典的闯入");
  });

  it("AC-003：点线名看单线区块——左栏导航保留", () => {
    render(
      <ScriptPanel
        storylineMarkdown={V2_MD}
        storylineEntries={ENTRIES}
        storylinePanorama={PANORAMA}
        storylineFormat="v2"
        activeStorylineFilename="复仇线"
        onSelectStoryline={() => {}}
      />,
    );
    expect(screen.getByText(/复仇线 · 主线 · 活跃/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("AC-004：legacy 降级为按线分区块视图（无全景表、不报错）", () => {
    render(
      <ScriptPanel
        storylineMarkdown="# 旧索引"
        storylineEntries={[{ filename: "S01-主线.md", title: "S01-主线", markdown: "### S01-主线 [主线]" }]}
        storylinePanorama={[]}
        storylineFormat="legacy"
        activeStorylineFilename=""
        onSelectStoryline={() => {}}
      />,
    );
    expect(screen.getByRole("heading", { name: "大纲全景" })).toBeInTheDocument();
    // legacy：左栏仍是故事线列表，右栏显示该线内容，无全景表
    expect(screen.getByText(/S01-主线/)).toBeInTheDocument();
    expect(screen.queryByText("跨线全景")).not.toBeInTheDocument();
  });

  it("AC-009：v2 解析失败（panorama 空）降级渲染 markdown 原文，不白屏", () => {
    render(
      <ScriptPanel
        storylineMarkdown={V2_MD}
        storylineEntries={[]}
        storylinePanorama={[]}
        storylineFormat="v2"
        activeStorylineFilename=""
        onSelectStoryline={() => {}}
      />,
    );
    expect(screen.getByText(/Logline/)).toBeInTheDocument();
    expect(screen.getByText(/灭门之夜/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

// ── 节奏区块（REQ-20261010-000638 FR-006 / AC-006）────────────────

const RHYTHM: RhythmData = {
  mainline: [
    { t: "T1", name: "灭门之夜", tension: 5, payoff: "—", line: "复仇线" },
    { t: "T2.5", name: "祭祖大典的闯入", tension: 4, payoff: "小", line: "复仇线" },
    { t: "T9", name: "新教父立威", tension: 5, payoff: "大", line: "复仇线" },
    // 旧版本区块事件：无张力标注（混合 schema，DEC-015）——应显示缺张力提示
    { t: "T10", name: "旧事件无张力", tension: null, payoff: "", line: "复仇线" },
  ],
  synthesis: [
    { t: "T1", name: "灭门之夜", tension: 5, payoff: "—", line: "复仇线" },
    { t: "T2.5", name: "祭祖大典的闯入", tension: 4, payoff: "小", line: "复仇线" },
    { t: "T9", name: "新教父立威", tension: 5, payoff: "大", line: "复仇线" },
  ],
  dark: {
    暗流线: [
      { t: "T3", name: "暗流涌动", tension: 4, payoff: "", line: "暗流线" },
      { t: "T5", name: "浮出水面", tension: 5, payoff: "", line: "暗流线", surface: true },
    ],
  },
  shape_slots: [
    { slot: "首事件", op: "≈", values: [2], twin_peak: false },
    { slot: "前段末", op: ">=", values: [4], twin_peak: false },
    { slot: "中点谷", op: "<=", values: [2], twin_peak: false },
    { slot: "终局", op: ">=", values: [5], twin_peak: true },
  ],
  promises: [
    {
      id: "P1",
      text: "查清灭门真相",
      level: "主线大期待",
      line: "全局",
      status: "推进中",
      promise_events: ["灭门之夜"],
      progress_events: ["祭祖大典的闯入"],
      payoff_events: [],
      note: "",
    },
  ],
};

describe("ScriptPanel 节奏区块（FR-006）", () => {
  it("AC-006：v2 + rhythm 渲染曲线、爽点标记、目标形态与许诺进度，可切换曲线", () => {
    render(
      <ScriptPanel
        storylineMarkdown={V2_MD}
        storylineEntries={ENTRIES}
        storylinePanorama={PANORAMA}
        storylineRhythm={RHYTHM}
        storylineFormat="v2"
        activeStorylineFilename=""
        onSelectStoryline={() => {}}
      />,
    );
    // SVG 曲线 + 爽点标记（小圆点/大菱形）+ 形态槽位带标签
    expect(screen.getByRole("img", { name: /张力曲线/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新教父立威（大爽/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /祭祖大典的闯入（小爽/ })).toBeInTheDocument();
    // 目标形态（工具栏摘要 + 槽位带各一处）
    const shapeLabel = screen.getByText(/目标形态：/);
    expect(shapeLabel.textContent).toContain("终局双峰5");
    // 暗线图例（曲线路径 title 与图例文本各一处）+ 浮出时点标记（FR-006）
    expect(screen.getAllByText(/暗流线/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/浮出时点/).length).toBeGreaterThan(0);
    // 混合 schema：无张力事件提示（评审修复：不静默丢弃）
    expect(screen.getByLabelText("缺张力提示").textContent).toContain("1 个事件无张力标注");
    // 许诺进度表
    const tables = screen.getAllByRole("table");
    const promiseTable = tables.find((t) => t.getAttribute("aria-label") === "许诺进度");
    expect(promiseTable).toBeDefined();
    expect(within(promiseTable!).getAllByRole("row")[1].textContent).toContain("查清灭门真相");
    // 切换到全局合成
    fireEvent.click(screen.getByRole("button", { name: "全局合成" }));
    expect(screen.getByRole("button", { name: "全局合成" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("AC-006：点曲线上的点联动全景表滚动定位事件", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    render(
      <ScriptPanel
        storylineMarkdown={V2_MD}
        storylineEntries={ENTRIES}
        storylinePanorama={PANORAMA}
        storylineRhythm={RHYTHM}
        storylineFormat="v2"
        activeStorylineFilename=""
        onSelectStoryline={() => {}}
      />,
    );
    // 点曲线上的点 → 联动定位全景表中同名事件行（PANORAMA 含「祭祖大典的闯入」）
    fireEvent.click(screen.getByRole("button", { name: /祭祖大典的闯入，张力4/ }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("AC-006/DEC-015：v2 旧大纲无 rhythm → 显示中性降级提示，全景表不受影响", () => {
    render(
      <ScriptPanel
        storylineMarkdown={V2_MD}
        storylineEntries={ENTRIES}
        storylinePanorama={PANORAMA}
        storylineRhythm={null}
        storylineFormat="v2"
        activeStorylineFilename=""
        onSelectStoryline={() => {}}
      />,
    );
    expect(screen.getByLabelText("无节奏数据提示")).toBeInTheDocument();
    expect(screen.getByLabelText("无节奏数据提示").textContent).not.toContain("重新构建");
    expect(screen.getByRole("table")).toBeInTheDocument(); // 全景表照常
    expect(screen.queryByRole("img", { name: /张力曲线/ })).not.toBeInTheDocument();
  });

  it("legacy 视图不渲染节奏区块", () => {
    render(
      <ScriptPanel
        storylineMarkdown="# 旧索引"
        storylineEntries={[{ filename: "S01-主线.md", title: "S01-主线", markdown: "### S01-主线 [主线]" }]}
        storylinePanorama={[]}
        storylineRhythm={RHYTHM}
        storylineFormat="legacy"
        activeStorylineFilename=""
        onSelectStoryline={() => {}}
      />,
    );
    expect(screen.queryByLabelText("节奏区块")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /张力曲线/ })).not.toBeInTheDocument();
  });
});
