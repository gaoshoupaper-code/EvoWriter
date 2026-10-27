
import { useEffect, useRef } from "react";
import type { CharacterMarkdownFile, ObjectMarkdownFile, PanoramaEvent, RhythmData, StorylineEntry } from "./types";
import {
  fetchWorkspaceCharacters,
  fetchWorkspaceObjects,
  fetchWorkspaceOutline,
  fetchWorkspaceStoryline,
  fetchWorkspaceWorldview,
} from "./api";
import { useContentStore } from "../stores/content";
import { useWorkrailStore } from "../stores/workrail";
import { useAnnotationsStore } from "../stores/annotations";

const POLL_INTERVAL_MS = 2000;

// 停前补拉兜底重拉延迟：final SSE 事件可能先于大纲文件落盘/索引就绪，
// 首拉全空时等文件系统追上再拉一次（仅一次，任务确实无产物时不无限轮询）。
const FINAL_RETRY_DELAY_MS = 2500;

/**
 * 内容面板的 setter 集合。签名刻意与 page.tsx 里 useState 返回的 setter 对齐，
 * 使本 Hook 能直接接管原 EventSource 订阅块写入的那批 state。
 * activeXxxFilename 的 setter 用函数式更新签名 ((cur)=>next)，
 * 因为选中项保持逻辑需要读取当前值。
 */
export interface PanelPollingSetters {

  setStorylineMarkdown: (s: string) => void;
  setStorylineEntries: (e: StorylineEntry[]) => void;
  setStorylinePanorama: (e: PanoramaEvent[]) => void;
  setStorylineRhythm: (v: RhythmData | null) => void;
  setStorylineFormat: (v: string) => void;
  setActiveStorylineFilename: (fn: (cur: string) => string) => void;

  setCharacters: (c: CharacterMarkdownFile[]) => void;
  setActiveCharacterFilename: (fn: (cur: string) => string) => void;
  setCharactersLoading: (b: boolean) => void;

  setObjects: (o: ObjectMarkdownFile[]) => void;
  setActiveObjectFilename: (fn: (cur: string) => string) => void;
  setObjectsLoading: (b: boolean) => void;

  setWorldviewMarkdown: (s: string) => void;
  setWorldviewLoading: (b: boolean) => void;

  setOutline: (v: { kind: string; pipeline: import("./types").PipelineStatus; files: import("./types").OutlineFileEntry[] }) => void;
}

export interface UsePanelPollingParams {
  activeWorkspaceId: string;
  loading: boolean;
  bootstrapping: boolean;
  setters: PanelPollingSetters;
}

/**
 * 选中项保持：当前 filename 仍在新列表里就保持，否则回退到第一项。
 * 与原 EventSource 订阅块的行为一致——避免文件被重命名/删除后面板显示空。
 */
function keepActiveFilename(current: string, filenames: string[]): string {
  return filenames.some((f) => f === current) ? current : (filenames[0] ?? "");
}

/** 从 contentStore 构造 setter 集（独立刷新入口用，不经过 home 传参）。 */
function contentStoreSetters(): PanelPollingSetters {
  const st = useContentStore.getState();
  return {
    setStorylineMarkdown: st.setStorylineMarkdown,
    setStorylineEntries: st.setStorylineEntries,
    setStorylinePanorama: st.setStorylinePanorama,
    setStorylineRhythm: st.setStorylineRhythm,
    setStorylineFormat: st.setStorylineFormat,
    setActiveStorylineFilename: (fn) => useContentStore.setState((s) => ({ activeStorylineFilename: fn(s.activeStorylineFilename) })),
    setCharacters: st.setCharacters,
    setActiveCharacterFilename: (fn) => useContentStore.setState((s) => ({ activeCharacterFilename: fn(s.activeCharacterFilename) })),
    setCharactersLoading: st.setCharactersLoading,
    setObjects: st.setObjects,
    setActiveObjectFilename: (fn) => useContentStore.setState((s) => ({ activeObjectFilename: fn(s.activeObjectFilename) })),
    setObjectsLoading: st.setObjectsLoading,
    setWorldviewMarkdown: st.setWorldviewMarkdown,
    setWorldviewLoading: st.setWorldviewLoading,
    setOutline: st.setOutline,
  };
}

/**
 * 全量拉取与写 state：大纲/人物/物品/世界观（+ storyline 供 legacy 判定）。
 * 返回本次拉取是否得到任何内容（停前补拉用：全空 → 延迟重拉兜底）。
 * 成功/失败都会写 workrailStore.pollError（作品栏「状态获取失败·重试」依据）。
 */
async function _pollAll(
  workspaceId: string,
  s: PanelPollingSetters,
): Promise<boolean> {
  let gotAny = false;
  const data = await fetchWorkspaceStoryline(workspaceId);
  s.setStorylineMarkdown(data.index_markdown);
  s.setStorylineEntries(data.entries);
  s.setStorylinePanorama(data.panorama);
  s.setStorylineRhythm(data.rhythm ?? null);
  s.setStorylineFormat(data.format);
  s.setActiveStorylineFilename((cur) => keepActiveFilename(cur, data.entries.map((e) => e.title)));
  if (data.index_markdown?.trim() || data.entries.length) gotAny = true;

  const charData = await fetchWorkspaceCharacters(workspaceId);
  s.setCharacters(charData.characters);
  s.setActiveCharacterFilename((cur) => keepActiveFilename(cur, charData.characters.map((c) => c.filename)));
  s.setCharactersLoading(false);
  if (charData.characters.length) gotAny = true;

  const objData = await fetchWorkspaceObjects(workspaceId);
  s.setObjects(objData.objects);
  s.setActiveObjectFilename((cur) => keepActiveFilename(cur, objData.objects.map((o) => o.filename)));
  s.setObjectsLoading(false);
  if (objData.objects.length) gotAny = true;

  const worldData = await fetchWorkspaceWorldview(workspaceId);
  s.setWorldviewMarkdown(worldData.markdown);
  s.setWorldviewLoading(false);
  if (worldData.markdown?.trim()) gotAny = true;

  const outlineData = await fetchWorkspaceOutline(workspaceId);
  s.setOutline(outlineData);
  if (outlineData.files.length) gotAny = true;

  return gotAny;
}

async function pollAll(workspaceId: string, s: PanelPollingSetters): Promise<boolean> {
  try {
    const gotAny = await _pollAll(workspaceId, s);
    useWorkrailStore.getState().markPollOk(true);
    return gotAny;
  } catch {
    useWorkrailStore.getState().markPollOk(false);
    // 静默：与单次轮询一致，避免打断完成态 UI。按全空处理走延迟重拉。
    return false;
  }
}

// 单飞串行（review P1 修复）：同一时刻至多一个全量轮询在途——
// 慢请求（底层 300s 超时）下 interval 不堆积；停前补拉/重试排队等在途
// 周期结束再跑，旧的在途数据永远不可能覆盖停前补拉写入的最终结果。
let pollChain: Promise<void> = Promise.resolve();
let pollInFlight = false;

function enqueuePoll(workspaceId: string, s: PanelPollingSetters): Promise<boolean> {
  const task = pollChain.then(() => {
    pollInFlight = true;
    return pollAll(workspaceId, s);
  });
  pollChain = task.then(
    () => undefined,
    () => undefined,
  );
  task.finally(() => {
    pollInFlight = false;
  });
  return task;
}

/**
 * 独立全量刷新（作品栏「重试」按钮用）：直接写 contentStore。
 * 在途周期未结束时跳过（单飞），避免连点堆积并发请求。
 */
export async function refreshWorkspaceContent(workspaceId: string): Promise<boolean> {
  if (pollInFlight) return false;
  return enqueuePoll(workspaceId, contentStoreSetters());
}

// ── SSE 断连后的阶段兜底观察（REQ-20261018-175526 FR-004 失败语义）──
// 心跳超时/断流后 loading 置 false、常规轮询停表，但服务端 run 可能仍在跑：
// 低频拉 outline 直到管线相位相对断连时点变化（阶段条/挂起卡片由 sync effect
// 响应）或达到上限（24 次 × 5s = 2 分钟）自行停止。
let disconnectWatchTimer: ReturnType<typeof setInterval> | null = null;

export function stopDisconnectWatch(): void {
  if (disconnectWatchTimer) {
    clearInterval(disconnectWatchTimer);
    disconnectWatchTimer = null;
  }
}

export function watchPipelineAfterDisconnect(workspaceId: string): void {
  stopDisconnectWatch();
  const startPhase = useContentStore.getState().outlinePipeline?.phase ?? "";
  let ticks = 0;
  disconnectWatchTimer = setInterval(() => {
    ticks += 1;
    const phase = useContentStore.getState().outlinePipeline?.phase ?? "";
    if (phase !== startPhase || ticks >= 24) {
      stopDisconnectWatch();
      return;
    }
    void enqueuePoll(workspaceId, contentStoreSetters());
  }, 5000);
}

/**
 * 内容轮询 Hook（REQ-20261012-202540 FR-005）。
 *
 * 行为规约：
 * - 仅 loading=true（生成中）时轮询，2s 周期，全量拉取——右侧作品栏常驻
 *   跟进进度与产物，刷新不依赖任何产物面板处于打开状态（含 chat）。
 * - bootstrapping=true 时跳过当前周期（bootstrap 为权威源，避免切换工作区
 *   状态闪烁），但不取消定时器，等 bootstrap 结束后自然恢复。
 * - activeWorkspaceId 变化时立即拉一次（切换即拉），再起 2s 周期。
 * - loading 从 true→false 过渡时补拉最后一次（保证显示 Agent 最终结果），之后停止。
 * - 空闲期不轮询（数据不会变化）。
 */
export function usePanelPolling({
  activeWorkspaceId,
  loading,
  bootstrapping,
  setters,
}: UsePanelPollingParams): void {
  // 用 ref 镜像最新 props，使轮询回调始终读到最新值而不依赖闭包快照。
  const bootstrappingRef = useRef(bootstrapping);
  bootstrappingRef.current = bootstrapping;
  const settersRef = useRef(setters);
  settersRef.current = setters;

  // 跟踪上一次 loading 值，用于检测 true→false 过渡，触发“停前补拉”。
  const prevLoadingRef = useRef(loading);

  const pollOnce = async (workspaceId: string) => {
    // bootstrap 是权威源（切换工作区时跳过，避免状态闪烁）；
    // 在途周期未结束也跳过本拍（单飞，防止慢请求下并发堆积）。
    if (bootstrappingRef.current || pollInFlight) return;
    // 标注交互进行中（划词/写意见）：跳过本拍刷新——刷新会重挂渲染、打断
    // 选区并丢失正在写的意见（REQ-20261027-213246 FR-001）。
    if (useAnnotationsStore.getState().interacting) return;
    await enqueuePoll(workspaceId, settersRef.current);
  };

  useEffect(() => {
    // 无工作区或未在生成中：什么都不做。
    // 注意：这里不能写 prevLoadingRef——过渡检测归下方停前补拉 effect 独占，
    // 本 effect 声明在前、会抢先写入新值，让停前补拉永远读不到 true→false 过渡
    // （线上 bug：创作完成后产物面板不刷新，重启才可见）。
    if (!activeWorkspaceId || !loading) {
      return;
    }

    // 生成中：立即全量拉一次（切换即拉 / 首次进入），再起 2s 定时器。
    void pollOnce(activeWorkspaceId);

    const timer = setInterval(() => {
      void pollOnce(activeWorkspaceId);
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
    // 依赖 activeWorkspaceId/loading：任一变化都重新挂载（切换即拉 / 起停）。
    // bootstrapping/setters 故意不进依赖（经 ref 读取，避免 bootstrap 期间频繁重挂）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspaceId, loading]);

  // 独立的 effect：检测 loading true→false 过渡，做"停前补拉"。
  // 不能并入上面的 effect——上面 effect 在 loading=false 时会直接 return 不轮询，
  // 这里专门负责"结束这一刻"的最终拉取。
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;
    // bootstrap 期间跳过停前补拉（与旧 pollAllPanels 行为一致）：bootstrap 是
    // 权威源，此刻写入可能与切换中的工作区数据互相覆盖。
    if (wasLoading && !loading && activeWorkspaceId && !bootstrappingRef.current) {
      void (async () => {
        // 停前补拉必须执行（最终结果）：排队等在途周期结束，串行写入。
        const gotAny = await enqueuePoll(activeWorkspaceId, settersRef.current);
        // 兜底：final 事件可能先于产物文件落盘/索引就绪，首拉全空时延迟重拉一次。
        if (!gotAny) {
          await new Promise((resolve) => setTimeout(resolve, FINAL_RETRY_DELAY_MS));
          if (bootstrappingRef.current) return;
          await enqueuePoll(activeWorkspaceId, settersRef.current);
        }
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);
}
