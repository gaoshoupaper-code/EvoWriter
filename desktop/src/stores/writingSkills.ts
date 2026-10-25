/**
 * writingSkillsStore —— 用户自定义写作技能
 * （REQ-20261023-000000 建库；REQ-20261025-205943 阶段维度）
 *
 * 职责：个人技能库列表（设置页板块）+ 当前书启用列表（侧栏技能模块）。
 * 勾选即保存：saveEnabled 调服务端成功后重拉权威列表（含 stage，供
 * 分段角标即时准确）；每段 ≤MAX_PER_STAGE 条由调用方先拦（服务端
 * 双保险再拦一次）。
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
  /** 当前书启用的技能全对象（含 stage；角标/浮层勾选都从这推导） */
  enabled: WritingSkill[];
  enabledWorkspaceId: string | null;

  loadSkills: () => Promise<void>;
  loadEnabled: (workspaceId: string) => Promise<void>;
  saveEnabled: (workspaceId: string, skillIds: string[]) => Promise<boolean>;
  removeSkill: (skillId: string) => Promise<boolean>;
  clear: () => void;
}

/** 每个阶段同时启用上限（REQ-20261025-205943 DEC-005；跨段不挤占） */
export const MAX_PER_STAGE = 5;

// 启用列表请求序号：仅最新请求的响应可写入（切书连点时旧响应全部丢弃）
let enabledReqSeq = 0;

export const useWritingSkillsStore = create<WritingSkillsState>((set, get) => ({
  skills: [],
  skillsLoading: false,
  enabled: [],
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
    const seq = ++enabledReqSeq;
    const enabled = await listEnabledWritingSkills(workspaceId);
    // 切书后陈旧响应不得写入新书启用列表：序号已被更新请求取代即丢弃
    if (seq !== enabledReqSeq) return;
    set({
      enabled,
      enabledWorkspaceId: workspaceId,
    });
  },

  saveEnabled: async (workspaceId: string, skillIds: string[]) => {
    const res = await setEnabledWritingSkills(workspaceId, skillIds);
    // 响应自带保存后的权威启用列表（含 stage），仍是同一本书时直接采用。
    // 不发二次回拉：保存期间切书的在途请求序号未受影响，仍是最终赢家
    if (get().enabledWorkspaceId === workspaceId) {
      set({ enabled: res.skills });
    }
    return true;
  },

  removeSkill: async (skillId: string) => {
    await deleteWritingSkill(skillId);
    set((s) => ({
      skills: s.skills.filter((sk) => sk.skill_id !== skillId),
      // 服务端已联动解绑；本地同步清掉启用项
      enabled: s.enabled.filter((sk) => sk.skill_id !== skillId),
    }));
    return true;
  },

  clear: () => set({ skills: [], enabled: [], enabledWorkspaceId: null }),
}));
