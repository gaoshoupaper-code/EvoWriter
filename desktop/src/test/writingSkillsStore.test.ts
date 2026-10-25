/**
 * writingSkillsStore 竞态回归（REQ-20261025-205943 修复回路）：
 * 切书连点时旧书响应不得覆盖新书启用列表——loadEnabled 用请求序号
 * 保证「最新请求胜出」，取代旧的倒置守卫（旧守卫会把切书后的首个
 * 响应整个丢掉，角标停留在上一本书）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WritingSkill } from "@/lib/api";
import { useWritingSkillsStore } from "@/stores/writingSkills";

let mockEnabledByCall: (() => Promise<WritingSkill[]>)[] = [];
let mockSaveResult: { status: string; enabled: string[]; skills: WritingSkill[] } = {
  status: "ok", enabled: [], skills: [],
};

vi.mock("@/lib/api", () => ({
  listWritingSkills: vi.fn(async () => [] as WritingSkill[]),
  createWritingSkill: vi.fn(),
  updateWritingSkill: vi.fn(),
  deleteWritingSkill: vi.fn(),
  listEnabledWritingSkills: vi.fn(() =>
    mockEnabledByCall.length ? mockEnabledByCall.shift()!() : Promise.resolve([]),
  ),
  setEnabledWritingSkills: vi.fn(async () => mockSaveResult),
}));

function sk(id: string): WritingSkill {
  return {
    skill_id: id, owner_id: "u1", name: id, description: "d", content: "c",
    stage: "text", created_at: "", updated_at: "",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockEnabledByCall = [];
  mockSaveResult = { status: "ok", enabled: [], skills: [] };
  useWritingSkillsStore.setState({
    skills: [], skillsLoading: false, enabled: [], enabledWorkspaceId: null,
  });
});

describe("writingSkillsStore 切书竞态（修复回路）", () => {
  it("切书后新书响应生效，旧书慢响应被丢弃（最新请求胜出）", async () => {
    // 书 A 的响应被人为延迟，慢于书 B 的响应
    let releaseA: (v: WritingSkill[]) => void = () => {};
    const slowA = new Promise<WritingSkill[]>((resolve) => { releaseA = resolve; });
    mockEnabledByCall = [
      () => slowA,
      () => Promise.resolve([sk("B1"), sk("B2")]),
    ];

    const pA = useWritingSkillsStore.getState().loadEnabled("ws-a");
    const pB = useWritingSkillsStore.getState().loadEnabled("ws-b");
    await pB;
    expect(useWritingSkillsStore.getState().enabledWorkspaceId).toBe("ws-b");
    expect(useWritingSkillsStore.getState().enabled.map((s) => s.skill_id))
      .toEqual(["B1", "B2"]);

    releaseA([sk("A1")]); // 旧书响应此刻才回来
    await pA;
    expect(useWritingSkillsStore.getState().enabledWorkspaceId).toBe("ws-b");
    expect(useWritingSkillsStore.getState().enabled.map((s) => s.skill_id))
      .toEqual(["B1", "B2"]); // 不得被旧响应覆盖
  });

  it("顺序请求正常落最新一本", async () => {
    mockEnabledByCall = [
      () => Promise.resolve([sk("A1")]),
      () => Promise.resolve([sk("B1")]),
    ];
    await useWritingSkillsStore.getState().loadEnabled("ws-a");
    await useWritingSkillsStore.getState().loadEnabled("ws-b");
    const s = useWritingSkillsStore.getState();
    expect(s.enabledWorkspaceId).toBe("ws-b");
    expect(s.enabled.map((x) => x.skill_id)).toEqual(["B1"]);
  });

  it("保存期间切书：直接采用响应列表，切书在途请求仍是最终赢家", async () => {
    useWritingSkillsStore.setState({
      enabled: [sk("A1")], enabledWorkspaceId: "ws-a",
    });
    mockSaveResult = { status: "ok", enabled: ["A1", "A2"], skills: [sk("A1"), sk("A2")] };
    // 书 B 的拉取挂起（模拟切书请求在途）
    let releaseB: (v: WritingSkill[]) => void = () => {};
    const pendingB = new Promise<WritingSkill[]>((resolve) => { releaseB = resolve; });
    mockEnabledByCall = [() => pendingB];
    const pB = useWritingSkillsStore.getState().loadEnabled("ws-b");

    await useWritingSkillsStore.getState().saveEnabled("ws-a", ["A1", "A2"]);
    // 保存成功：直接采用响应里的权威列表（无二次回拉），仍是 ws-a
    const mid = useWritingSkillsStore.getState();
    expect(mid.enabledWorkspaceId).toBe("ws-a");
    expect(mid.enabled.map((x) => x.skill_id)).toEqual(["A1", "A2"]);
    expect(mockEnabledByCall).toEqual([]); // 没有额外的 listEnabled 调用

    releaseB([sk("B1")]);
    await pB;
    const s = useWritingSkillsStore.getState();
    expect(s.enabledWorkspaceId).toBe("ws-b");
    expect(s.enabled.map((x) => x.skill_id)).toEqual(["B1"]); // 切书请求胜出
  });
});
