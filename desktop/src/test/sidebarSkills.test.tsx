import { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WritingSkillsDialog } from "@/components/workspace/SidebarSkills";
import { useWritingSkillsStore } from "@/stores/writingSkills";
import { useWorkspaceStore } from "@/stores/workspace";
import { createWritingSkill, deleteWritingSkill, listEnabledWritingSkills, listWritingSkills, setEnabledWritingSkills, updateWritingSkill, type EnabledWritingSkills, type WritingSkill } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  listWritingSkills: vi.fn(), listEnabledWritingSkills: vi.fn(), setEnabledWritingSkills: vi.fn(),
  createWritingSkill: vi.fn(), updateWritingSkill: vi.fn(), deleteWritingSkill: vi.fn(),
}));

function skill(id: string, stage: WritingSkill["stage"] = "outline"): WritingSkill {
  return { skill_id: id, owner_id: "u1", name: id, description: `${id}说明`, content: "指令", stage, created_at: "", updated_at: "" };
}
const empty = (): EnabledWritingSkills => ({ enabled: [], pending_stages: [], candidates: [] });
const open = () => render(<WritingSkillsDialog onClose={vi.fn()} returnFocusRef={createRef<HTMLButtonElement>()} />);

beforeEach(() => {
  vi.resetAllMocks();
  useWritingSkillsStore.getState().clear();
  useWorkspaceStore.setState({ activeWorkspaceId: "" });
  vi.mocked(listWritingSkills).mockResolvedValue([]);
  vi.mocked(listEnabledWritingSkills).mockResolvedValue(empty());
  vi.mocked(setEnabledWritingSkills).mockResolvedValue(empty());
});

describe("全局技能独立管理弹窗（AC-004/005）", () => {
  it("无作品也能管理四阶段，勾选按阶段保存全局状态", async () => {
    vi.mocked(listWritingSkills).mockResolvedValue([skill("伏笔"), skill("打斗", "text")]);
    open();
    expect(screen.getByRole("dialog", { name: "写作技能" })).toHaveTextContent("全部作品生效");
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    await userEvent.click(await screen.findByRole("checkbox", { name: /伏笔/ }));
    expect(setEnabledWritingSkills).toHaveBeenCalledWith("outline", ["伏笔"]);
    expect(screen.queryByRole("checkbox", { name: /打斗/ })).not.toBeInTheDocument();
  });

  it("第六条拦截；待选择阶段明确保存，失败不解除待选", async () => {
    const candidates = Array.from({ length: 6 }, (_, i) => skill(`正文技${i}`, "text"));
    vi.mocked(listWritingSkills).mockResolvedValue(candidates);
    vi.mocked(listEnabledWritingSkills).mockResolvedValue({ enabled: [], pending_stages: ["text"], candidates });
    open();
    await userEvent.click(screen.getByRole("tab", { name: /正文/ }));
    await screen.findByRole("checkbox", { name: /正文技5/ });
    expect(screen.getByText(/仅暂停正文/)).toBeInTheDocument();
    for (let i = 0; i < 6; i += 1) await userEvent.click(screen.getByRole("checkbox", { name: new RegExp(`正文技${i}`) }));
    expect(screen.getAllByRole("checkbox").filter((node) => (node as HTMLInputElement).checked)).toHaveLength(5);
    expect(setEnabledWritingSkills).not.toHaveBeenCalled();
    vi.mocked(setEnabledWritingSkills).mockRejectedValueOnce(new Error("保存失败"));
    await userEvent.click(screen.getByRole("button", { name: "保存正文选择" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
    expect(useWritingSkillsStore.getState().pendingStages).toEqual(["text"]);
    vi.mocked(setEnabledWritingSkills).mockResolvedValueOnce({ enabled: candidates.slice(0, 5), pending_stages: [], candidates: [] });
    await userEvent.click(screen.getByRole("button", { name: "保存正文选择" }));
    await waitFor(() => expect(useWritingSkillsStore.getState().pendingStages).toEqual([]));
    expect(useWritingSkillsStore.getState().enabled).toHaveLength(5);
  });

  it("待选阶段可以明确保存空清单", async () => {
    vi.mocked(listEnabledWritingSkills).mockResolvedValue({ enabled: [], pending_stages: ["outline"], candidates: [skill("候选")] });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "保存总纲选择" }));
    expect(setEnabledWritingSkills).toHaveBeenCalledWith("outline", []);
  });

  it("加载失败显示重试，不把失败显示为空技能库", async () => {
    vi.mocked(listWritingSkills).mockRejectedValueOnce(new Error("网络不可用"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("网络不可用");
    expect(screen.queryByText(/还没有总纲技能/)).not.toBeInTheDocument();
    vi.mocked(listWritingSkills).mockResolvedValueOnce([skill("恢复")]);
    await userEvent.click(screen.getByRole("button", { name: "重试加载技能" }));
    expect(await screen.findByRole("checkbox", { name: /恢复/ })).toBeInTheDocument();
  });

  it("新建、编辑和删除沿用现有字段与阶段", async () => {
    vi.mocked(listWritingSkills).mockResolvedValue([skill("伏笔")]);
    vi.mocked(createWritingSkill).mockResolvedValue(skill("新技能"));
    vi.mocked(updateWritingSkill).mockResolvedValue(skill("改名"));
    vi.mocked(deleteWritingSkill).mockResolvedValue({ status: "ok", deleted: "伏笔" });
    vi.stubGlobal("confirm", () => true);
    open();
    await screen.findByRole("checkbox", { name: /伏笔/ });
    await userEvent.click(screen.getByRole("button", { name: "新建总纲技能" }));
    let editor = await screen.findByRole("dialog", { name: "新建写作技能" });
    await userEvent.type(within(editor).getByLabelText("名称"), "新技能");
    await userEvent.type(within(editor).getByLabelText("一句话说明"), "说明");
    await userEvent.type(within(editor).getByLabelText("指令正文"), "具体要求");
    await userEvent.click(within(editor).getByRole("button", { name: "保存技能" }));
    expect(createWritingSkill).toHaveBeenCalledWith({ name: "新技能", description: "说明", content: "具体要求", stage: "outline" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "新建写作技能" })).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "编辑 伏笔" }));
    editor = await screen.findByRole("dialog", { name: "编辑「伏笔」" });
    await userEvent.clear(within(editor).getByLabelText("名称"));
    await userEvent.type(within(editor).getByLabelText("名称"), "改名");
    await userEvent.click(within(editor).getByRole("button", { name: "更新技能" }));
    expect(updateWritingSkill).toHaveBeenCalledWith("伏笔", expect.objectContaining({ name: "改名", stage: "outline" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑「伏笔」" })).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "删除 伏笔" }));
    expect(deleteWritingSkill).toHaveBeenCalledWith("伏笔");
    vi.unstubAllGlobals();
  });
});
