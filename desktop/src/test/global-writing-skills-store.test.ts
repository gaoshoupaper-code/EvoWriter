import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWritingSkillsStore } from "@/stores/writingSkills";
import { listEnabledWritingSkills, listWritingSkills, setEnabledWritingSkills, type WritingSkill } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  listWritingSkills: vi.fn(), listEnabledWritingSkills: vi.fn(),
  setEnabledWritingSkills: vi.fn(), deleteWritingSkill: vi.fn(),
}));

function skill(id: string): WritingSkill {
  return { skill_id: id, owner_id: "u1", name: id, description: "说明", content: "指令", stage: "text", created_at: "", updated_at: "" };
}
const state = (enabled: WritingSkill[] = []) => ({ enabled, pending_stages: [] as WritingSkill["stage"][], candidates: [] as WritingSkill[] });

beforeEach(() => {
  vi.resetAllMocks();
  useWritingSkillsStore.getState().clear();
  vi.mocked(listEnabledWritingSkills).mockResolvedValue(state());
  vi.mocked(listWritingSkills).mockResolvedValue([]);
});

describe("账号全局技能状态（AC-003/005/009）", () => {
  it("不传作品参数，接收待选择阶段与完整候选", async () => {
    vi.mocked(listEnabledWritingSkills).mockResolvedValue({ enabled: [], pending_stages: ["text"], candidates: [skill("候选")] });
    await useWritingSkillsStore.getState().loadEnabled();
    expect(listEnabledWritingSkills).toHaveBeenCalledExactlyOnceWith();
    expect(useWritingSkillsStore.getState().pendingStages).toEqual(["text"]);
    expect(useWritingSkillsStore.getState().candidates.map((s) => s.skill_id)).toEqual(["候选"]);
  });

  it("按阶段保存，失败保留此前清单", async () => {
    useWritingSkillsStore.setState({ enabled: [skill("旧")], enabledLoaded: true });
    vi.mocked(setEnabledWritingSkills).mockRejectedValueOnce(new Error("保存失败"));
    await expect(useWritingSkillsStore.getState().saveEnabled("text", ["新"])).rejects.toThrow("保存失败");
    expect(useWritingSkillsStore.getState().enabled.map((s) => s.skill_id)).toEqual(["旧"]);
    vi.mocked(setEnabledWritingSkills).mockResolvedValueOnce(state([skill("新")]));
    await useWritingSkillsStore.getState().saveEnabled("text", ["新"]);
    expect(setEnabledWritingSkills).toHaveBeenLastCalledWith("text", ["新"]);
    expect(useWritingSkillsStore.getState().enabled.map((s) => s.skill_id)).toEqual(["新"]);
  });

  it("清空账号后丢弃旧账号迟到的技能、启用与保存响应", async () => {
    let releaseSkills!: (value: WritingSkill[]) => void;
    let releaseEnabled!: (value: ReturnType<typeof state>) => void;
    let releaseSave!: (value: ReturnType<typeof state>) => void;
    vi.mocked(listWritingSkills).mockImplementationOnce(() => new Promise((resolve) => { releaseSkills = resolve; }));
    vi.mocked(listEnabledWritingSkills).mockImplementationOnce(() => new Promise((resolve) => { releaseEnabled = resolve; }));
    vi.mocked(setEnabledWritingSkills).mockImplementationOnce(() => new Promise((resolve) => { releaseSave = resolve; }));
    const requests = [useWritingSkillsStore.getState().loadSkills(), useWritingSkillsStore.getState().loadEnabled(), useWritingSkillsStore.getState().saveEnabled("text", ["旧账号"])];
    useWritingSkillsStore.getState().clear();
    releaseSkills([skill("旧账号")]); releaseEnabled(state([skill("旧账号")])); releaseSave(state([skill("旧账号")]));
    await Promise.all(requests);
    expect(useWritingSkillsStore.getState().skills).toEqual([]);
    expect(useWritingSkillsStore.getState().enabled).toEqual([]);
    expect(useWritingSkillsStore.getState().enabledLoaded).toBe(false);
  });
});
