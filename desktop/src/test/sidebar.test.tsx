/**
 * 作品侧栏验收（REQ-20261018-115508 FR-011，DEC-011）：
 * 书列表（选择/新建/删除）+ 五段产物树 + 对话入口；旧「作品资料」分组不存在。
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar, spineTone } from "@/components/workspace/Sidebar";
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
  it("底栏位于滚动导航之外，空列表仍保留入口（AC-001/003）", () => {
    render(<Sidebar {...baseProps({ workspaces: [], files: [], footer: <button>底栏入口</button> })} />);
    const sidebar = screen.getByRole("complementary", { name: "作品侧栏" });
    const nav = within(sidebar).getByRole("navigation");
    const footer = within(sidebar).getByRole("button", { name: "底栏入口" });
    expect(nav).not.toContainElement(footer);
    expect(sidebar.lastElementChild).toContainElement(footer);
    expect(screen.getByText("还没有作品，点「新书」开一本。")).toBeInTheDocument();
  });

  it("50 本作品和 100 条产物保留全名与末项回调（AC-001/002/006）", async () => {
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
    expect(sidebar.querySelectorAll(".sidebar-book")).toHaveLength(50);
    expect(sidebar.querySelectorAll(".sidebar-tree-item")).toHaveLength(100);
    const book = within(sidebar).getByTitle(longTitle);
    const artifact = within(sidebar).getByTitle(`第100章${longTitle}`);
    await userEvent.click(book);
    await userEvent.click(artifact);
    expect(props.onSelectWorkspace).toHaveBeenCalledWith("book-49");
    expect(props.onSelectArtifact).toHaveBeenCalledWith(files[99].rel);
    expect(books[49].title).toBe(longTitle);
    expect(Array.from(sidebar.querySelectorAll(".sidebar-group-label"), (label) => label.textContent))
      .toEqual(["创作", "作品", "产物"]);
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

  it("五段产物树分组渲染；点条目回调 rel（AC-013）", async () => {
    const props = baseProps();
    render(<Sidebar {...props} />);
    const tree = document.querySelector(".sidebar-tree") as HTMLElement;
    // 组标签（主题/总纲/文风/卷纲/章纲/正文）——「总纲」同时是组名与条目名，用 getAllByText
    for (const label of ["主题", "总纲", "文风", "卷纲", "章纲", "正文"]) {
      expect(within(tree).getAllByText(label).length).toBeGreaterThanOrEqual(1);
    }
    // 每个分组标签都带锚点图标（REQ-20261020-200710 DEC-007）
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

describe("书脊色派生（REQ-20261020-200710 FR-001）", () => {
  it("同名同色，色值落在 6 色色板内（AC-001）", () => {
    for (const title of ["行走的经验包", "以敌为粮", "", "📕 纯 emoji 书名", "A".repeat(200)]) {
      const tone = spineTone(title);
      expect(tone).toBe(spineTone(title));
      expect(tone).toBeGreaterThanOrEqual(0);
      expect(tone).toBeLessThan(6);
    }
  });

  it("不同书名分布到多个色，避免一排同色（AC-001）", () => {
    const titles = Array.from({ length: 12 }, (_, i) => `书名样本第${i}号·类型${i % 3}`);
    const tones = new Set(titles.map(spineTone));
    expect(tones.size).toBeGreaterThanOrEqual(4);
  });
});

describe("书脊渲染（REQ-20261020-200710 FR-001）", () => {
  const IMAGE_BOOK = {
    workspace_id: "ws-img",
    title: "图文混排示范",
    domain: "image",
    created_at: "",
    session_count: 0,
    thread_count: 0,
  } as unknown as WorkspaceSummary;

  it("每本书渲染书脊块；图文书带角标、小说不带（AC-001）", () => {
    render(<Sidebar {...baseProps({ workspaces: [...BOOKS, IMAGE_BOOK] })} />);
    const spines = document.querySelectorAll(".sidebar-book-spine");
    expect(spines).toHaveLength(3);
    expect(spines[0].getAttribute("aria-hidden")).toBe("true");
    // 渲染级绑定：书脊色类名必须等于该书名经 spineTone 派生的档位
    const books = [...BOOKS, IMAGE_BOOK];
    spines.forEach((spine, i) => {
      expect(spine.className).toContain(`t${spineTone(books[i].title)}`);
    });
    const bookRows = document.querySelectorAll(".sidebar-books li");
    expect(bookRows[0].querySelector(".sidebar-book-badge")).toBeNull();
    expect(bookRows[2].querySelector(".sidebar-book-badge")).not.toBeNull();
  });

  it("书脊纯装饰不进书名文本，active 书行文字仍可断言（回归）", () => {
    render(<Sidebar {...baseProps()} />);
    expect(document.querySelector(".sidebar-book.active")?.textContent).toContain("行走的经验包");
  });
});
