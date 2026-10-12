/**
 * usePanelPolling 轮询行为测试（REQ-20261012-202540 FR-005 + 停前补拉回归）。
 *
 * FR-005 契约：生成中全量轮询（2s），不依赖任何产物面板打开——右侧作品栏
 * 常驻跟进进度与产物；loading true→false 过渡时补拉最后一次；空闲不轮询；
 * 轮询失败写 workrailStore.pollError（作品栏「状态获取失败·重试」依据）。
 */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { usePanelPolling, type PanelPollingSetters } from "@/lib/usePanelPolling";
import { useWorkrailStore } from "@/stores/workrail";
import {
  fetchWorkspaceCharacters,
  fetchWorkspaceOutline,
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
  fetchWorkspaceOutline: vi.fn(),
}));

const mockedStoryline = vi.mocked(fetchWorkspaceStoryline);
const mockedCharacters = vi.mocked(fetchWorkspaceCharacters);
const mockedWorldview = vi.mocked(fetchWorkspaceWorldview);
const mockedObjects = vi.mocked(fetchWorkspaceObjects);
const mockedOutline = vi.mocked(fetchWorkspaceOutline);

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
    setOutline: vi.fn(),
  };
}

const ENTRIES: StorylineEntry[] = [{ filename: "storyline.md", title: "复仇线", markdown: "## 复仇线" }];
const PANORAMA: PanoramaEvent[] = [];
const CHARACTERS: CharacterMarkdownFile[] = [
  { filename: "林寒.md", name: "林寒", markdown: "# 林寒" },
];

describe("usePanelPolling 生成中全量轮询（FR-005）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useWorkrailStore.setState({ pollError: false });
    mockedStoryline.mockResolvedValue({
      index_markdown: "# 大纲",
      entries: ENTRIES,
      panorama: PANORAMA,
      format: "v2",
    } as any);
    mockedCharacters.mockResolvedValue({ characters: CHARACTERS } as any);
    mockedObjects.mockResolvedValue({ objects: [] } as any);
    mockedWorldview.mockResolvedValue({ markdown: "# 世界观" } as any);
    mockedOutline.mockResolvedValue({ kind: "pipeline", pipeline: {}, files: [] } as any);
  });

  it("生成中（loading=true）立即全量拉取——用户停在对话也不缺席（AC-007）", async () => {
    const setters = makeSetters();
    const hook = renderHook(
      ({ loading }: { loading: boolean }) =>
        usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters }),
      { initialProps: { loading: true } },
    );

    await vi.waitFor(() => {
      expect(mockedStoryline).toHaveBeenCalledWith("ws-1");
      expect(mockedOutline).toHaveBeenCalledWith("ws-1");
    });
    expect(setters.setStorylineMarkdown).toHaveBeenCalledWith("# 大纲");
    expect(useWorkrailStore.getState().pollError).toBe(false);
    hook.unmount();
  });

  it("轮询失败写 pollError，成功恢复后清除（FR-001 失败语义）", async () => {
    mockedStoryline.mockRejectedValue(new Error("network down"));
    const setters = makeSetters();
    const hook = renderHook(
      ({ loading }: { loading: boolean }) =>
        usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters }),
      { initialProps: { loading: true } },
    );
    await vi.waitFor(() => {
      expect(useWorkrailStore.getState().pollError).toBe(true);
    });
    hook.unmount();

    // 恢复后（例如停前补拉）成功 → 清除失败标记
    mockedStoryline.mockResolvedValue({ index_markdown: "# 大纲", entries: ENTRIES, panorama: PANORAMA, format: "v2" } as any);
    const hook2 = renderHook(
      ({ loading }: { loading: boolean }) =>
        usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters: makeSetters() }),
      { initialProps: { loading: true } },
    );
    hook2.rerender({ loading: false });
    await vi.waitFor(() => {
      expect(useWorkrailStore.getState().pollError).toBe(false);
    });
    hook2.unmount();
  });

  it("loading 一直为 false（无生成）不触发任何拉取", async () => {
    const setters = makeSetters();
    const hook = renderHook(
      ({ loading }: { loading: boolean }) =>
        usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters }),
      { initialProps: { loading: false } },
    );
    // 首次挂载 loading 已是 false：没有 true→false 过渡，也不在生成中——不拉。
    await new Promise((r) => setTimeout(r, 30));
    expect(mockedStoryline).not.toHaveBeenCalled();
    expect(mockedCharacters).not.toHaveBeenCalled();
    expect(mockedWorldview).not.toHaveBeenCalled();
    hook.unmount();
  });

  it("生成中 2s 周期全量轮询，卸载后停止（FR-005 回归）", async () => {
    vi.useFakeTimers();
    try {
      const setters = makeSetters();
      const hook = renderHook(
        ({ loading }: { loading: boolean }) =>
          usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters }),
        { initialProps: { loading: true } },
      );
      await vi.advanceTimersByTimeAsync(0);
      expect(mockedStoryline).toHaveBeenCalledTimes(1); // 挂载即拉

      await vi.advanceTimersByTimeAsync(2000);
      expect(mockedStoryline).toHaveBeenCalledTimes(2); // 第 1 拍
      await vi.advanceTimersByTimeAsync(2000);
      expect(mockedStoryline).toHaveBeenCalledTimes(3); // 第 2 拍

      hook.unmount();
      await vi.advanceTimersByTimeAsync(4000);
      expect(mockedStoryline).toHaveBeenCalledTimes(3); // 卸载后不再轮询
    } finally {
      vi.useRealTimers();
    }
  });

  it("bootstrapping 期间不轮询、也不停前补拉（bootstrap 是权威源）", async () => {
    vi.useFakeTimers();
    try {
      const setters = makeSetters();
      const hook = renderHook(
        ({ loading }: { loading: boolean }) =>
          usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: true, setters }),
        { initialProps: { loading: true } },
      );
      await vi.advanceTimersByTimeAsync(5000);
      expect(mockedStoryline).not.toHaveBeenCalled();

      hook.rerender({ loading: false }); // true→false 也不补拉
      await vi.advanceTimersByTimeAsync(5000);
      expect(mockedStoryline).not.toHaveBeenCalled();
      hook.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("在途周期串行：interval 跳过、停前补拉等在途结束后写入最终数据（review P1）", async () => {
    vi.useFakeTimers();
    try {
      let resolveFirst!: (v: any) => void;
      mockedStoryline.mockImplementationOnce(
        () => new Promise((r) => { resolveFirst = r; }),
      );
      const setters = makeSetters();
      const hook = renderHook(
        ({ loading }: { loading: boolean }) =>
          usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters }),
        { initialProps: { loading: true } },
      );
      // 挂载即拉的第一轮挂起（慢请求）；期间 interval 拍到但被单飞跳过
      await vi.advanceTimersByTimeAsync(4000);
      expect(mockedStoryline).toHaveBeenCalledTimes(1);

      // 任务结束：停前补拉排队等在途周期，不并发
      hook.rerender({ loading: false });
      await vi.advanceTimersByTimeAsync(1000);
      expect(mockedStoryline).toHaveBeenCalledTimes(1);

      // 在途周期完成（写入旧数据）→ 排队的停前补拉立即执行（写入最终数据）
      resolveFirst({ index_markdown: "# 旧数据", entries: [], panorama: [], format: "v2" });
      await vi.advanceTimersByTimeAsync(0);
      expect(mockedStoryline).toHaveBeenCalledTimes(2);
      expect(setters.setStorylineMarkdown).toHaveBeenLastCalledWith("# 大纲");
      hook.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("停前补拉全空时延迟重拉一次（final 先于产物落盘的竞态兜底）", async () => {
    vi.useFakeTimers();
    try {
      // 首拉全空
      mockedStoryline.mockResolvedValue({ index_markdown: "", entries: [], panorama: [], format: "v2" } as any);
      mockedCharacters.mockResolvedValue({ characters: [] } as any);
      mockedObjects.mockResolvedValue({ objects: [] } as any);
      mockedWorldview.mockResolvedValue({ markdown: "" } as any);
      mockedOutline.mockResolvedValue({ kind: "pipeline", pipeline: {}, files: [] } as any);

      const setters = makeSetters();
      const hook = renderHook(
        ({ loading }: { loading: boolean }) =>
          usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters }),
        { initialProps: { loading: true } },
      );
      hook.rerender({ loading: false });

      // 首拉完成：挂载即全量拉一次（FR-005）+ 停前补拉一次 = 2 次，写入空数据
      await vi.advanceTimersByTimeAsync(0);
      expect(mockedStoryline).toHaveBeenCalledTimes(2);
      expect(setters.setStorylineMarkdown).toHaveBeenCalledWith("");

      // 文件落盘后接口有数据了
      mockedStoryline.mockResolvedValue({ index_markdown: "# 大纲", entries: ENTRIES, panorama: PANORAMA, format: "v2" } as any);
      mockedCharacters.mockResolvedValue({ characters: CHARACTERS } as any);
      mockedObjects.mockResolvedValue({ objects: [] } as any);
      mockedWorldview.mockResolvedValue({ markdown: "# 世界观" } as any);
      mockedOutline.mockResolvedValue({ kind: "pipeline", pipeline: {}, files: [] } as any);

      // 2.5s 后兜底重拉，写入真数据
      await vi.advanceTimersByTimeAsync(2500);
      expect(mockedStoryline).toHaveBeenCalledTimes(3);
      expect(setters.setStorylineMarkdown).toHaveBeenCalledWith("# 大纲");
      expect(setters.setCharacters).toHaveBeenCalledWith(CHARACTERS);
      expect(setters.setWorldviewMarkdown).toHaveBeenCalledWith("# 世界观");

      // 任务确实无产物时不再无限拉：再推进也不会有第四次
      await vi.advanceTimersByTimeAsync(10000);
      expect(mockedStoryline).toHaveBeenCalledTimes(3);
      hook.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("停前补拉拉到内容时不做延迟重拉", async () => {
    vi.useFakeTimers();
    try {
      // beforeEach 已把接口 mock 成有数据
      const setters = makeSetters();
      const hook = renderHook(
        ({ loading }: { loading: boolean }) =>
          usePanelPolling({ activeWorkspaceId: "ws-1", loading, bootstrapping: false, setters }),
        { initialProps: { loading: true } },
      );
      hook.rerender({ loading: false });

      await vi.advanceTimersByTimeAsync(10000);
      // 挂载即拉一次 + 停前补拉一次，拉到内容后无兜底重拉
      expect(mockedStoryline).toHaveBeenCalledTimes(2);
      expect(mockedCharacters).toHaveBeenCalledTimes(2);
      expect(mockedWorldview).toHaveBeenCalledTimes(2);
      hook.unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
