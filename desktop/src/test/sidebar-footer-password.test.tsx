import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { SidebarFooter } from "@/components/workspace/SidebarFooter";
import { changePassword } from "@/lib/api";

vi.mock("@/lib/api", () => ({ changePassword: vi.fn() }));
const change = vi.mocked(changePassword);

function mountFooter() {
  const onLogout = vi.fn();
  const onThemeToggle = vi.fn();
  render(
    <MemoryRouter>
      <textarea aria-label="写作草稿" defaultValue="未发送的草稿" />
      <SidebarFooter username="writer" hasApiKey theme="light" onLogout={onLogout} onThemeToggle={onThemeToggle} />
    </MemoryRouter>,
  );
  return { onLogout, onThemeToggle, account: screen.getByRole("button", { name: "用户菜单" }) };
}

beforeEach(() => {
  change.mockReset();
  change.mockResolvedValue({ ok: true });
});

describe("账户菜单修改密码入口", () => {
  it("退出前新增修改密码，不改变 Key 状态和独立设置入口", async () => {
    const user = userEvent.setup();
    const { account } = mountFooter();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "设置（API Key）" })).toHaveAttribute("href", "/settings");
    await user.click(account);
    expect(screen.getByText("Key 已配置")).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["主题", "修改密码", "退出登录"]);
  });

  it("点击先关闭菜单再聚焦弹窗，关闭后回到账户按钮", async () => {
    const user = userEvent.setup();
    const { account, onLogout, onThemeToggle } = mountFooter();
    await user.click(account);
    await user.click(screen.getByRole("menuitem", { name: "修改密码" }));
    const dialog = await screen.findByRole("dialog", { name: "修改密码" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByLabelText("当前密码")).toHaveFocus());
    await user.type(within(dialog).getByLabelText("当前密码"), "old-secret");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(account).toHaveFocus());
    await user.click(account);
    await user.click(screen.getByRole("menuitem", { name: "修改密码" }));
    const reopened = await screen.findByRole("dialog", { name: "修改密码" });
    expect(within(reopened).getByLabelText("当前密码")).toHaveValue("");
    await user.click(within(reopened).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "写作草稿" })).toHaveValue("未发送的草稿");
    expect(onLogout).not.toHaveBeenCalled();
    expect(onThemeToggle).not.toHaveBeenCalled();
    expect(change).not.toHaveBeenCalled();
  });

  it("键盘选择修改密码，焦点不被关闭中的菜单抢回", async () => {
    const user = userEvent.setup();
    const { account } = mountFooter();
    act(() => account.focus());
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: "主题" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "修改密码" })).toHaveFocus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "修改密码" });
    await waitFor(() => expect(within(dialog).getByLabelText("当前密码")).toHaveFocus());
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "确认修改" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(account).toHaveFocus());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: "主题" })).toHaveFocus();
  });

  it("成功只更新弹窗，保留当前工作区且不调用退出", async () => {
    const user = userEvent.setup();
    const { account, onLogout, onThemeToggle } = mountFooter();
    await user.click(account);
    await user.click(screen.getByRole("menuitem", { name: "修改密码" }));
    const dialog = await screen.findByRole("dialog", { name: "修改密码" });
    fireEvent.change(within(dialog).getByLabelText("当前密码"), { target: { value: "old-secret" } });
    fireEvent.change(within(dialog).getByLabelText("新密码", { exact: true }), { target: { value: "new-secret" } });
    fireEvent.change(within(dialog).getByLabelText("确认新密码"), { target: { value: "new-secret" } });
    await user.click(within(dialog).getByRole("button", { name: "确认修改" }));
    expect(await screen.findByRole("status")).toHaveTextContent("密码已修改，其他设备需重新登录");
    expect(change).toHaveBeenCalledExactlyOnceWith("old-secret", "new-secret");
    expect(onLogout).not.toHaveBeenCalled();
    expect(onThemeToggle).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() => expect(account).toHaveFocus());
    expect(screen.getByRole("textbox", { name: "写作草稿" })).toHaveValue("未发送的草稿");
    await user.click(account);
    await user.click(screen.getByRole("menuitem", { name: "退出登录" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });
});
