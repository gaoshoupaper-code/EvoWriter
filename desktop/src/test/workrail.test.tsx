/**
 * WorkRail 作品浮窗验收（REQ-20261018-115508 DEC-012：实时进度浮窗）。
 * jsdom 无真实布局：悬浮位置/宽度由 layout-contract.test 断言样式表，
 * 本文件覆盖三态结构契约（closed/mini/expanded）、小态概要内容、
 * 窄屏锁定、五段进度、空态/失败态。档案浏览（产物树）归作品侧栏（DEC-011）。
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
  kind: "five",
  phase: "chapter",
  next_unit: "章纲",
  awaiting: {},
  ...over,
});

const FILES = [
  { rel: "主题.md", markdown: "" },
  { rel: "总纲.md", markdown: "" },
  { rel: "文风.md", markdown: "" },
  { rel: "卷纲/第01卷.md", markdown: "" },
];

/** 默认宽屏 matchMedia stub（窄屏=对话区容器 <1280px ⇔ 窗口 <1544px） */
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
    outlineKind: "five",
    outlinePipeline: pipeline(),
    outlineFiles: FILES,
    activeOutlineRel: "主题.md",
    characters: [],
    activeCharacterFilename: "",
    objects: [],
    worldviewMarkdown: "",
  });
});

describe("小态浮窗概要（DEC-012）", () => {
  it("默认 mini：段位+第几步+接下来写+最新 1 条产物，完整进度区不可见", () => {
    render(<WorkRail />);
    const mini = screen.getByLabelText("作品");
    expect(within(mini).getByText("定章纲")).toBeInTheDocument();
    expect(within(mini).getByText("第 4 步 · 章纲")).toBeInTheDocument();
    expect(within(mini).getByText("接下来写：章纲")).toBeInTheDocument();
    expect(within(mini).getByText("刚写完：卷纲/第01卷")).toBeInTheDocument();
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument();
  });

  it("生成中：小态显示旋转标记", () => {
    useExecutionStore.setState({ loading: true });
    render(<WorkRail />);
    expect(screen.getByLabelText("生成中")).toBeInTheDocument();
  });

  it("等你拍板：awaiting 非空时小态显示暂停提示", () => {
    useContentStore.setState({
      outlinePipeline: pipeline({ awaiting: { t1: "awaiting_approval" } }),
    });
    render(<WorkRail />);
    expect(screen.getByText("⏸ 等你拍板")).toBeInTheDocument();
  });

  it("legacy 工作区：小态显示旧版徽标与模式说明", () => {
    useContentStore.setState({
      outlinePipeline: { ...pipeline(), kind: "legacy", phase: "legacy" },
    });
    render(<WorkRail />);
    expect(screen.getByText("旧版工作区")).toBeInTheDocument();
    expect(screen.getByText("旧版结构，只读查看")).toBeInTheDocument();
  });

  it("全新工作区无产物 → 小态引导文案，不报错", () => {
    useContentStore.setState({
      outlinePipeline: null,
      outlineFiles: [],
    });
    render(<WorkRail />);
    expect(screen.getByText("开始对话后，进度会在这里出现。")).toBeInTheDocument();
  });
});

describe("三态切换与唤回", () => {
  it("mini 点击卡面 → expanded：五段步名与接下来写齐备", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByRole("button", { name: "展开作品详情" }));
    const progress = screen.getByLabelText("创作进度");
    const steps = within(progress).getAllByText(/^(主题|总纲|卷纲|章纲|正文)$/);
    expect(new Set(steps.map((s) => s.textContent))).toEqual(new Set(["主题", "总纲", "卷纲", "章纲", "正文"]));
    expect(within(progress).getByText(/接下来写：章纲/)).toBeInTheDocument();
    expect(document.querySelectorAll(".work-rail-steps li").length).toBe(5);
    // 档案索引组不在此浮窗（归作品侧栏，DEC-011/012 分工）
    expect(screen.queryByText("刚写完：卷纲/第01卷") ?? within(progress).getByText(/刚写完/)).toBeTruthy();
  });

  it("FR-011 可跳转：展开态列出最新产物，点击切到产物面板并选中", async () => {
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    const artifacts = screen.getByLabelText("最新产物");
    // 最近 5 条倒序：文风/总纲/主题 顺序里最新是卷纲
    const items = within(artifacts).getAllByRole("button");
    expect(items[0]).toHaveTextContent("卷纲/第01卷");

    await userEvent.click(within(artifacts).getByRole("button", { name: "总纲" }));
    expect(useContentStore.getState().activeOutlineRel).toBe("总纲.md");
    expect(useWorkspaceStore.getState().activePanel).toBe("artifacts");
  });

  it("expanded 收起（»）→ mini；再点击卡面可回到 expanded", async () => {
    useWorkrailStore.setState({ floatMode: "expanded" });
    render(<WorkRail />);
    await userEvent.click(screen.getByLabelText("收起作品浮窗"));
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "展开作品详情" }));
    expect(screen.getByLabelText("创作进度")).toBeInTheDocument();
  });

  it("彻底关闭（×）→ 只剩唤回小按钮；点唤回回 mini", async () => {
    render(<WorkRail />);
    await userEvent.click(screen.getByLabelText("关闭作品浮窗"));
    expect(screen.queryByLabelText("作品")).not.toBeInTheDocument();
    expect(screen.getByLabelText("展开作品浮窗")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("展开作品浮窗"));
    expect(screen.getByLabelText("作品")).toBeInTheDocument();
    expect(screen.queryByLabelText("创作进度")).not.toBeInTheDocument(); // 回到 mini 而非 expanded
  });
});

describe("窄窗口（对话区容器 <1280px ⇔ 窗口 <1544px）锁定小态", () => {
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

describe("失败态", () => {
  it("轮询失败 → 「状态获取失败·重试」，点击重试发起全量刷新", async () => {
    const api = await import("@/lib/api");
    vi.mocked(api.fetchWorkspaceStoryline).mockResolvedValue({ index_markdown: "", entries: [], panorama: [], format: "v2" } as any);
    vi.mocked(api.fetchWorkspaceCharacters).mockResolvedValue({ characters: [] } as any);
    vi.mocked(api.fetchWorkspaceObjects).mockResolvedValue({ objects: [] } as any);
    vi.mocked(api.fetchWorkspaceWorldview).mockResolvedValue({ markdown: "" } as any);
    vi.mocked(fetchWorkspaceOutline).mockResolvedValue({
      kind: "five",
      pipeline: pipeline(),
      files: FILES,
    } as any);
    useWorkrailStore.setState({ pollError: true, floatMode: "expanded" });
    render(<WorkRail />);
    expect(screen.getByText("状态获取失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(fetchWorkspaceOutline).toHaveBeenCalledWith("ws-1");
  });
});
