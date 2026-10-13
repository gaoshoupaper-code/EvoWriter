/**
 * WorkRail 作品浮窗验收（REQ-20261013-181350 FR-001，AC-001/002）。
 * jsdom 无真实布局：悬浮位置/宽度由 layout-contract.test 断言样式表，
 * 本文件覆盖三态结构契约（closed/mini/expanded）、小态概要内容、
 * 窄屏锁定、跳转定位、空态/失败态。
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

/** 默认宽屏 matchMedia stub（<1280px 视为窄屏，见 NARROW_QUERY） */
function stubWideMedia() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  stubWideMedia();
  useWorkrailStore.setState({
    floatMode: "mini",
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

describe("小态浮窗概要（AC-001）", () => {
  it("默认 mini：阶段+第几步+接下来写+最新 1 条产物，完整进度区不可见", () => {
    render(<WorkRail />);
    const mini = screen.getByLabelText("作品");
    expect(within(mini).getByText("结构阶段")).toBeInTheDocument();
    expect(within(mini).getByText("第 6 步 · 卷纲")).toBeInTheDocument();
    expect(within(mini).getByText("接下来写：卷纲·第1卷")).toBeInTheDocument();
    expect(within(mini).getByText("刚写完：04-总纲")).toBeInTheDocument();
    // 小态只有概要：完整进度区与索引组不渲染
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument();
    expect(screen.queryByText("大纲")).not.toBeInTheDocument();
  });

  it("生成中：小态显示旋转标记（AC-001 边界）", () => {
    useExecutionStore.setState({ loading: true });
    render(<WorkRail />);
    expect(screen.getByLabelText("生成中")).toBeInTheDocument();
  });

  it("等你拍板：awaiting 非空时小态显示暂停提示（AC-001 边界）", () => {
    useContentStore.setState({
      outlinePipeline: pipeline({ awaiting: { t1: "awaiting_approval" } }),
    });
    render(<WorkRail />);
    expect(screen.getByText("⏸ 等你拍板")).toBeInTheDocument();
  });

  it("legacy 工作区：小态显示旧版徽标与模式说明（AC-001 边界）", () => {
    useContentStore.setState({
      outlinePipeline: { ...pipeline(), kind: "legacy", phase: "legacy" },
    });
    render(<WorkRail />);
    expect(screen.getByText("旧版工作区")).toBeInTheDocument();
    expect(screen.getByText("五件套修订模式")).toBeInTheDocument();
  });

  it("全新工作区无产物 → 小态引导文案，不报错", () => {
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
});

describe("三态切换与唤回（AC-002）", () => {
  it("mini 点击卡面 → expanded：八步（名字+副标）与四组索引齐备", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByRole("button", { name: "展开作品详情" }));
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
    expect(document.querySelectorAll(".work-rail-steps li").length).toBe(8);
  });

  it("expanded 收起（»）→ mini；再点击卡面可回到 expanded", async () => {
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    await userEvent.click(screen.getByLabelText("收起作品浮窗"));
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "展开作品详情" }));
    expect(screen.getByLabelText("创作进度")).toBeInTheDocument();
  });

  it("彻底关闭（×）→ 只剩唤回小按钮；点唤回回 mini（DEC-006）", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByLabelText("关闭作品浮窗"));
    expect(screen.queryByLabelText("作品")).not.toBeInTheDocument();
    expect(screen.getByLabelText("展开作品浮窗")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("展开作品浮窗"));
    expect(screen.getByLabelText("作品")).toBeInTheDocument();
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument(); // 回到 mini 而非 expanded
  });

  it("生成中：expanded 态活跃步无文件时显示「生成中：xx」占位行", async () => {
    useExecutionStore.setState({ loading: true });
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    expect(screen.getByText("生成中：卷纲")).toBeInTheDocument();
  });
});

describe("窄窗口（<1280px）锁定小态（AC-006 结构部分，DEC-011）", () => {
  it("挂载即窄屏：expanded 被压回 mini", () => {
    useWorkrailStore.setState({ floatMode: "expanded" });
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    );
    render(<WorkRail />);
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开作品详情" })).toBeDisabled();
  });

  it("窗口缩过阈值触发 change → 压回 mini 且卡面点击置灰", () => {
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
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    expect(screen.getByLabelText("创作进度")).toBeInTheDocument();

    act(() => {
      matches = true;
      for (const cb of listeners) cb();
    });
    expect(useWorkrailStore.getState().floatMode).toBe("mini");
    expect(screen.getByRole("button", { name: "展开作品详情" })).toBeDisabled();
  });
});

describe("索引跳转定位（expanded 态）", () => {
  it("点大纲文件 → 面板切 outline 且选中该文件", async () => {
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    await userEvent.click(screen.getByText("04-总纲"));
    expect(useWorkspaceStore.getState().activePanel).toBe("outline");
    expect(useContentStore.getState().activeOutlineRel).toBe("outline/04-总纲.md");
  });

  it("点人物条目 → 面板切 characters 且选中该文件", async () => {
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    await userEvent.click(screen.getByText("人物")); // 展开人物组
    await userEvent.click(screen.getByText("林寒"));
    expect(useWorkspaceStore.getState().activePanel).toBe("characters");
    expect(useContentStore.getState().activeCharacterFilename).toBe("lin.md");
  });

  it("点物品条目 → 面板切 objects 且选中该文件", async () => {
    useContentStore.setState({
      objects: [{ filename: "sword.md", name: "青冥剑", markdown: "" }],
    });
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    await userEvent.click(screen.getByText("物品"));
    await userEvent.click(screen.getByText("青冥剑"));
    expect(useWorkspaceStore.getState().activePanel).toBe("objects");
    expect(useContentStore.getState().activeObjectFilename).toBe("sword.md");
  });

  it("点世界观条目 → 面板切 worldview", async () => {
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    await userEvent.click(screen.getByText("世界观"));
    await userEvent.click(screen.getByText("世界观设定"));
    expect(useWorkspaceStore.getState().activePanel).toBe("worldview");
  });
});

describe("交付高亮与失败态", () => {
  it("revealDelivery → 展开浮窗并高亮新增条目，4 秒后熄灭", async () => {
    vi.useFakeTimers();
    try {
      useWorkrailStore.setState({ floatMode: "closed" });
      render(<WorkRail />);
      await act(async () => {
        useWorkrailStore.getState().revealDelivery();
      });
      // 无基线快照时高亮全部现有条目（恢复会话分支）；交付必须展开浮窗
      expect(useWorkrailStore.getState().floatMode).toBe("expanded");
      const freshItems = document.querySelectorAll(".work-rail-entries li.fresh");
      expect(freshItems.length).toBeGreaterThanOrEqual(2);

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
    useWorkrailStore.setState({ pollError: true, floatMode: "expanded" });
    render(<WorkRail />);
    expect(screen.getByText("状态获取失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(fetchWorkspaceOutline).toHaveBeenCalledWith("ws-1");
  });
});
