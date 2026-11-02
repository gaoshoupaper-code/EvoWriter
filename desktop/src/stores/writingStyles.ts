import { create } from "zustand";
import { listWritingStyles, renameWritingStyle, deleteWritingStyle, saveWorkspaceWritingStyle, type WritingStyleAsset } from "@/lib/api";

interface WritingStylesState {
  assets: WritingStyleAsset[];
  loaded: boolean;
  loading: boolean;
  error: string;
  load: () => Promise<void>;
  save: (workspaceId: string, name: string, targetStyleId?: string) => Promise<WritingStyleAsset | null>;
  rename: (styleId: string, name: string) => Promise<boolean>;
  remove: (styleId: string) => Promise<boolean>;
  clear: () => void;
}

// 账号切换使全部在途响应失效；写入返回值直接更新列表，不重复提交刷新。
let accountEpoch = 0;
let requestSequence = 0;

export const useWritingStylesStore = create<WritingStylesState>((set) => ({
  assets: [], loaded: false, loading: false, error: "",
  load: async () => {
    const epoch = accountEpoch, sequence = ++requestSequence;
    set({ loading: true, error: "" });
    try {
      const assets = await listWritingStyles();
      if (epoch === accountEpoch && sequence === requestSequence) set({ assets, loaded: true });
    } catch (error) {
      if (epoch === accountEpoch && sequence === requestSequence) set({ error: error instanceof Error ? error.message : "文风加载失败。" });
      throw error;
    } finally {
      if (epoch === accountEpoch && sequence === requestSequence) set({ loading: false });
    }
  },
  save: async (workspaceId, name, targetStyleId) => {
    const epoch = accountEpoch;
    const asset = await saveWorkspaceWritingStyle(workspaceId, name, targetStyleId);
    if (epoch !== accountEpoch) return null;
    ++requestSequence;
    set((state) => ({ assets: [asset, ...state.assets.filter((item) => item.style_id !== asset.style_id)], loaded: true, loading: false, error: "" }));
    return asset;
  },
  rename: async (styleId, name) => {
    const epoch = accountEpoch;
    const asset = await renameWritingStyle(styleId, name);
    if (epoch !== accountEpoch) return false;
    ++requestSequence;
    set((state) => ({ assets: state.assets.map((item) => item.style_id === styleId ? asset : item), loading: false }));
    return true;
  },
  remove: async (styleId) => {
    const epoch = accountEpoch;
    await deleteWritingStyle(styleId);
    if (epoch !== accountEpoch) return false;
    ++requestSequence;
    set((state) => ({ assets: state.assets.filter((item) => item.style_id !== styleId), loading: false }));
    return true;
  },
  clear: () => {
    ++accountEpoch; ++requestSequence;
    set({ assets: [], loaded: false, loading: false, error: "" });
  },
}));
