/**
 * ScriptPanel（大纲全景）行为测试——FR-003/AC-003/004/009（REQ-20260930-163019）。
 *
 * - v2：页头「大纲全景」+ 故事核心 + 跨线全景表（七列、原 T 号、交汇全参与线）
 * - legacy：降级为按线分区块视图
 * - v2 解析失败（panorama 空）：降级渲染 markdown 原文，不白屏
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ScriptPanel } from "@/components/workspace/ScriptPanel";
import type { PanoramaEvent, StorylineEntry } from "@/lib/types";

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
