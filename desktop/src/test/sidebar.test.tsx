/**
 * 作品侧栏验收（REQ-20261018-115508 FR-011，DEC-011）：
 * 书列表（选择/新建/删除）+ 五段产物树 + 对话入口；旧「作品资料」分组不存在。
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar } from "@/components/workspace/Sidebar";
import type { WorkspaceSummary } from "@/lib/types";

const BOOKS: WorkspaceSummary[] = [
  { workspace_id: "ws-1", title: "行走的经验包", domain: "writing", created_at: "", session_count: 0, thread_count: 0 },
  { workspace_id: "ws-2", title: "以敌为粮", domain: "writing", created_at: "", session_count: 0, thread_count: 0 },
] as unknown as WorkspaceSummary[];

const FILES = [
  { rel: "主题.md", markdown: "" },
  { rel: "总纲.md", markdown: "" },
  { rel: "文风.md", markdown: "" },
  { rel: "卷纲/第01卷.md", markdown: "" },
  { rel: "章纲/第001章.md", markdown: "" },
  { rel: "正文/第001章开局.md", markdown: "" },
];

function baseProps(over: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  return {
    activePanel: "chat" as const,
    onPanelChange: vi.fn(),
    workspaces: BOOKS,
    activeWorkspaceId: "ws-1",
    creatingWorkspace: false,
    deletingWorkspace: false,
    onSelectWorkspace: vi.fn(),
    onCreateWorkspace: vi.fn(),
    onDeleteWorkspace: vi.fn(),
    files: FILES,
    activeRel: "",
    onSelectArtifact: vi.fn(),
    ...over,
  };
}

describe("作品侧栏（FR-011）", () => {
  it("书列表渲染并可切换；当前书高亮", async () => {
    const props = baseProps();
    render(<Sidebar {...props} />);
    expect(screen.getByText("行走的经验包")).toBeInTheDocument();
    expect(screen.getByText("以敌为粮")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /以敌为粮/ }));
    expect(props.onSelectWorkspace).toHaveBeenCalledWith("ws-2");
    expect(document.querySelector(".sidebar-book.active")?.textContent).toContain("行走的经验包");
  });

  it("新书按钮与删除入口回调", async () => {
    const props = baseProps();
    render(<Sidebar {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "新建作品" }));
    expect(props.onCreateWorkspace).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "删除 行走的经验包" }));
    expect(props.onDeleteWorkspace).toHaveBeenCalledWith("ws-1");
  });

  it("五段产物树分组渲染；点条目回调 rel（AC-013）", async () => {
    const props = baseProps();
    render(<Sidebar {...props} />);
    const tree = document.querySelector(".sidebar-tree") as HTMLElement;
    // 组标签（题材/总纲/文风/卷纲/章纲/正文）——「总纲」同时是组名与条目名，用 getAllByText
    for (const label of ["题材", "总纲", "文风", "卷纲", "章纲", "正文"]) {
      expect(within(tree).getAllByText(label).length).toBeGreaterThanOrEqual(1);
    }
    await userEvent.click(within(tree).getByText("第001章开局"));
    expect(props.onSelectArtifact).toHaveBeenCalledWith("正文/第001章开局.md");
  });

  it("空书产物树显示引导占位，不报错", () => {
    render(<Sidebar {...baseProps({ files: [] })} />);
    expect(screen.getByText("开始对话后，产物会在这里出现。")).toBeInTheDocument();
  });

  it("旧「作品资料」分组不存在（DEC-011）", () => {
    render(<Sidebar {...baseProps()} />);
    expect(screen.queryByText("作品资料")).not.toBeInTheDocument();
    expect(screen.queryByText("角色档案与关系")).not.toBeInTheDocument();
  });

  it("对话入口切换回调 chat，激活态正确标记", async () => {
    const props = baseProps();
    const { rerender } = render(<Sidebar {...props} />);
    await userEvent.click(screen.getByRole("button", { name: /和陪练一起推进这本书/ }));
    expect(props.onPanelChange).toHaveBeenCalledWith("chat");
    rerender(<Sidebar {...baseProps({ activePanel: "artifacts" })} />);
    expect(document.querySelector(".sidebar-item.active")).toBeNull();
  });
});
