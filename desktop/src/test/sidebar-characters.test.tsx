import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar } from "@/components/workspace/Sidebar";

function props(over: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  return {
    activePanel: "artifacts" as const, onPanelChange: vi.fn(), workspaces: [],
    activeWorkspaceId: "ws-1", creatingWorkspace: false, deletingWorkspace: false,
    onSelectWorkspace: vi.fn(), onCreateWorkspace: vi.fn(), onDeleteWorkspace: vi.fn(),
    files: [{ rel: "总纲.md", markdown: "" }, { rel: "文风.md", markdown: "" }],
    activeRel: "", onSelectArtifact: vi.fn(), onRepairCharacters: vi.fn(), ...over,
  };
}

describe("人物卡侧栏（REQ-20261102-135406 AC-001/006）", () => {
  it("空模块常驻总纲与文风之间，保留对话重做入口且不会自动生成", async () => {
    const p = props();
    render(<Sidebar {...p} />);
    const tree = document.querySelector(".sidebar-tree") as HTMLElement;
    const labels = Array.from(tree.querySelectorAll("button"), (node) => node.textContent);
    expect(labels.indexOf("总纲")).toBeLessThan(labels.findIndex((name) => name?.includes("人物卡")));
    expect(labels.findIndex((name) => name?.includes("人物卡"))).toBeLessThan(labels.indexOf("文风"));
    expect(screen.getByText("暂无人物卡")).toBeInTheDocument();
    expect(p.onRepairCharacters).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "重做人物卡" }));
    expect(p.onRepairCharacters).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /^技能/ })).not.toBeInTheDocument();
  });

  it("单张仍保留组头，多张主角置顶，可折叠、点击姓名查看", async () => {
    const p = props({ files: [
      { rel: "人物/阿青.md", markdown: "青" }, { rel: "人物/主角.md", markdown: "主" },
      { rel: "文风.md", markdown: "" }, { rel: "总纲.md", markdown: "" },
    ] });
    const { rerender } = render(<Sidebar {...p} />);
    const group = screen.getByRole("group", { name: "人物卡" });
    expect(within(group).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["主角", "阿青"]);
    await userEvent.click(within(group).getByRole("button", { name: "阿青" }));
    expect(p.onSelectArtifact).toHaveBeenCalledWith("人物/阿青.md");
    await userEvent.click(within(group).getByRole("button", { name: "人物卡" }));
    expect(within(group).queryByRole("button", { name: "阿青" })).not.toBeInTheDocument();
    rerender(<Sidebar {...p} files={[{ rel: "人物/主角.md", markdown: "" }]} />);
    expect(within(group).getByRole("button", { name: "人物卡" })).toBeInTheDocument();
  });

  it("加载失败不会显示无卡，提供重试；切书后只展示新名单", async () => {
    const onRetry = vi.fn();
    const p = props({ filesError: "人物卡加载失败", onRetryFiles: onRetry });
    const { rerender } = render(<Sidebar {...p} />);
    expect(screen.getByRole("alert")).toHaveTextContent("人物卡加载失败");
    expect(screen.queryByText("暂无人物卡")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试加载人物卡" }));
    expect(onRetry).toHaveBeenCalledOnce();
    rerender(<Sidebar {...p} filesError="" activeWorkspaceId="ws-2" files={[{ rel: "人物/乙.md", markdown: "" }]} />);
    expect(screen.getByRole("button", { name: "乙" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "阿青" })).not.toBeInTheDocument();
  });
});
