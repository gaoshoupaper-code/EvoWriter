import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EnabledWritingSkills, WritingSkill } from "@/lib/api";
import { useWritingSkillsStore } from "@/stores/writingSkills";
import { listEnabledWritingSkills, setEnabledWritingSkills } from "@/lib/api";

vi.mock("@/lib/api", () => ({ listWritingSkills: vi.fn(), deleteWritingSkill: vi.fn(), listEnabledWritingSkills: vi.fn(), setEnabledWritingSkills: vi.fn() }));
const skill = (id: string): WritingSkill => ({ skill_id: id, owner_id: "u1", name: id, description: "d", content: "c", stage: "text", created_at: "", updated_at: "" });
const state = (...ids: string[]): EnabledWritingSkills => ({ enabled: ids.map(skill), pending_stages: [], candidates: [] });

beforeEach(() => { vi.resetAllMocks(); useWritingSkillsStore.getState().clear(); });

describe("全局启用清单请求竞态（REQ-20261102-135406）", () => {
  it("最新刷新胜出，旧的慢响应不能覆盖全局清单", async () => {
    let release!: (data: EnabledWritingSkills) => void;
    vi.mocked(listEnabledWritingSkills).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; })).mockResolvedValueOnce(state("新"));
    const slow = useWritingSkillsStore.getState().loadEnabled();
    await useWritingSkillsStore.getState().loadEnabled();
    release(state("旧")); await slow;
    expect(useWritingSkillsStore.getState().enabled.map((s) => s.skill_id)).toEqual(["新"]);
  });

  it("顺序刷新正常采用最新服务端状态", async () => {
    vi.mocked(listEnabledWritingSkills).mockResolvedValueOnce(state("一")).mockResolvedValueOnce(state("二"));
    await useWritingSkillsStore.getState().loadEnabled();
    await useWritingSkillsStore.getState().loadEnabled();
    expect(useWritingSkillsStore.getState().enabled.map((s) => s.skill_id)).toEqual(["二"]);
  });

  it("保存使更早的读取失效，不被旧读取响应覆盖", async () => {
    let release!: (data: EnabledWritingSkills) => void;
    vi.mocked(listEnabledWritingSkills).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    vi.mocked(setEnabledWritingSkills).mockResolvedValueOnce(state("已保存"));
    const read = useWritingSkillsStore.getState().loadEnabled();
    await useWritingSkillsStore.getState().saveEnabled("text", ["已保存"]);
    release(state("保存前")); await read;
    expect(useWritingSkillsStore.getState().enabled.map((s) => s.skill_id)).toEqual(["已保存"]);
    expect(useWritingSkillsStore.getState().enabledLoading).toBe(false);
  });
});
