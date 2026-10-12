/**
 * WorkRail 作品栏验收（REQ-20261012-202540 FR-001/FR-003，AC-001/002/004/011）。
 * jsdom 无真实布局：横滚/宽度由 design_lint + 浏览器 harness 验证，
 * 本文件覆盖结构契约（八步+副标+四组）、收起/展开、跳转定位、空态/失败态。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkRail } from "@/components/workspace/WorkRail";
import { useContentStore } from "@/stores/content";
import { useExecutionStore } from "@/stores/execution";
import { useWorkspaceStore } from "@/stores/workspace";
import { useWorkrailStore } from "@/stores/workrail";
import type { PipelineStatus } from "@/lib/types";
import { fetchWorkspaceOutline } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchWorkspaceStoryline: vi.fn(),
  fetchWorkspaceCharacters: vi.fn(),
  fetchWorkspaceObjects: vi.fn(),
  fetchWorkspaceWorldview: vi.fn(),
  fetchWorkspaceOutline: vi.fn(),
}));

const pipeline = (over: Partial<PipelineStatus> = {}): PipelineStatus => ({
  kind: "pipeline",
  phase: "storystructure",
  next_unit: "卷纲·第1卷",
  design_done: false,
  rework: null,
  awaiting: {},
  ...over,
});

const FILES = [
  { rel: "outline/00-赛道.md", markdown: "" },
  { rel: "outline/01-设计原则.md", markdown: "" },
  { rel: "outline/04-总纲.md", markdown: "" },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  );
  useWorkrailStore.setState({
    collapsed: false,
    pollError: false,
    highlightKeys: new Set(),
    baselineKeys: null,
    _highlightTimer: null,
  });
  useExecutionStore.setState({ loading: false });
  useWorkspaceStore.setState({ activeWorkspaceId: "ws-1", activePanel: "chat" });
  useContentStore.setState({
    outlineKind: "pipeline",
    outlinePipeline: pipeline(),
    outlineFiles: FILES,
    activeOutlineRel: "outline/00-赛道.md",
    characters: [{ filename: "lin.md", name: "林寒", markdown: "" }],
    activeCharacterFilename: "",
    objects: [],
    worldviewMarkdown: "# 世界观",
  });
});

describe("作品栏默认视图（AC-001）", () => {
  it("八步进度（名字+副标+状态）与四组索引齐备，无横向滚动容器", () => {
    render(<WorkRail />);
    const progress = screen.getByLabelText("创作进度");
    for (const name of ["赛道", "设计原则", "冲突引擎", "力量体系", "总纲", "卷纲", "章纲", "卷末盘点"]) {
      expect(within(progress).getByText(name)).toBeInTheDocument();
    }
    expect(within(progress).getByText(/定类型：写什么题材、给谁看/)).toBeInTheDocument();
    expect(within(progress).getByText(/接下来写：卷纲·第1卷/)).toBeInTheDocument();

    const rail = screen.getByLabelText("作品");
    for (const g of ["大纲", "人物", "物品", "世界观"]) {
      expect(within(rail).getByText(g)).toBeInTheDocument();
    }
    // 大纲组默认展开：文件到条目级
    expect(within(rail).getByText("00-赛道")).toBeInTheDocument();
    // 步骤纵向网格（work-rail-steps 为 ol；横滚与否属布局行为，由浏览器目检）
    expect(document.querySelectorAll(".work-rail-steps li").length).toBe(8);
  });

  it("legacy 工作区：显示旧版提示（边界）", () => {
    useContentStore.setState({
      outlinePipeline: { ...pipeline(), kind: "legacy", phase: "legacy" },
    });
    render(<WorkRail />);
    expect(screen.getByText("旧版工作区")).toBeInTheDocument();
    expect(screen.getByText("五件套修订模式")).toBeInTheDocument();
  });

  it("生成中：活跃步无文件时显示「生成中：xx」占位行", () => {
    useExecutionStore.setState({ loading: true });
    render(<WorkRail />);
    expect(screen.getByText("生成中：卷纲")).toBeInTheDocument();
  });
});

describe("收起与展开（AC-002 结构部分）", () => {
  it("收起变细条（进度点+展开按钮），再点恢复", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByLabelText("收起作品栏"));
    expect(document.querySelector(".work-rail.collapsed")).not.toBeNull();
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("展开作品栏"));
    expect(document.querySelector(".work-rail.collapsed")).toBeNull();
    expect(screen.getByLabelText("创作进度")).toBeInTheDocument();
  });

  it("窄窗口（<1280px）挂载即自动收起（AC-002）", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    );
    render(<WorkRail />);
    expect(document.querySelector(".work-rail.collapsed")).not.toBeNull();
  });

  it("窗口缩过阈值触发 change → 自动收起，可手动展开（AC-002）", () => {
    const listeners: Array<() => void> = [];
    let matches = false;
    const mq = {
      get matches() {
        return matches;
      },
      addEventListener: (_t: string, cb: () => void) => listeners.push(cb),
      removeEventListener: vi.fn(),
    };
    vi.stubGlobal("matchMedia", vi.fn(() => mq));
    render(<WorkRail />);
    expect(document.querySelector(".work-rail.collapsed")).toBeNull();

    act(() => {
      matches = true;
      for (const cb of listeners) cb();
    });
    expect(useWorkrailStore.getState().collapsed).toBe(true);
    expect(document.querySelector(".work-rail.collapsed")).not.toBeNull();
  });
});

describe("索引跳转定位（AC-004）", () => {
  it("点大纲文件 → 面板切 outline 且选中该文件", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByText("04-总纲"));
    expect(useWorkspaceStore.getState().activePanel).toBe("outline");
    expect(useContentStore.getState().activeOutlineRel).toBe("outline/04-总纲.md");
  });

  it("点人物条目 → 面板切 characters 且选中该文件", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByText("人物")); // 展开人物组
    await userEvent.click(screen.getByText("林寒"));
    expect(useWorkspaceStore.getState().activePanel).toBe("characters");
    expect(useContentStore.getState().activeCharacterFilename).toBe("lin.md");
  });

  it("点物品条目 → 面板切 objects 且选中该文件（AC-004 边界补齐）", async () => {
    useContentStore.setState({
      objects: [{ filename: "sword.md", name: "青冥剑", markdown: "" }],
    });
    render(<WorkRail />);
    await userEvent.click(screen.getByText("物品"));
    await userEvent.click(screen.getByText("青冥剑"));
    expect(useWorkspaceStore.getState().activePanel).toBe("objects");
    expect(useContentStore.getState().activeObjectFilename).toBe("sword.md");
  });

  it("点世界观条目 → 面板切 worldview", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByText("世界观"));
    await userEvent.click(screen.getByText("世界观设定"));
    expect(useWorkspaceStore.getState().activePanel).toBe("worldview");
  });
});

describe("空态与失败态（AC-011）", () => {
  it("全新工作区无产物 → 引导文案，不报错", () => {
    useContentStore.setState({
      outlinePipeline: null,
      outlineFiles: [],
      characters: [],
      objects: [],
      worldviewMarkdown: "",
    });
    render(<WorkRail />);
    expect(screen.getByText("开始对话后，作品会在这里出现。")).toBeInTheDocument();
  });

  it("交付高亮渲染到条目级（fresh），4 秒后自动熄灭（AC-006 渲染端）", async () => {
    vi.useFakeTimers();
    try {
      render(<WorkRail />);
      await act(async () => {
        useWorkrailStore.getState().revealDelivery();
      });
      // 无基线快照时高亮全部现有条目（恢复会话分支）
      const freshItems = document.querySelectorAll(".work-rail-entries li.fresh");
      expect(freshItems.length).toBeGreaterThanOrEqual(2); // 大纲组 3 个文件均在展开组内

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
      expect(document.querySelectorAll(".work-rail-entries li.fresh").length).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("轮询失败 → 「状态获取失败·重试」，点击重试发起全量刷新", async () => {
    const api = await import("@/lib/api");
    vi.mocked(api.fetchWorkspaceStoryline).mockResolvedValue({ index_markdown: "", entries: [], panorama: [], format: "v2" } as any);
    vi.mocked(api.fetchWorkspaceCharacters).mockResolvedValue({ characters: [] } as any);
    vi.mocked(api.fetchWorkspaceObjects).mockResolvedValue({ objects: [] } as any);
    vi.mocked(api.fetchWorkspaceWorldview).mockResolvedValue({ markdown: "" } as any);
    vi.mocked(fetchWorkspaceOutline).mockResolvedValue({ kind: "pipeline", pipeline: pipeline(), files: FILES } as any);
    useWorkrailStore.setState({ pollError: true });
    render(<WorkRail />);
    expect(screen.getByText("状态获取失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(fetchWorkspaceOutline).toHaveBeenCalledWith("ws-1");
  });
});
