/**
 * 侧栏重组验收（REQ-20261012-202540 FR-006/AC-008，DEC-005/007/008）：
 * 两组五入口、「大纲（旧）」不存在、文案零黑话。
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar } from "@/components/workspace/Sidebar";

describe("Sidebar 两组五入口（FR-006）", () => {
  it("渲染「创作」「作品资料」两组，共五个入口", () => {
    render(<Sidebar activePanel="chat" onPanelChange={vi.fn()} />);
    expect(screen.getByText("创作")).toBeInTheDocument();
    expect(screen.getByText("作品资料")).toBeInTheDocument();

    const group = (title: string) => within(screen.getByText(title).closest(".sidebar-group")!);
    expect(group("创作").getByRole("button", { name: /对话/ })).toBeInTheDocument();
    expect(group("作品资料").getAllByRole("button")).toHaveLength(4);
  });

  it("「大纲（旧）」入口不存在（DEC-007）", () => {
    render(<Sidebar activePanel="chat" onPanelChange={vi.fn()} />);
    expect(screen.queryByText("大纲（旧）")).not.toBeInTheDocument();
    expect(screen.queryByText("旧版故事线五件套")).not.toBeInTheDocument();
  });

  it("文案零黑话（FR-007）：不出现「管线/工业/产物」", () => {
    const { container } = render(<Sidebar activePanel="chat" onPanelChange={vi.fn()} />);
    const text = container.textContent ?? "";
    for (const banned of ["管线", "工业", "产物", "Agent"]) {
      expect(text).not.toContain(banned);
    }
  });

  it("点击入口回调对应面板 id，激活态正确标记", async () => {
    const onPanelChange = vi.fn();
    const { rerender } = render(<Sidebar activePanel="chat" onPanelChange={onPanelChange} />);
    await userEvent.click(screen.getByRole("button", { name: /角色档案与关系/ }));
    expect(onPanelChange).toHaveBeenCalledWith("characters");

    rerender(<Sidebar activePanel="characters" onPanelChange={onPanelChange} />);
    const active = document.querySelector(".sidebar-item.active");
    expect(active?.textContent).toContain("人物");
  });
});
