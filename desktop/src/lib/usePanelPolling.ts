
import { useEffect, useRef } from "react";
import type { CharacterMarkdownFile, PanoramaEvent, StorylineEntry } from "./types";
import type { WorkspacePanel } from "./types";
import {
  fetchWorkspaceCharacters,
  fetchWorkspaceStoryline,
  fetchWorkspaceWorldview,
} from "./api";

const POLL_INTERVAL_MS = 2000;

// 停前补拉兜底重拉延迟：final SSE 事件可能先于大纲文件落盘/索引就绪，
// 首拉全空时等文件系统追上再拉一次（仅一次，任务确实无产物时不无限轮询）。
const FINAL_RETRY_DELAY_MS = 2500;

// 不参与轮询的面板：chat 走独立 /generate/stream（聊天区不动）。
const NON_POLL_PANELS: ReadonlySet<WorkspacePanel> = new Set(["chat"]);

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
  setStorylineFormat: (v: string) => void;
  setActiveStorylineFilename: (fn: (cur: string) => string) => void;


  setCharacters: (c: CharacterMarkdownFile[]) => void;
  setActiveCharacterFilename: (fn: (cur: string) => string) => void;
  setCharactersLoading: (b: boolean) => void;

  setWorldviewMarkdown: (s: string) => void;
  setWorldviewLoading: (b: boolean) => void;
}

export interface UsePanelPollingParams {
  activeWorkspaceId: string;
  activePanel: WorkspacePanel;
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

/**
 * 内容面板轮询 Hook。
 *
 * 行为规约（对应需求/设计的冻结决策）：
 * - 仅 loading=true（生成中）时轮询；否则不发起任何请求。
 * - 仅轮询当前打开的面板；chat 不参与。
 * - bootstrapping=true 时跳过当前周期（bootstrap 为权威源，避免切换工作区状态闪烁），
 *   但不取消定时器，等 bootstrap 结束后自然恢复。
 * - activePanel / activeWorkspaceId 变化时立即拉一次（切换即拉），再起 2s 周期。
 * - loading 从 true→false 过渡时补拉最后一次（保证显示 Agent 最终结果），之后停止。
 */
export function usePanelPolling({
  activeWorkspaceId,
  activePanel,
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

  // 一次轮询：根据当前面板拉对应接口并写 state。bootstrap 期间跳过（不写）。
  const pollOnce = async (panel: WorkspacePanel, workspaceId: string) => {
    if (bootstrappingRef.current || panel === "chat") return;
    const s = settersRef.current;
    try {
      await _pollPanel(panel, workspaceId, s);
    } catch {
      // 单次轮询失败静默处理——下一周期会重试，无需把 loading 置回 true（避免面板闪烁）。
    }
  };

  // 单面板拉取与写 state（pollOnce 与 pollAllPanels 共用）。
  // 返回本次拉取是否得到任何内容（停前补拉用：全空 → 延迟重拉兜底）。
  const _pollPanel = async (
    panel: Exclude<WorkspacePanel, "chat"> | "all",
    workspaceId: string,
    s: PanelPollingSetters,
  ): Promise<boolean> => {
    let gotAny = false;
    if (panel === "script" || panel === "all") {
      // script 面板展示 storyline（含全景表数据 panorama，FR-003）。
      const data = await fetchWorkspaceStoryline(workspaceId);
      s.setStorylineMarkdown(data.index_markdown);
      s.setStorylineEntries(data.entries);
      s.setStorylinePanorama(data.panorama);
      s.setStorylineFormat(data.format);
      s.setActiveStorylineFilename((cur) => keepActiveFilename(cur, data.entries.map((e) => e.title)));
      if (data.index_markdown?.trim() || data.entries.length) gotAny = true;
    }
    if (panel === "characters" || panel === "all") {
      const data = await fetchWorkspaceCharacters(workspaceId);
      s.setCharacters(data.characters);
      s.setActiveCharacterFilename((cur) => keepActiveFilename(cur, data.characters.map((c) => c.filename)));
      s.setCharactersLoading(false);
      if (data.characters.length) gotAny = true;
    }
    if (panel === "worldview" || panel === "all") {
      const data = await fetchWorkspaceWorldview(workspaceId);
      s.setWorldviewMarkdown(data.markdown);
      s.setWorldviewLoading(false);
      if (data.markdown?.trim()) gotAny = true;
    }
    return gotAny;
  };

  // 全量拉取：三个内容面板并行（任务结束时用，见停前补拉 effect）。
  // 返回是否拉到内容；bootstrapping 期间跳过（bootstrap 是权威源，不触发重拉）。
  const pollAllPanels = async (workspaceId: string): Promise<boolean> => {
    if (bootstrappingRef.current) return true;
    try {
      return await _pollPanel("all", workspaceId, settersRef.current);
    } catch {
      // 静默：与单次轮询一致，避免打断完成态 UI。按全空处理走延迟重拉。
      return false;
    }
  };

  useEffect(() => {
    // 无工作区、或面板不参与轮询、或未在生成中：什么都不做。
    // 注意：这里不能写 prevLoadingRef——过渡检测归下方停前补拉 effect 独占，
    // 本 effect 声明在前、会抢先写入新值，让停前补拉永远读不到 true→false 过渡
    // （线上 bug：创作完成后产物面板不刷新，重启才可见）。
    if (!activeWorkspaceId || NON_POLL_PANELS.has(activePanel) || !loading) {
      return;
    }

    // 生成中：立即拉一次（切换即拉 / 首次进入），再起 2s 定时器。
    void pollOnce(activePanel, activeWorkspaceId);

    const timer = setInterval(() => {
      void pollOnce(activePanel, activeWorkspaceId);
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
    // 依赖 activePanel/activeWorkspaceId/loading：三者任一变化都重新挂载（切换即拉 / 起停）。
    // bootstrapping/setters 故意不进依赖（经 ref 读取，避免 bootstrap 期间频繁重挂）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePanel, activeWorkspaceId, loading]);

  // 独立的 effect：检测 loading true→false 过渡，做"停前补拉"。
  // 不能并入上面的 effect——上面 effect 在 loading=false 时会直接 return 不轮询，
  // 这里专门负责"结束这一刻"的最终拉取。
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;
    // 结束时全量拉三件套（不限当前面板）：生成中用户多停在 chat（不参与轮询），
    // 只补拉当前面板会让 script/characters/worldview 保持旧空数据，
    // 直到下次 bootstrap（重启/切换工作区）才可见。
    if (wasLoading && !loading && activeWorkspaceId) {
      void (async () => {
        const gotAny = await pollAllPanels(activeWorkspaceId);
        // 兜底：final 事件可能先于产物文件落盘/索引就绪，首拉全空时延迟重拉一次。
        if (!gotAny) {
          await new Promise((resolve) => setTimeout(resolve, FINAL_RETRY_DELAY_MS));
          await pollAllPanels(activeWorkspaceId);
        }
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);
}
