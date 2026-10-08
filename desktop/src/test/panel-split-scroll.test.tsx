/**
 * 面板双栏独立滚动 + 切条目回顶验收测试（FR-001/FR-002/FR-003，AC-001/AC-002/AC-003，
 * REQ-20261008-201413）。
 *
 * jsdom 无真实布局，几何（独立滚动/卡片贴合/细滚动条）由浏览器 harness 验证；
 * 本文件覆盖 DOM 结构契约 + 切换条目后内容列 scrollTop 归零（AC-003）。
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CharactersPanel } from "@/components/workspace/CharactersPanel";
import { ObjectsPanel } from "@/components/workspace/ObjectsPanel";
import { ScriptPanel } from "@/components/workspace/ScriptPanel";
import type { CharacterMarkdownFile, ObjectMarkdownFile, StorylineEntry, PanoramaEvent } from "@/lib/types";

const LONG = "很长的设定。".repeat(40);

const characters: CharacterMarkdownFile[] = [
  { filename: "lin.md", name: "林逐电", markdown: LONG },
  { filename: "zhao.md", name: "赵无咎", markdown: "只有三行。" },
  { filename: "kong.md", name: "空条目", markdown: "   " },
];

const objects: ObjectMarkdownFile[] = [
  { filename: "sword.md", name: "青冥剑", markdown: LONG },
  { filename: "dan.md", name: "凝神丹", markdown: "只有三行。" },
];

const storylineEntries: StorylineEntry[] = [
  { filename: "storyline.md", title: "主线", markdown: LONG },
  { filename: "storyline.md", title: "暗线", markdown: "只有三行。" },
];

const panorama: PanoramaEvent[] = [
  { t: "1", storylines: ["主线"], name: "事件一", type: "开局", characters: "林逐电", location: "北荒", desc: "描述" },
];

function renderCharacters(activeFilename: string, onSelect = vi.fn()) {
  return {
    ...render(
      <CharactersPanel characters={characters} activeFilename={activeFilename} loading={false} onSelectCharacter={onSelect} />,
    ),
    onSelect,
  };
}

describe("面板双栏结构契约（FR-001/FR-002）", () => {
  it("人物/物品面板：卡片包在独立内容列里，列表栏与内容列平级", () => {
    const { container } = renderCharacters("lin.md");
    const layout = container.querySelector(".character-layout");
    expect(layout).not.toBeNull();
    expect(layout?.querySelector(":scope > aside.character-sidebar")).not.toBeNull();
    const column = layout?.querySelector(":scope > .character-content");
    expect(column).not.toBeNull();
    expect(column?.querySelector("article.character-markdown")).not.toBeNull();
  });

  it("物品面板：同样结构", () => {
    const { container } = render(
      <ObjectsPanel objects={objects} activeFilename="sword.md" loading={false} onSelectObject={vi.fn()} />,
    );
    const column = container.querySelector(".character-layout > .character-content");
    expect(column?.querySelector("article.character-markdown")).not.toBeNull();
  });

  it("大纲面板：故事线内容包在独立内容列里", () => {
    const { container } = render(
      <ScriptPanel
        storylineMarkdown="# 核心\n\n故事核心"
        storylineEntries={storylineEntries}
        storylinePanorama={panorama}
        storylineFormat="v2"
        activeStorylineFilename="主线"
        onSelectStoryline={vi.fn()}
      />,
    );
    const column = container.querySelector(".detail-outline-layout > .detail-outline-content");
    expect(column?.querySelector("article.detail-outline-markdown")).not.toBeNull();
  });
});

describe("切换条目内容列回顶（FR-003 / AC-003）", () => {
  it("人物：长内容滚到中部后切换条目，scrollTop 归零", () => {
    const { rerender } = renderCharacters("lin.md");
    const column = document.querySelector(".character-content") as HTMLElement;
    column.scrollTop = 120;
    expect(column.scrollTop).toBe(120);

    rerender(
      <CharactersPanel characters={characters} activeFilename="zhao.md" loading={false} onSelectCharacter={vi.fn()} />,
    );
    expect(column.scrollTop).toBe(0);
  });

  it("人物：切到内容不足一屏/空条目同样回顶（边界）", () => {
    const { rerender } = renderCharacters("lin.md");
    const column = document.querySelector(".character-content") as HTMLElement;
    column.scrollTop = 90;

    rerender(
      <CharactersPanel characters={characters} activeFilename="kong.md" loading={false} onSelectCharacter={vi.fn()} />,
    );
    expect(column.scrollTop).toBe(0);
    expect(screen.getByText("这个人物文件暂无内容。")).toBeInTheDocument();
  });

  it("物品：切换物品后 scrollTop 归零", () => {
    const { rerender } = render(
      <ObjectsPanel objects={objects} activeFilename="sword.md" loading={false} onSelectObject={vi.fn()} />,
    );
    const column = document.querySelector(".character-content") as HTMLElement;
    column.scrollTop = 200;

    rerender(<ObjectsPanel objects={objects} activeFilename="dan.md" loading={false} onSelectObject={vi.fn()} />);
    expect(column.scrollTop).toBe(0);
  });

  it("大纲：切换故事线后 scrollTop 归零", () => {
    const props = {
      storylineMarkdown: "# 核心\n\n故事核心",
      storylineEntries,
      storylinePanorama: panorama,
      storylineFormat: "v2",
      onSelectStoryline: vi.fn(),
    };
    const { rerender } = render(<ScriptPanel {...props} activeStorylineFilename="主线" />);
    const column = document.querySelector(".detail-outline-content") as HTMLElement;
    column.scrollTop = 150;

    rerender(<ScriptPanel {...props} activeStorylineFilename="暗线" />);
    expect(column.scrollTop).toBe(0);
  });

  it("大纲：同条目刷新数据不重置滚动位置（不打断阅读）", () => {
    const props = {
      storylineMarkdown: "# 核心\n\n故事核心",
      storylinePanorama: panorama,
      storylineFormat: "v2",
      activeStorylineFilename: "主线",
      onSelectStoryline: vi.fn(),
    };
    const { rerender } = render(
      <ScriptPanel {...props} storylineEntries={storylineEntries} />,
    );
    const column = document.querySelector(".detail-outline-content") as HTMLElement;
    column.scrollTop = 150;

    const refreshed = [{ ...storylineEntries[0], markdown: `${LONG}\n\n新增一段。` }, storylineEntries[1]];
    rerender(<ScriptPanel {...props} storylineEntries={refreshed} />);
    expect(column.scrollTop).toBe(150);
  });
});
