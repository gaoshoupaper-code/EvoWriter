/**
 * writingSkillsStore —— 用户自定义写作技能（REQ-20261023-000000）
 *
 * 职责：个人技能库列表（设置页板块）+ 当前书启用列表（写作页勾选浮层）。
 * 勾选即保存：saveEnabled 直接调服务端，成功才更新本地；每书 ≤5 条由
 * 调用方先拦（服务端双保险再拦一次）。
 */
import { create } from "zustand";
import {
  deleteWritingSkill,
  listEnabledWritingSkills,
  listWritingSkills,
  setEnabledWritingSkills,
  type WritingSkill,
} from "../lib/api";

interface WritingSkillsState {
  skills: WritingSkill[];
  skillsLoading: boolean;
  enabledIds: string[];
  enabledWorkspaceId: string | null;

  loadSkills: () => Promise<void>;
  loadEnabled: (workspaceId: string) => Promise<void>;
  saveEnabled: (workspaceId: string, skillIds: string[]) => Promise<boolean>;
  removeSkill: (skillId: string) => Promise<boolean>;
  clear: () => void;
}

export const MAX_ENABLED_PER_WORKSPACE = 5;

export const useWritingSkillsStore = create<WritingSkillsState>((set, get) => ({
  skills: [],
  skillsLoading: false,
  enabledIds: [],
  enabledWorkspaceId: null,

  loadSkills: async () => {
    set({ skillsLoading: true });
    try {
      const skills = await listWritingSkills();
      set({ skills });
    } finally {
      set({ skillsLoading: false });
    }
  },

  loadEnabled: async (workspaceId: string) => {
    const enabled = await listEnabledWritingSkills(workspaceId);
    // 切书后陈旧响应不得写入新书的启用列表
    if (get().enabledWorkspaceId !== null && get().enabledWorkspaceId !== workspaceId) return;
    set({
      enabledIds: enabled.map((s) => s.skill_id),
      enabledWorkspaceId: workspaceId,
    });
  },

  saveEnabled: async (workspaceId: string, skillIds: string[]) => {
    await setEnabledWritingSkills(workspaceId, skillIds);
    // 只有仍是同一本书时才落本地（保存期间切书的竞态）
    if (get().enabledWorkspaceId === workspaceId) {
      set({ enabledIds: skillIds });
    }
    return true;
  },

  removeSkill: async (skillId: string) => {
    await deleteWritingSkill(skillId);
    set((s) => ({
      skills: s.skills.filter((sk) => sk.skill_id !== skillId),
      // 服务端已联动解绑；本地同步清掉角标计数
      enabledIds: s.enabledIds.filter((id) => id !== skillId),
    }));
    return true;
  },

  clear: () => set({ skills: [], enabledIds: [], enabledWorkspaceId: null }),
}));
