/**
 * contentStore —— 内容面板数据 + 轮询（从 home.tsx 迁移）
 *
 * 职责：大纲/人物/物品/世界观/故事线的数据 state（v9：细纲/正文面板已删，FR-009），
 * 接收 workspaceStore.bootstrap 返回的 ContentData 并填充。
 * 轮询逻辑保留在 usePanelPolling hook（它直接读这个 store 的 setter）。
 */
import { create } from "zustand";
import type { CharacterMarkdownFile, ObjectMarkdownFile, OutlineFileEntry, PanoramaEvent, PipelineStatus, RhythmData, StorylineEntry } from "../lib/types";
import type { ContentData } from "./workspace";

interface ContentState {
  characters: CharacterMarkdownFile[];
  charactersLoading: boolean;
  activeCharacterFilename: string;
  worldviewMarkdown: string;
  worldviewLoading: boolean;
  storylineMarkdown: string;
  storylineEntries: StorylineEntry[];
  storylinePanorama: PanoramaEvent[];
  storylineRhythm: RhythmData | null;
  storylineFormat: string;
  activeStorylineFilename: string;
  objects: ObjectMarkdownFile[];
  objectsLoading: boolean;
  activeObjectFilename: string;
  // 双 agent 管线（REQ-20261011-142529 FR-010）
  outlineKind: string;
  outlinePipeline: PipelineStatus | null;
  outlineFiles: OutlineFileEntry[];
  activeOutlineRel: string;

  // actions
  setContentData: (data: ContentData) => void;
  clearContent: () => void;
  setStorylineMarkdown: (v: string) => void;
  setStorylineEntries: (v: StorylineEntry[]) => void;
  setStorylinePanorama: (v: PanoramaEvent[]) => void;
  setStorylineRhythm: (v: RhythmData | null) => void;
  setStorylineFormat: (v: string) => void;
  setActiveStorylineFilename: (v: string) => void;
  setWorldviewMarkdown: (v: string) => void;
  setCharacters: (v: CharacterMarkdownFile[]) => void;
  setActiveCharacterFilename: (v: string) => void;
  setCharactersLoading: (v: boolean) => void;
  setWorldviewLoading: (v: boolean) => void;
  setObjects: (v: ObjectMarkdownFile[]) => void;
  setActiveObjectFilename: (v: string) => void;
  setObjectsLoading: (v: boolean) => void;
  setOutline: (v: { kind: string; pipeline: PipelineStatus; files: OutlineFileEntry[] }) => void;
  /** 只更新管线相位（SSE pipeline 事件即时通道，DEC-013）；产物文件仍由轮询维护 */
  setOutlinePipeline: (v: PipelineStatus) => void;
  setActiveOutlineRel: (v: string) => void;
}

export const useContentStore = create<ContentState>((set) => ({
  characters: [],
  charactersLoading: false,
  activeCharacterFilename: "",
  worldviewMarkdown: "",
  worldviewLoading: false,
  storylineMarkdown: "",
  storylineEntries: [],
  storylinePanorama: [],
  storylineRhythm: null,
  storylineFormat: "v2",
  activeStorylineFilename: "",
  objects: [],
  objectsLoading: false,
  activeObjectFilename: "",
  outlineKind: "pipeline",
  outlinePipeline: null,
  outlineFiles: [],
  activeOutlineRel: "",

  setContentData: (data) =>
    set({
      storylineMarkdown: data.storylineMarkdown,
      storylineEntries: data.storylineEntries,
      storylinePanorama: data.storylinePanorama,
      storylineRhythm: data.storylineRhythm,
      storylineFormat: data.storylineFormat,
      activeStorylineFilename: data.activeStorylineFilename,
      worldviewMarkdown: data.worldviewMarkdown,
      characters: data.characters,
      activeCharacterFilename: data.activeCharacterFilename,
      objects: data.objects,
      activeObjectFilename: data.activeObjectFilename,
      outlineKind: data.outlineKind,
      outlinePipeline: data.outlinePipeline,
      outlineFiles: data.outlineFiles,
      activeOutlineRel: data.activeOutlineRel,
    }),

  clearContent: () =>
    set({
      storylineMarkdown: "",
      storylineEntries: [],
      storylinePanorama: [],
      storylineRhythm: null,
      storylineFormat: "v2",
      activeStorylineFilename: "",
      worldviewMarkdown: "",
      characters: [],
      activeCharacterFilename: "",
      objects: [],
      activeObjectFilename: "",
      outlineKind: "pipeline",
      outlinePipeline: null,
      outlineFiles: [],
      activeOutlineRel: "",
    }),

  setStorylineMarkdown: (v) => set({ storylineMarkdown: v }),
  setStorylineEntries: (v) => set({ storylineEntries: v }),
  setStorylinePanorama: (v) => set({ storylinePanorama: v }),
  setStorylineRhythm: (v) => set({ storylineRhythm: v }),
  setStorylineFormat: (v) => set({ storylineFormat: v }),
  setActiveStorylineFilename: (v) => set({ activeStorylineFilename: v }),
  setWorldviewMarkdown: (v) => set({ worldviewMarkdown: v }),
  setCharacters: (v) => set({ characters: v }),
  setActiveCharacterFilename: (v) => set({ activeCharacterFilename: v }),
  setCharactersLoading: (v) => set({ charactersLoading: v }),
  setWorldviewLoading: (v) => set({ worldviewLoading: v }),
  setObjects: (v) => set({ objects: v }),
  setActiveObjectFilename: (v) => set({ activeObjectFilename: v }),
  setObjectsLoading: (v) => set({ objectsLoading: v }),
  setOutline: (v) =>
    set((s) => ({
      outlineKind: v.kind,
      outlinePipeline: v.pipeline,
      outlineFiles: v.files,
      activeOutlineRel: v.files.some((f) => f.rel === s.activeOutlineRel)
        ? s.activeOutlineRel
        : (v.files[0]?.rel ?? ""),
    })),
  setOutlinePipeline: (v) => set({ outlinePipeline: v }),
  setActiveOutlineRel: (v) => set({ activeOutlineRel: v }),
}));
