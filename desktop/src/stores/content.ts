/**
 * contentStore —— 内容面板数据 + 轮询（从 home.tsx 迁移）
 *
 * 职责：大纲/人物/物品/世界观/故事线的数据 state（v9：细纲/正文面板已删，FR-009），
 * 接收 workspaceStore.bootstrap 返回的 ContentData 并填充。
 * 轮询逻辑保留在 usePanelPolling hook（它直接读这个 store 的 setter）。
 */
import { create } from "zustand";
import type { CharacterMarkdownFile, ObjectMarkdownFile, PanoramaEvent, StorylineEntry } from "../lib/types";
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
  storylineFormat: string;
  activeStorylineFilename: string;
  objects: ObjectMarkdownFile[];
  objectsLoading: boolean;
  activeObjectFilename: string;

  // actions
  setContentData: (data: ContentData) => void;
  clearContent: () => void;
  setStorylineMarkdown: (v: string) => void;
  setStorylineEntries: (v: StorylineEntry[]) => void;
  setStorylinePanorama: (v: PanoramaEvent[]) => void;
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
  storylineFormat: "v2",
  activeStorylineFilename: "",
  objects: [],
  objectsLoading: false,
  activeObjectFilename: "",

  setContentData: (data) =>
    set({
      storylineMarkdown: data.storylineMarkdown,
      storylineEntries: data.storylineEntries,
      storylinePanorama: data.storylinePanorama,
      storylineFormat: data.storylineFormat,
      activeStorylineFilename: data.activeStorylineFilename,
      worldviewMarkdown: data.worldviewMarkdown,
      characters: data.characters,
      activeCharacterFilename: data.activeCharacterFilename,
      objects: data.objects,
      activeObjectFilename: data.activeObjectFilename,
    }),

  clearContent: () =>
    set({
      storylineMarkdown: "",
      storylineEntries: [],
      storylinePanorama: [],
      storylineFormat: "v2",
      activeStorylineFilename: "",
      worldviewMarkdown: "",
      characters: [],
      activeCharacterFilename: "",
      objects: [],
      activeObjectFilename: "",
    }),

  setStorylineMarkdown: (v) => set({ storylineMarkdown: v }),
  setStorylineEntries: (v) => set({ storylineEntries: v }),
  setStorylinePanorama: (v) => set({ storylinePanorama: v }),
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
}));
