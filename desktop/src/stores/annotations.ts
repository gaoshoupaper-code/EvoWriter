/**
 * 标注 store（REQ-20261027-213246 FR-001/002/004）。
 *
 - annotations：当前工作区标注全集（状态随服务端复验更新）
 * - interacting：划词/写意见进行中——usePanelPolling 的暂停门（FR-001，
 *   刷新会打断选区与丢失正在写的意见）
 * - dispatch：只做打包（服务端复验＋组装），消息由界面经 executionStore.submit
 *   走聊天通道发出（复用排队语义 DEC-011）
 */
import { create } from "zustand";
import type { ArtifactAnnotation, AnnotationDispatchResult } from "@/lib/types";
import {
  clearAnnotations,
  createAnnotation,
  deleteAnnotation,
  dispatchAnnotations,
  fetchAnnotations,
  updateAnnotation,
  type AnnotationCreateInput,
} from "@/lib/api";

type AnnotationsState = {
  workspaceId: string | null;
  annotations: ArtifactAnnotation[];
  interacting: boolean;

  setInteracting: (v: boolean) => void;
  /** 拉取（切工作区自动换轨；同工作区即刷新，读前服务端会做锚点复验）。 */
  load: (workspaceId: string) => Promise<void>;
  create: (input: AnnotationCreateInput) => Promise<ArtifactAnnotation>;
  update: (
    annotationId: string,
    patch: { note?: string; custom_label?: string; reanchor?: { quote: string; context_before?: string; context_after?: string } },
  ) => Promise<ArtifactAnnotation>;
  remove: (annotationId: string) => Promise<void>;
  clear: (statuses: string[]) => Promise<number>;
  dispatch: () => Promise<AnnotationDispatchResult>;
};

export const useAnnotationsStore = create<AnnotationsState>((set, get) => ({
  workspaceId: null,
  annotations: [],
  interacting: false,

  setInteracting: (v) => set({ interacting: v }),

  load: async (workspaceId) => {
    if (get().workspaceId !== workspaceId) {
      set({ workspaceId, annotations: [] });
    }
    try {
      const { annotations } = await fetchAnnotations(workspaceId);
      // 竞态保护：仅当仍指向同一工作区才写入
      if (get().workspaceId === workspaceId) set({ annotations });
    } catch {
      // 读失败保持现状：标注面板下次打开/轮询再试，不阻塞产物查看
    }
  },

  create: async (input) => {
    const ann = await createAnnotation(get().workspaceId!, input);
    set((s) => ({ annotations: [...s.annotations, ann] }));
    return ann;
  },

  update: async (annotationId, patch) => {
    const ann = await updateAnnotation(get().workspaceId!, annotationId, patch);
    set((s) => ({
      annotations: s.annotations.map((a) => (a.id === ann.id ? ann : a)),
    }));
    return ann;
  },

  remove: async (annotationId) => {
    await deleteAnnotation(get().workspaceId!, annotationId);
    set((s) => ({ annotations: s.annotations.filter((a) => a.id !== annotationId) }));
  },

  clear: async (statuses) => {
    const { deleted } = await clearAnnotations(get().workspaceId!, statuses);
    set((s) => ({
      annotations: s.annotations.filter((a) => !statuses.includes(a.status as string)),
    }));
    return deleted;
  },

  dispatch: () => dispatchAnnotations(get().workspaceId!),
}));

/** 按 rel 过滤的只读选择器（组件用）。 */
export function annotationsOfRel(
  state: { annotations: ArtifactAnnotation[] },
  rel: string,
): ArtifactAnnotation[] {
  return state.annotations.filter((a) => a.artifact_rel === rel);
}
