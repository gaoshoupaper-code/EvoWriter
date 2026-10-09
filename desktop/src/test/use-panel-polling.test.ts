/**
 * usePanelPolling 停前补拉行为测试（线上 bug：创作完成后执行端看不到产物）。
 *
 * 根因：生成中用户停在 chat 面板（不参与轮询），任务结束 loading→false 时
 * 原逻辑只补拉"当前面板"——chat 直接跳过，三个内容面板全部保持旧空数据，
 * 切面板后仍不拉（loading=false 不轮询），直到重启/切工作区触发 bootstrap。
 *
 * 修复契约：loading true→false 过渡时全量拉三件套，与当前面板无关。
 */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { usePanelPolling, type PanelPollingSetters } from "@/lib/usePanelPolling";
import {
  fetchWorkspaceCharacters,
  fetchWorkspaceStoryline,
  fetchWorkspaceWorldview,
} from "@/lib/api";
import type { CharacterMarkdownFile, PanoramaEvent, StorylineEntry } from "@/lib/types";
import { fetchWorkspaceObjects } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchWorkspaceStoryline: vi.fn(),
  fetchWorkspaceCharacters: vi.fn(),
  fetchWorkspaceObjects: vi.fn(),
  fetchWorkspaceWorldview: vi.fn(),
}));

const mockedStoryline = vi.mocked(fetchWorkspaceStoryline);
const mockedCharacters = vi.mocked(fetchWorkspaceCharacters);
const mockedWorldview = vi.mocked(fetchWorkspaceWorldview);
const mockedObjects = vi.mocked(fetchWorkspaceObjects);

function makeSetters(): PanelPollingSetters {
  return {
    setStorylineMarkdown: vi.fn(),
    setStorylineEntries: vi.fn(),
    setStorylinePanorama: vi.fn(),
    setStorylineRhythm: vi.fn(),
    setStorylineFormat: vi.fn(),
    setActiveStorylineFilename: vi.fn(),
    setCharacters: vi.fn(),
    setActiveCharacterFilename: vi.fn(),
    setCharactersLoading: vi.fn(),
    setObjects: vi.fn(),
    setActiveObjectFilename: vi.fn(),
    setObjectsLoading: vi.fn(),
    setWorldviewMarkdown: vi.fn(),
    setWorldviewLoading: vi.fn(),
  };
}

const ENTRIES: StorylineEntry[] = [{ filename: "storyline.md", title: "复仇线", markdown: "## 复仇线" }];
const PANORAMA: PanoramaEvent[] = [];
const CHARACTERS: CharacterMarkdownFile[] = [
  { filename: "林寒.md", name: "林寒", markdown: "# 林寒" },
];

describe("usePanelPolling 停前全量补拉", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedStoryline.mockResolvedValue({
      index_markdown: "# 大纲",
      entries: ENTRIES,
      panorama: PANORAMA,
      format: "v2",
    } as any);
    mockedCharacters.mockResolvedValue({ characters: CHARACTERS } as any);
    mockedObjects.mockResolvedValue({ objects: [] } as any);
    mockedWorldview.mockResolvedValue({ markdown: "# 世界观" } as any);
  });

  it("任务结束（loading true→false）时停在 chat 面板也全量拉三件套", async () => {
    const setters = makeSetters();
    // 生成中：用户停在 chat 面板（不参与轮询，三个 fetch 均未调用）。
    const hook = renderHook(
      ({ loading }: { loading: boolean }) =>
        usePanelPolling({
          activeWorkspaceId: "ws-1",
          activePanel: "chat",
          loading,
          bootstrapping: false,
          setters,
        }),
      { initialProps: { loading: true } },
    );
    expect(mockedStoryline).not.toHaveBeenCalled();

    // 任务完成：loading → false。
    hook.rerender({ loading: false });

    // 三个内容面板接口都被拉取（与当前面板是 chat 无关）。
    await vi.waitFor(() => {
      expect(mockedStoryline).toHaveBeenCalledWith("ws-1");
      expect(mockedCharacters).toHaveBeenCalledWith("ws-1");
      expect(mockedWorldview).toHaveBeenCalledWith("ws-1");
    });
    // 产物数据写入 store setters。
    expect(setters.setStorylineMarkdown).toHaveBeenCalledWith("# 大纲");
    expect(setters.setCharacters).toHaveBeenCalledWith(CHARACTERS);
    expect(setters.setWorldviewMarkdown).toHaveBeenCalledWith("# 世界观");
    hook.unmount();
  });

  it("loading 一直为 false（无生成）不触发任何拉取", async () => {
    const setters = makeSetters();
    const hook = renderHook(
      ({ loading }: { loading: boolean }) =>
        usePanelPolling({
          activeWorkspaceId: "ws-1",
          activePanel: "script",
          loading,
          bootstrapping: false,
          setters,
        }),
      { initialProps: { loading: false } },
    );
    // 首次挂载 loading 已是 false：没有 true→false 过渡，也不在生成中——不拉。
    await new Promise((r) => setTimeout(r, 30));
    expect(mockedStoryline).not.toHaveBeenCalled();
    expect(mockedCharacters).not.toHaveBeenCalled();
    expect(mockedWorldview).not.toHaveBeenCalled();
    hook.unmount();
  });

  it("停前补拉全空时延迟重拉一次（final 先于产物落盘的竞态兜底）", async () => {
    vi.useFakeTimers();
    try {
      // 首拉三件套全空
      mockedStoryline.mockResolvedValue({ index_markdown: "", entries: [], panorama: [], format: "v2" } as any);
      mockedCharacters.mockResolvedValue({ characters: [] } as any);
      mockedObjects.mockResolvedValue({ objects: [] } as any);
      mockedWorldview.mockResolvedValue({ markdown: "" } as any);

      const setters = makeSetters();
      const hook = renderHook(
        ({ loading }: { loading: boolean }) =>
          usePanelPolling({
            activeWorkspaceId: "ws-1",
            activePanel: "chat",
            loading,
            bootstrapping: false,
            setters,
          }),
        { initialProps: { loading: true } },
      );
      hook.rerender({ loading: false });

      // 首拉完成：三接口各一次，写入空数据
      await vi.advanceTimersByTimeAsync(0);
      expect(mockedStoryline).toHaveBeenCalledTimes(1);
      expect(setters.setStorylineMarkdown).toHaveBeenCalledWith("");

      // 文件落盘后接口有数据了
      mockedStoryline.mockResolvedValue({ index_markdown: "# 大纲", entries: ENTRIES, panorama: PANORAMA, format: "v2" } as any);
      mockedCharacters.mockResolvedValue({ characters: CHARACTERS } as any);
      mockedObjects.mockResolvedValue({ objects: [] } as any);
      mockedWorldview.mockResolvedValue({ markdown: "# 世界观" } as any);

      // 2.5s 后兜底重拉，写入真数据
      await vi.advanceTimersByTimeAsync(2500);
      expect(mockedStoryline).toHaveBeenCalledTimes(2);
      expect(setters.setStorylineMarkdown).toHaveBeenCalledWith("# 大纲");
      expect(setters.setCharacters).toHaveBeenCalledWith(CHARACTERS);
      expect(setters.setWorldviewMarkdown).toHaveBeenCalledWith("# 世界观");

      // 任务确实无产物时不再无限拉：再推进也不会有第三次
      await vi.advanceTimersByTimeAsync(10000);
      expect(mockedStoryline).toHaveBeenCalledTimes(2);
      hook.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("停前补拉拉到内容时不做延迟重拉", async () => {
    vi.useFakeTimers();
    try {
      // beforeEach 已把三接口 mock 成有数据
      const setters = makeSetters();
      const hook = renderHook(
        ({ loading }: { loading: boolean }) =>
          usePanelPolling({
            activeWorkspaceId: "ws-1",
            activePanel: "chat",
            loading,
            bootstrapping: false,
            setters,
          }),
        { initialProps: { loading: true } },
      );
      hook.rerender({ loading: false });

      await vi.advanceTimersByTimeAsync(10000);
      // 只有停前补拉那一次，无兜底重拉
      expect(mockedStoryline).toHaveBeenCalledTimes(1);
      expect(mockedCharacters).toHaveBeenCalledTimes(1);
      expect(mockedWorldview).toHaveBeenCalledTimes(1);
      hook.unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
