/**
 * 侧栏「技能」分组验收（REQ-20261025-205943 FR-004/005；2026-10-25 修订：
 * 浮层 portal+fixed 定位修复侧栏裁剪不可见，管理入口整体迁入侧栏）：
 * 四段常显＋角标按段计数、浮层只列本段技能、勾选保存全书合集（整书替换
 * 契约）、每段第 6 条界面拦截、浮层内新建/编辑/删除、设置页板块移除后
 * 管理仅存于侧栏、showSkills 关闭时分组消失。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar } from "@/components/workspace/Sidebar";
import { SidebarSkills } from "@/components/workspace/SidebarSkills";
import type { WritingSkill } from "@/lib/api";
import { useWritingSkillsStore } from "@/stores/writingSkills";
import { useWorkspaceStore } from "@/stores/workspace";

// 浮层打开时组件会重拉库列表（loadSkills），mock 必须返回测试预置的
// 数据，否则会把 seed 清空——用模块级变量承接
let mockSkills: WritingSkill[] = [];
let mockEnabled: WritingSkill[] = [];

vi.mock("@/lib/api", () => ({
  listWritingSkills: vi.fn(async () => mockSkills),
  createWritingSkill: vi.fn(async () => ({ skill_id: "new-1" })),
  updateWritingSkill: vi.fn(async () => ({ skill_id: "upd-1" })),
  deleteWritingSkill: vi.fn(async () => ({ status: "ok", deleted: "x" })),
  listEnabledWritingSkills: vi.fn(async () => mockEnabled),
  setEnabledWritingSkills: vi.fn(async () => ({ status: "ok", enabled: [], skills: [] })),
}));

import {
  createWritingSkill,
  deleteWritingSkill,
  setEnabledWritingSkills,
  updateWritingSkill,
} from "@/lib/api";

function skill(id: string, stage: WritingSkill["stage"], name = id): WritingSkill {
  return {
    skill_id: id, owner_id: "u1", name, description: `${name}说明`,
    content: "指令", stage, created_at: "", updated_at: "",
  };
}

function mountSkills() {
  return render(<SidebarSkills />);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSkills = [];
  mockEnabled = [];
  useWorkspaceStore.setState({ activeWorkspaceId: "ws-1" });
  useWritingSkillsStore.setState({
    skills: [], skillsLoading: false, enabled: [], enabledWorkspaceId: "ws-1",
  });
});

describe("侧栏技能分组（FR-004 / AC-005）", () => {
  it("四段常显，角标按段计数（0 不显示数字）", () => {
    useWritingSkillsStore.setState({
      enabled: [skill("t1", "text"), skill("t2", "text"), skill("o1", "outline")],
    });
    mountSkills();
    const row = (name: string) => screen.getByRole("button", { name: new RegExp(name) });
    expect(row("总纲").textContent).toContain("1");
    expect(row("正文").textContent).toContain("2");
    expect(row("卷纲").querySelector(".sidebar-skill-badge")).toBeNull();
    expect(row("章纲").querySelector(".sidebar-skill-badge")).toBeNull();
  });

  it("浮层渲染到 body（不被侧栏滚动容器裁剪），只列本段技能", async () => {
    mockSkills = [skill("oa", "outline", "伏笔编织"), skill("tc", "text", "打斗")];
    mockEnabled = [skill("tc", "text", "打斗")];
    useWritingSkillsStore.setState({ skills: mockSkills, enabled: mockEnabled });
    const { container } = mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    // 浮层在 body 直下（portal），不在侧栏组件树内——修复「点击无反应」
    const pop = document.body.querySelector(".sidebar-skill-popover") as HTMLElement;
    expect(pop).not.toBeNull();
    expect(pop.getAttribute("style")).toContain("position: fixed");
    expect(within(pop).getByText("伏笔编织")).toBeInTheDocument();
    expect(within(pop).queryByText("打斗")).not.toBeInTheDocument();
    // 触发按钮留在侧栏树内
    expect(container.querySelector(".sidebar-skill-popover")).toBeNull();
  });

  it("勾选保存全书合集（整书替换契约）", async () => {
    mockSkills = [skill("oa", "outline", "伏笔编织"), skill("tc", "text", "打斗")];
    mockEnabled = [skill("tc", "text", "打斗")];
    useWritingSkillsStore.setState({ skills: mockSkills, enabled: mockEnabled });
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    const pop = document.body.querySelector(".sidebar-skill-popover") as HTMLElement;
    await userEvent.click(within(pop).getByRole("checkbox", { name: /伏笔编织/ }));
    expect(setEnabledWritingSkills).toHaveBeenCalledWith("ws-1", ["tc", "oa"]);
  });

  it("同段第 6 条被界面拦截，不发保存请求（AC-003 界面层）", async () => {
    mockSkills = Array.from({ length: 6 }, (_, i) => skill(`o${i}`, "outline", `总纲技${i}`));
    mockEnabled = Array.from({ length: 5 }, (_, i) => skill(`o${i}`, "outline", `总纲技${i}`));
    useWritingSkillsStore.setState({ skills: mockSkills, enabled: mockEnabled });
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    const pop = document.body.querySelector(".sidebar-skill-popover") as HTMLElement;
    await userEvent.click(within(pop).getByRole("checkbox", { name: /总纲技5/ }));
    expect(setEnabledWritingSkills).not.toHaveBeenCalled();
  });

  it("浮层内新建：弹窗表单预选该段，提交带 stage（管理迁入侧栏）", async () => {
    mockSkills = [skill("oa", "outline", "伏笔编织")];
    useWritingSkillsStore.setState({ skills: mockSkills });
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    const pop = document.body.querySelector(".sidebar-skill-popover") as HTMLElement;
    await userEvent.click(within(pop).getByRole("button", { name: /新建总纲技能/ }));
    const dialog = document.body.querySelector(".skill-dialog") as HTMLElement;
    expect(dialog).not.toBeNull();
    await userEvent.type(within(dialog).getByLabelText("名称"), "卷BOSS设计");
    await userEvent.type(within(dialog).getByLabelText("一句话说明"), "每卷一个BOSS");
    await userEvent.type(within(dialog).getByLabelText("指令正文"), "BOSS战前置三章铺垫。");
    await userEvent.click(within(dialog).getByRole("button", { name: /保存技能/ }));
    expect(createWritingSkill).toHaveBeenCalledWith(
      expect.objectContaining({ name: "卷BOSS设计", stage: "outline" }),
    );
  });

  it("浮层内编辑：预填原值，提交更新", async () => {
    mockSkills = [skill("oa", "outline", "伏笔编织")];
    useWritingSkillsStore.setState({ skills: mockSkills });
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    const pop = document.body.querySelector(".sidebar-skill-popover") as HTMLElement;
    await userEvent.click(within(pop).getByRole("button", { name: "编辑 伏笔编织" }));
    const dialog = document.body.querySelector(".skill-dialog") as HTMLElement;
    expect(within(dialog).getByDisplayValue("伏笔编织")).toBeInTheDocument();
    const nameInput = within(dialog).getByLabelText("名称");
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "伏笔编织法");
    await userEvent.click(within(dialog).getByRole("button", { name: /更新技能/ }));
    expect(updateWritingSkill).toHaveBeenCalledWith(
      "oa",
      expect.objectContaining({ name: "伏笔编织法", stage: "outline" }),
    );
  });

  it("浮层内删除：确认后调删除接口（服务端联动解绑）", async () => {
    mockSkills = [skill("oa", "outline", "伏笔编织")];
    useWritingSkillsStore.setState({ skills: mockSkills });
    vi.stubGlobal("confirm", () => true);
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    const pop = document.body.querySelector(".sidebar-skill-popover") as HTMLElement;
    await userEvent.click(within(pop).getByRole("button", { name: "删除 伏笔编织" }));
    expect(deleteWritingSkill).toHaveBeenCalledWith("oa");
    vi.unstubAllGlobals();
  });

  it("该段没有技能时浮层给空态引导＋新建入口（AC-005 空态）", async () => {
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /卷纲/ }));
    const pop = document.body.querySelector(".sidebar-skill-popover") as HTMLElement;
    expect(within(pop).getByText(/还没有卷纲技能/)).toBeInTheDocument();
    expect(within(pop).getByRole("button", { name: /新建卷纲技能/ })).toBeInTheDocument();
  });
});

describe("Sidebar 集成（FR-004/DEC-011 / AC-005/006）", () => {
  const BOOK = [{
    workspace_id: "ws-1", title: "仙侠", domain: "writing", created_at: "",
    session_count: 0, thread_count: 0,
  }] as unknown as import("@/lib/types").WorkspaceSummary[];

  it("showSkills 默认显示技能分组（创作书）", () => {
    render(
      <Sidebar
        activePanel="chat" onPanelChange={() => {}} workspaces={BOOK}
        activeWorkspaceId="ws-1" creatingWorkspace={false} deletingWorkspace={false}
        onSelectWorkspace={() => {}} onCreateWorkspace={() => {}} onDeleteWorkspace={() => {}}
        files={[]} activeRel="" onSelectArtifact={() => {}}
      />,
    );
    expect(screen.getByText("技能")).toBeInTheDocument();
  });

  it("showSkills=false 分组消失（文生图/legacy 书）", () => {
    render(
      <Sidebar
        activePanel="chat" onPanelChange={() => {}} workspaces={BOOK}
        activeWorkspaceId="ws-1" creatingWorkspace={false} deletingWorkspace={false}
        onSelectWorkspace={() => {}} onCreateWorkspace={() => {}} onDeleteWorkspace={() => {}}
        files={[]} activeRel="" onSelectArtifact={() => {}} showSkills={false}
      />,
    );
    expect(screen.queryByText("技能")).not.toBeInTheDocument();
  });
});
