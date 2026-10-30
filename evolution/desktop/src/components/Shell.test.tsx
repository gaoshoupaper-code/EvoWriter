import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Shell from "./Shell";
import { changePassword, fetchMeOrNull, logout } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchMeOrNull: vi.fn(),
  logout: vi.fn(),
  changePassword: vi.fn(),
}));
vi.mock("@/components/UpdateBanner", () => ({ default: () => null }));

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(changePassword).mockResolvedValue({ ok: true });
});

describe("Shell 本人修改密码入口", () => {
  it.each([
    { label: "普通用户", is_admin: false, is_super_admin: false },
    { label: "管理员", is_admin: true, is_super_admin: false },
    { label: "超级管理员", is_admin: true, is_super_admin: true },
  ])("$label 均可从侧栏底部账号区修改本人密码", async ({ is_admin, is_super_admin }) => {
    vi.mocked(fetchMeOrNull).mockResolvedValue({
      user_id: "current-user", username: "当前账号", is_admin, is_super_admin, has_api_key: false,
    });
    render(
      <MemoryRouter initialEntries={["/analysis"]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/analysis" element={<h1>分析页内容</h1>} />
          </Route>
          <Route path="/login" element={<h1>登录页</h1>} />
        </Routes>
      </MemoryRouter>,
    );
    const trigger = await screen.findByRole("button", { name: "修改密码" });
    expect(trigger.closest(".shell-user")).toHaveTextContent("当前账号");
    expect(trigger.closest(".shell-sidebar")).not.toBeNull();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    trigger.focus();
    fireEvent.click(trigger);
    expect(await screen.findByRole("dialog", { name: "修改密码" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("当前密码"), { target: { value: "old-password" } });
    fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "new-password" } });
    fireEvent.change(screen.getByLabelText("确认新密码"), { target: { value: "new-password" } });
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    expect(await screen.findByRole("status")).toHaveTextContent("密码已修改，其他设备需重新登录");
    expect(changePassword).toHaveBeenCalledTimes(1);
    expect(changePassword).toHaveBeenCalledWith("old-password", "new-password");
    expect(logout).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() => { expect(trigger).toHaveFocus(); });
    expect(screen.getByRole("heading", { name: "分析页内容" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "登录页" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
  });

  it("登录态检查完成前不显示修改密码入口", () => {
    vi.mocked(fetchMeOrNull).mockReturnValue(new Promise(() => {}));
    render(<MemoryRouter><Shell /></MemoryRouter>);
    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修改密码" })).not.toBeInTheDocument();
  });
});
