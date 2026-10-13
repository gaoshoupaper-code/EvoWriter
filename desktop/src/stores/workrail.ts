/**
 * workrailStore —— 作品浮窗（REQ-20261013-181350 FR-001）的 UI 状态。
 *
 * 职责：浮窗三态（closed=只剩唤回按钮 / mini=概要小卡 / expanded=完整浮窗，
 * 会话内不持久化，刷新回默认 mini）、交付高亮（对比运行起点快照算「本次新增」，
 * 4 秒后自动熄灭）、轮询失败标记（浮窗显示重试入口）。
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

/** 浮窗三态：closed 只剩右上角唤回按钮；mini 为默认态（刷新回 mini，避免「找不到」） */
export type WorkrailFloatMode = "mini" | "expanded" | "closed";

interface WorkrailState {
  /** 浮窗态：默认 mini；不持久化（刷新回 mini，避免「找不到」） */
  floatMode: WorkrailFloatMode;
  /** 轮询失败标记（成功即清除；浮窗据此显示「状态获取失败·重试」） */
  pollError: boolean;
  /** 本次交付的新增条目高亮 */
  highlightKeys: Set<ArtifactKey>;
  /** 运行起点快照：交付时 diff 出「本次新增」 */
  baselineKeys: Set<ArtifactKey> | null;
  _highlightTimer: ReturnType<typeof setTimeout> | null;

  setFloatMode: (mode: WorkrailFloatMode) => void;
  markPollOk: (ok: boolean) => void;
  /** 任务开始（loading true）时快照当前条目集 */
  captureBaseline: () => void;
  /** 交付卡「查看新产物」：展开浮窗并高亮本次新增 */
  revealDelivery: () => void;
  clearHighlight: () => void;
}

export const useWorkrailStore = create<WorkrailState>((set, get) => ({
  floatMode: "mini",
  pollError: false,
  highlightKeys: new Set(),
  baselineKeys: null,
  _highlightTimer: null,

  setFloatMode: (mode) => set({ floatMode: mode }),

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
      floatMode: "expanded",
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
