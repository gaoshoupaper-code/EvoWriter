/**
 * workrailStore —— 右侧作品栏（REQ-20261012-202540 FR-001/FR-004）的 UI 状态。
 *
 * 职责：收起态（会话内不持久化，刷新回默认展开）、交付高亮（对比运行起点
 * 快照算「本次新增」，4 秒后自动熄灭）、轮询失败标记（作品栏显示重试入口）。
 * 数据本体在 contentStore，这里只放视图态。
 */
import { create } from "zustand";
import { useContentStore } from "./content";

/** 产物条目键：分组前缀 + 定位 id，交付高亮与索引跳转共用 */
export type ArtifactKey =
  | `outline:${string}`
  | `character:${string}`
  | `object:${string}`
  | "worldview";

function currentArtifactKeys(): Set<ArtifactKey> {
  const c = useContentStore.getState();
  const keys = new Set<ArtifactKey>();
  for (const f of c.outlineFiles) keys.add(`outline:${f.rel}`);
  for (const ch of c.characters) keys.add(`character:${ch.filename}`);
  for (const o of c.objects) keys.add(`object:${o.filename}`);
  if (c.worldviewMarkdown.trim()) keys.add("worldview");
  return keys;
}

const HIGHLIGHT_AUTO_CLEAR_MS = 4000;

interface WorkrailState {
  /** 收起态：默认展开；不持久化（刷新回展开，避免「找不到」） */
  collapsed: boolean;
  /** 轮询失败标记（成功即清除；作品栏据此显示「状态获取失败·重试」） */
  pollError: boolean;
  /** 本次交付的新增条目高亮 */
  highlightKeys: Set<ArtifactKey>;
  /** 运行起点快照：交付时 diff 出「本次新增」 */
  baselineKeys: Set<ArtifactKey> | null;
  _highlightTimer: ReturnType<typeof setTimeout> | null;

  toggleCollapsed: (v?: boolean) => void;
  markPollOk: (ok: boolean) => void;
  /** 任务开始（loading true）时快照当前条目集 */
  captureBaseline: () => void;
  /** 交付卡「查看新产物」：展开作品栏并高亮本次新增 */
  revealDelivery: () => void;
  clearHighlight: () => void;
}

export const useWorkrailStore = create<WorkrailState>((set, get) => ({
  collapsed: false,
  pollError: false,
  highlightKeys: new Set(),
  baselineKeys: null,
  _highlightTimer: null,

  toggleCollapsed: (v) =>
    set((s) => ({ collapsed: v ?? !s.collapsed })),

  markPollOk: (ok) => set({ pollError: !ok }),

  captureBaseline: () => {
    set({ baselineKeys: currentArtifactKeys() });
  },

  revealDelivery: () => {
    const { baselineKeys, _highlightTimer } = get();
    // 新增 = 当前 - 起点快照；无快照（恢复会话等）则高亮全部现有条目
    const current = currentArtifactKeys();
    const fresh = baselineKeys
      ? new Set([...current].filter((k) => !baselineKeys.has(k)))
      : current;
    if (_highlightTimer) clearTimeout(_highlightTimer);
    set({
      collapsed: false,
      highlightKeys: fresh,
      _highlightTimer: setTimeout(() => get().clearHighlight(), HIGHLIGHT_AUTO_CLEAR_MS),
    });
  },

  clearHighlight: () => {
    const { _highlightTimer } = get();
    if (_highlightTimer) clearTimeout(_highlightTimer);
    set({ highlightKeys: new Set(), _highlightTimer: null });
  },
}));
