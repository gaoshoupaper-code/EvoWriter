/**
 * 个人技能库与账号全局启用状态（REQ-20261102-135406）。
 * 待选候选不等于已启用；确认某阶段后采用服务端返回的完整权威状态。
 */
import { create } from "zustand";
import {
  deleteWritingSkill,
  listEnabledWritingSkills,
  listWritingSkills,
  setEnabledWritingSkills,
  type EnabledWritingSkills,
  type WritingSkill,
  type WritingStage,
} from "../lib/api";

interface WritingSkillsState {
  skills: WritingSkill[];
  skillsLoading: boolean;
  skillsError: string;
  enabled: WritingSkill[];
  enabledLoaded: boolean;
  enabledLoading: boolean;
  enabledError: string;
  pendingStages: WritingStage[];
  candidates: WritingSkill[];

  loadSkills: () => Promise<void>;
  loadEnabled: () => Promise<void>;
  saveEnabled: (stage: WritingStage, skillIds: string[]) => Promise<boolean>;
  removeSkill: (skillId: string) => Promise<boolean>;
  clear: () => void;
}

/** 每个阶段同时启用上限（REQ-20261025-205943 DEC-005；跨段不挤占） */
export const MAX_PER_STAGE = 5;

// 清空账号使所有在途操作失效，防止旧账号的迟到响应重新填回列表。
let accountEpoch = 0;
let skillsReqSeq = 0;
let enabledReqSeq = 0;

function enabledState(data: EnabledWritingSkills) {
  return { enabled: data.enabled, pendingStages: data.pending_stages, candidates: data.candidates, enabledLoaded: true };
}

export const useWritingSkillsStore = create<WritingSkillsState>((set) => ({
  skills: [],
  skillsLoading: false,
  skillsError: "",
  enabled: [],
  enabledLoaded: false,
  enabledLoading: false,
  enabledError: "",
  pendingStages: [],
  candidates: [],

  loadSkills: async () => {
    const epoch = accountEpoch;
    const seq = ++skillsReqSeq;
    set({ skillsLoading: true, skillsError: "" });
    try {
      const skills = await listWritingSkills();
      if (epoch !== accountEpoch || seq !== skillsReqSeq) return;
      set({ skills });
    } catch (error) {
      if (epoch === accountEpoch && seq === skillsReqSeq) set({ skillsError: error instanceof Error ? error.message : "技能加载失败。" });
      throw error;
    } finally {
      if (epoch === accountEpoch && seq === skillsReqSeq) set({ skillsLoading: false });
    }
  },

  loadEnabled: async () => {
    const epoch = accountEpoch;
    const seq = ++enabledReqSeq;
    set({ enabledLoading: true, enabledError: "" });
    try {
      const data = await listEnabledWritingSkills();
      if (epoch !== accountEpoch || seq !== enabledReqSeq) return;
      set(enabledState(data));
    } catch (error) {
      if (epoch === accountEpoch && seq === enabledReqSeq) set({ enabledError: error instanceof Error ? error.message : "启用设置加载失败。" });
      throw error;
    } finally {
      if (epoch === accountEpoch && seq === enabledReqSeq) set({ enabledLoading: false });
    }
  },

  saveEnabled: async (stage, skillIds) => {
    const epoch = accountEpoch;
    const seq = ++enabledReqSeq;
    try {
      const data = await setEnabledWritingSkills(stage, skillIds);
      if (epoch !== accountEpoch || seq !== enabledReqSeq) return false;
      set({ ...enabledState(data), enabledError: "" });
      return true;
    } finally {
      if (epoch === accountEpoch && seq === enabledReqSeq) set({ enabledLoading: false });
    }
  },

  removeSkill: async (skillId: string) => {
    const epoch = accountEpoch;
    await deleteWritingSkill(skillId);
    if (epoch !== accountEpoch) return false;
    set((s) => ({
      skills: s.skills.filter((sk) => sk.skill_id !== skillId),
      // 服务端已联动解绑；本地同步清掉启用项
      enabled: s.enabled.filter((sk) => sk.skill_id !== skillId),
      candidates: s.candidates.filter((sk) => sk.skill_id !== skillId),
    }));
    return true;
  },

  clear: () => {
    accountEpoch += 1;
    skillsReqSeq += 1;
    enabledReqSeq += 1;
    set({ skills: [], skillsLoading: false, skillsError: "", enabled: [], enabledLoaded: false, enabledLoading: false, enabledError: "", pendingStages: [], candidates: [] });
  },
}));
