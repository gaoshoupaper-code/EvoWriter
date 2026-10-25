/**
 * 侧栏「技能」分组验收（REQ-20261025-205943 FR-004/005，AC-005/006）：
 * 四段常显＋角标按段计数、浮层只列本段技能＋管理链接、勾选保存全书合集
 * （整书替换契约）、每段第 6 条界面拦截、showSkills 关闭时分组消失。
 * 对话流零插入由组件结构保证（无对话流相关渲染）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
  createWritingSkill: vi.fn(),
  updateWritingSkill: vi.fn(),
  deleteWritingSkill: vi.fn(),
  listEnabledWritingSkills: vi.fn(async () => mockEnabled),
  setEnabledWritingSkills: vi.fn(async () => ({ status: "ok", enabled: [], skills: [] })),
}));

import { setEnabledWritingSkills } from "@/lib/api";

function skill(id: string, stage: WritingSkill["stage"], name = id): WritingSkill {
  return {
    skill_id: id, owner_id: "u1", name, description: `${name}说明`,
    content: "指令", stage, created_at: "", updated_at: "",
  };
}

function mountSkills() {
  return render(
    <MemoryRouter>
      <SidebarSkills />
    </MemoryRouter>,
  );
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

  it("浮层只列本段技能＋管理链接；勾选保存全书合集（整书替换契约）", async () => {
    mockSkills = [skill("oa", "outline", "伏笔编织"), skill("ob", "outline", "卷BOSS表"), skill("tc", "text", "打斗")];
    mockEnabled = [skill("tc", "text", "打斗")];
    useWritingSkillsStore.setState({ skills: mockSkills, enabled: mockEnabled });
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    const pop = screen.getByRole("dialog", { name: "总纲技能" });
    // 只列总纲技能：他段「打斗」不在浮层
    expect(within(pop).getByText("伏笔编织")).toBeInTheDocument();
    expect(within(pop).queryByText("打斗")).not.toBeInTheDocument();
    expect(within(pop).getByRole("button", { name: /去设置页管理/ })).toBeInTheDocument();
    // 勾选「伏笔编织」：提交的列表含他段已启用的 tc（整书替换）
    await userEvent.click(within(pop).getByRole("checkbox", { name: /伏笔编织/ }));
    expect(setEnabledWritingSkills).toHaveBeenCalledWith("ws-1", ["tc", "oa"]);
  });

  it("同段第 6 条被界面拦截，不发保存请求（AC-003 界面层）", async () => {
    mockSkills = Array.from({ length: 6 }, (_, i) => skill(`o${i}`, "outline", `总纲技${i}`));
    mockEnabled = Array.from({ length: 5 }, (_, i) => skill(`o${i}`, "outline", `总纲技${i}`));
    useWritingSkillsStore.setState({ skills: mockSkills, enabled: mockEnabled });
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /总纲/ }));
    const pop = screen.getByRole("dialog", { name: "总纲技能" });
    await userEvent.click(within(pop).getByRole("checkbox", { name: /总纲技5/ }));
    expect(setEnabledWritingSkills).not.toHaveBeenCalled();
  });

  it("该段没有技能时浮层给空态引导（AC-005 空态）", async () => {
    mountSkills();
    await userEvent.click(screen.getByRole("button", { name: /卷纲/ }));
    const pop = screen.getByRole("dialog", { name: "卷纲技能" });
    expect(within(pop).getByText(/还没有卷纲技能/)).toBeInTheDocument();
  });
});

describe("Sidebar 集成（FR-004/DEC-011 / AC-005/006）", () => {
  const BOOK = [{
    workspace_id: "ws-1", title: "仙侠", domain: "writing", created_at: "",
    session_count: 0, thread_count: 0,
  }] as unknown as import("@/lib/types").WorkspaceSummary[];

  it("showSkills 默认显示技能分组（创作书）", () => {
    render(
      <MemoryRouter>
        <Sidebar
          activePanel="chat" onPanelChange={() => {}} workspaces={BOOK}
          activeWorkspaceId="ws-1" creatingWorkspace={false} deletingWorkspace={false}
          onSelectWorkspace={() => {}} onCreateWorkspace={() => {}} onDeleteWorkspace={() => {}}
          files={[]} activeRel="" onSelectArtifact={() => {}}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("技能")).toBeInTheDocument();
  });

  it("showSkills=false 分组消失（文生图/legacy 书）", () => {
    render(
      <MemoryRouter>
        <Sidebar
          activePanel="chat" onPanelChange={() => {}} workspaces={BOOK}
          activeWorkspaceId="ws-1" creatingWorkspace={false} deletingWorkspace={false}
          onSelectWorkspace={() => {}} onCreateWorkspace={() => {}} onDeleteWorkspace={() => {}}
          files={[]} activeRel="" onSelectArtifact={() => {}} showSkills={false}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByText("技能")).not.toBeInTheDocument();
  });
});
