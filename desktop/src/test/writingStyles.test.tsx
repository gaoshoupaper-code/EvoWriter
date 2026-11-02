import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { WritingStylesDialog } from "@/components/workspace/WritingStylesDialog";
import { SidebarFooter } from "@/components/workspace/SidebarFooter";
import { SidebarStyle } from "@/components/workspace/SidebarStyle";
import { useContentStore } from "@/stores/content";
import { useWorkspaceStore } from "@/stores/workspace";
import { useAnnotationsStore } from "@/stores/annotations";
import { useWritingStylesStore } from "@/stores/writingStyles";
import { listWritingStyles, saveWorkspaceWritingStyle, importWorkspaceWritingStyle, deleteWritingStyle, renameWritingStyle } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  listWritingStyles: vi.fn(), saveWorkspaceWritingStyle: vi.fn(), importWorkspaceWritingStyle: vi.fn(), deleteWritingStyle: vi.fn(), renameWritingStyle: vi.fn(),
  listWritingSkills: vi.fn(async () => []), listEnabledWritingSkills: vi.fn(async () => ({ enabled: [], pending_stages: [], candidates: [] })),
}));
const asset = { style_id: "a", name: "冷静", content: "# 原文\n完整样章。", created_at: "now", updated_at: "now" };
beforeEach(() => {
  vi.resetAllMocks(); useWritingStylesStore.getState().clear();
  vi.mocked(listWritingStyles).mockResolvedValue([asset]);
});

describe("文风资产交互", () => {
  it("个人菜单位于主题与技能之间，无作品也能查看全文", async () => {
    render(<MemoryRouter><SidebarFooter username="writer" hasApiKey theme="light" onLogout={vi.fn()} onThemeToggle={vi.fn()} /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: "用户菜单" }));
    expect(screen.getAllByRole("menuitem").map((node) => node.textContent)).toEqual(["主题", "文风", "技能", "修改密码", "退出登录"]);
    await userEvent.click(screen.getByRole("menuitem", { name: "文风" }));
    const dialog = await screen.findByRole("dialog", { name: "个人文风库" });
    await userEvent.click(await within(dialog).findByRole("button", { name: "查看 冷静" }));
    expect(within(dialog).getByText("完整样章。")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /新建|编辑全文/ })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "关闭文风" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "用户菜单" })).toHaveFocus());
  });

  it("默认另存；更新旧资产必须明确选择", async () => {
    const close = vi.fn();
    vi.mocked(saveWorkspaceWritingStyle).mockRejectedValueOnce(new Error("已有同名文风"));
    render(<WritingStylesDialog mode="save" workspaceId="ws" onClose={close} />);
    await screen.findByRole("option", { name: "更新「冷静」" });
    expect(screen.getByLabelText("保存方式")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("文风名称"), { target: { value: "冷静" } });
    await userEvent.click(screen.getByRole("button", { name: "保存到个人库" }));
    expect(saveWorkspaceWritingStyle).toHaveBeenCalledWith("ws", "冷静", undefined);
    expect(await screen.findByRole("alert")).toHaveTextContent("已有同名文风");
    expect(screen.getByLabelText("文风名称")).toHaveValue("冷静");
    expect(close).not.toHaveBeenCalled();
    vi.mocked(saveWorkspaceWritingStyle).mockResolvedValue(asset);
    fireEvent.change(screen.getByLabelText("保存方式"), { target: { value: "a" } });
    await userEvent.click(screen.getByRole("button", { name: "更新选中文风" }));
    expect(saveWorkspaceWritingStyle).toHaveBeenLastCalledWith("ws", "冷静", "a");
    expect(close).toHaveBeenCalledOnce();
    expect(listWritingStyles).toHaveBeenCalledTimes(1);
  });

  it("导入不再确认，失败不关闭，重试成功只写一次", async () => {
    const close = vi.fn(), imported = vi.fn();
    const confirm = vi.spyOn(window, "confirm");
    vi.mocked(importWorkspaceWritingStyle).mockRejectedValueOnce(new Error("作品正在生成"));
    render(<WritingStylesDialog mode="import" workspaceId="ws" onClose={close} onImported={imported} />);
    await userEvent.click(await screen.findByRole("button", { name: "查看 冷静" }));
    await userEvent.click(screen.getByRole("button", { name: "导入选中文风" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("作品正在生成");
    expect(imported).not.toHaveBeenCalled();
    vi.mocked(importWorkspaceWritingStyle).mockResolvedValue({ status: "ok", style_id: "a", content: "实际导入全文", invalidated_annotations: 2 });
    await userEvent.click(screen.getByRole("button", { name: "导入选中文风" }));
    expect(imported).toHaveBeenCalledExactlyOnceWith("实际导入全文");
    expect(close).toHaveBeenCalledOnce(); expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("删除需确认，取消不写入；改名不编辑全文", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<WritingStylesDialog mode="library" onClose={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "删除 冷静" }));
    expect(deleteWritingStyle).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "改名 冷静" }));
    fireEvent.change(screen.getByLabelText("新名称"), { target: { value: "新名字" } });
    vi.mocked(renameWritingStyle).mockResolvedValue({ ...asset, name: "新名字" });
    await userEvent.click(screen.getByRole("button", { name: "确认改名" }));
    expect(renameWritingStyle).toHaveBeenCalledExactlyOnceWith("a", "新名字");
    expect(await screen.findByRole("button", { name: "查看 新名字" })).toBeInTheDocument();
    confirm.mockRestore();
  });

  it("加载失败不会显示空库，重试后显示明确空态", async () => {
    vi.mocked(listWritingStyles).mockRejectedValueOnce(new Error("加载失败")).mockResolvedValueOnce([]);
    render(<WritingStylesDialog mode="import" workspaceId="ws" onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("加载失败");
    expect(screen.queryByText(/还没有保存文风/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试加载文风" }));
    expect(await screen.findByText(/还没有保存文风/)).toBeInTheDocument();
  });

  it("无卡也能导入，成功更新作品预览并仅失效旧文风待办", async () => {
    useWorkspaceStore.setState({ activeWorkspaceId: "ws", activePanel: "chat" });
    useContentStore.setState({ outlineWorkspaceId: "ws", outlineFiles: [{ rel: "总纲.md", markdown: "原总纲" }] });
    useAnnotationsStore.setState({ workspaceId: "ws", annotations: [
      { id: "a", artifact_rel: "文风.md", label: "custom", quote: "旧文风", status: "pending", dispatched: true },
      { id: "b", artifact_rel: "文风.md", label: "custom", quote: "已完成", status: "resolved" },
      { id: "c", artifact_rel: "总纲.md", label: "custom", quote: "原总纲", status: "pending" },
    ] });
    vi.mocked(importWorkspaceWritingStyle).mockResolvedValue({ status: "ok", style_id: "a", content: asset.content, invalidated_annotations: 1 });
    render(<SidebarStyle workspaceId="ws" hasStyle={false} active={false} onOpen={vi.fn()} filesLoading={false} filesError="" />);
    expect(screen.getByRole("button", { name: "保存文风" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "导入文风" }));
    await userEvent.click(await screen.findByRole("button", { name: "查看 冷静" }));
    await userEvent.click(screen.getByRole("button", { name: "导入选中文风" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(useContentStore.getState().outlineFiles).toEqual([{ rel: "总纲.md", markdown: "原总纲" }, { rel: "文风.md", markdown: asset.content }]);
    expect(useContentStore.getState().activeOutlineRel).toBe("文风.md");
    expect(useWorkspaceStore.getState().activePanel).toBe("artifacts");
    expect(useAnnotationsStore.getState().annotations.map((a) => a.status)).toEqual(["invalid", "resolved", "pending"]);
  });
});
