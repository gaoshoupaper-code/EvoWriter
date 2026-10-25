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
  { rel: "正文/第002章相遇.md", markdown: "" },
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
    // 技能分组（REQ-20261025-205943）在 sidebarSkills.test.tsx 单测；此处
    // 关闭以保持书架/产物树断言聚焦
    showSkills: false,
    ...over,
  };
}

describe("作品侧栏（FR-011）", () => {
  it("底栏位于滚动导航之外，空列表仍保留入口（AC-001/003）", () => {
    render(<Sidebar {...baseProps({ workspaces: [], files: [], footer: <button>底栏入口</button> })} />);
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    const nav = within(sidebar).getByRole("navigation");
    const footer = within(sidebar).getByRole("button", { name: "底栏入口" });
    expect(nav).not.toContainElement(footer);
    expect(sidebar.lastElementChild).toContainElement(footer);
    expect(screen.getByText("还没有作品，点「新书」开一本。")).toBeInTheDocument();
  });

  it("书超过 5 本默认折叠，展开后全量可点，再收起（AC-002）", async () => {
    const longTitle = "长书名测试".repeat(8);
    const books = Array.from({ length: 50 }, (_, index) => ({
      ...BOOKS[0], workspace_id: `book-${index}`, title: index === 49 ? longTitle : `作品 ${index}`,
    }));
    const files = Array.from({ length: 100 }, (_, index) => ({
      rel: `正文/第${index + 1}章${longTitle}.md`, markdown: "",
    }));
    const props = baseProps({ workspaces: books, activeWorkspaceId: "book-0", files });
    render(<Sidebar {...props} />);
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    // 默认收起：只有前 5 本可见（选中书在前 5 内）
    expect(sidebar.querySelectorAll(".sidebar-book")).toHaveLength(5);
    const toggle = within(sidebar).getByRole("button", { name: "展开其余 45 本" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // 展开：50 本全量 + 按钮变「收起」
    await userEvent.click(toggle);
    expect(sidebar.querySelectorAll(".sidebar-book")).toHaveLength(50);
    const collapse = within(sidebar).getByRole("button", { name: "收起" });
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    const book = within(sidebar).getByTitle(longTitle);
    const artifact = within(sidebar).getByTitle(`第100章${longTitle}`);
    await userEvent.click(book);
    await userEvent.click(artifact);
    expect(props.onSelectWorkspace).toHaveBeenCalledWith("book-49");
    expect(props.onSelectArtifact).toHaveBeenCalledWith(files[99].rel);
    // 收起：回到前 5 本
    await userEvent.click(collapse);
    expect(sidebar.querySelectorAll(".sidebar-book")).toHaveLength(5);
    expect(books[49].title).toBe(longTitle);
    expect(Array.from(sidebar.querySelectorAll(".sidebar-group-label"), (label) => label.textContent))
      .toEqual(["创作", "作品", "产物"]);
  });

  it("折叠时选中的书不在前 5，强制追加可见（AC-002）", () => {
    const books = Array.from({ length: 8 }, (_, index) => ({
      ...BOOKS[0], workspace_id: `book-${index}`, title: `作品 ${index}`,
    }));
    render(<Sidebar {...baseProps({ workspaces: books, activeWorkspaceId: "book-6" })} />);
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    expect(sidebar.querySelectorAll(".sidebar-book")).toHaveLength(6);
    expect(document.querySelector(".sidebar-book.active")?.textContent).toContain("作品 6");
    expect(within(sidebar).getByRole("button", { name: "展开其余 2 本" })).toBeInTheDocument();
  });

  it("书不超过 5 本不出现折叠按钮（AC-002 边界）", () => {
    render(<Sidebar {...baseProps()} />);
    expect(screen.queryByRole("button", { name: /展开其余/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "收起" })).not.toBeInTheDocument();
  });

  it("创建或删除进行中仍禁止重复操作（AC-006）", () => {
    const props = baseProps({ creatingWorkspace: true });
    const { rerender } = render(<Sidebar {...props} />);
    expect(screen.getByRole("button", { name: "新建作品" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "删除 行走的经验包" })).toBeDisabled();
    rerender(<Sidebar {...baseProps({ deletingWorkspace: true })} />);
    expect(screen.getByRole("button", { name: "删除 行走的经验包" })).toBeDisabled();
  });

  it("书列表渲染并可切换；当前书高亮", async () => {
    const props = baseProps();
    render(<Sidebar {...props} />);
    expect(screen.getByText("行走的经验包")).toBeInTheDocument();
    expect(screen.getByText("以敌为粮")).toBeInTheDocument();
    // 删除入口现在每行都有（aria 名含书名），书行按钮必须精确匹配
    await userEvent.click(screen.getByRole("button", { name: "以敌为粮" }));
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

  it("每本书都有删除入口（悬停浮现），非选中书可删（REQ-20261020-200710 AC-004）", async () => {
    const props = baseProps();
    render(<Sidebar {...props} />);
    const deletes = document.querySelectorAll(".sidebar-book-delete");
    expect(deletes).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "删除 以敌为粮" }));
    expect(props.onDeleteWorkspace).toHaveBeenCalledWith("ws-2");
  });

  it("单条目组组头折叠进条目，多条目组保留组标签；点条目回调 rel（AC-013/AC-005）", async () => {
    const props = baseProps();
    render(<Sidebar {...props} />);
    const tree = document.querySelector(".sidebar-tree") as HTMLElement;
    // 单条目组（主题/总纲/文风/卷纲/章纲各一份文档，REQ-20261025-210512 FR-005）：
    // 不渲染组标签行，条目带 solo 类与组锚点图标，文档名直接可点
    const soloItems = document.querySelectorAll(".sidebar-tree-item.solo");
    expect(soloItems).toHaveLength(5);
    const soloNames = Array.from(soloItems, (item) => item.textContent);
    for (const name of ["主题", "总纲", "文风", "第01卷", "第001章"]) {
      expect(soloNames).toContain(name);
    }
    soloItems.forEach((item) => {
      expect(item.querySelector(".sidebar-tree-icon")).not.toBeNull();
    });
    // 多条目组（正文 2 条）保留组标签并带锚点图标
    const labels = Array.from(
      document.querySelectorAll(".sidebar-tree-group-label"),
      (el) => el.textContent,
    );
    expect(labels).toEqual(["正文"]);
    document
      .querySelectorAll(".sidebar-tree-group-label")
      .forEach((label) => expect(label.querySelector("svg")).not.toBeNull());
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

describe("书图标（REQ-20261025-210512 FR-003/DEC-003）", () => {
  const IMAGE_BOOK = {
    workspace_id: "ws-img",
    title: "图文混排示范",
    domain: "image",
    created_at: "",
    session_count: 0,
    thread_count: 0,
  } as unknown as WorkspaceSummary;

  it("所有书统一书本图标、无随机色块；图文书用图片图标（AC-003）", () => {
    render(<Sidebar {...baseProps({ workspaces: [...BOOKS, IMAGE_BOOK] })} />);
    const rows = document.querySelectorAll(".sidebar-books li");
    expect(rows).toHaveLength(3);
    // 小说行：统一书本图标；图文书行：图片图标
    expect(rows[0].querySelector(".sidebar-book-icon")).not.toBeNull();
    expect(rows[1].querySelector(".sidebar-book-icon")).not.toBeNull();
    expect(rows[2].querySelector(".sidebar-book-icon--image")).not.toBeNull();
    // 随机取色书脊机制整体退役：色块、色档类名、首字母都不再渲染
    expect(document.querySelectorAll(".sidebar-book-spine")).toHaveLength(0);
    expect(document.querySelectorAll(".sidebar-spine-letter")).toHaveLength(0);
    expect(Array.from(document.querySelectorAll<HTMLElement>(".sidebar-books [class]"))
      .every((el) => !/\bt[0-5]\b/.test(el.className))).toBe(true);
  });

  it("图标纯装饰不进书名文本，active 书行文字仍可断言（回归）", () => {
    render(<Sidebar {...baseProps()} />);
    expect(document.querySelector(".sidebar-book.active")?.textContent).toContain("行走的经验包");
  });
});
