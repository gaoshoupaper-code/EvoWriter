import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChangePasswordDialog from "./ChangePasswordDialog";
import { changePassword } from "@/lib/api";

vi.mock("@/lib/api", () => ({ changePassword: vi.fn() }));

const changePasswordMock = vi.mocked(changePassword);

afterEach(cleanup);
beforeEach(() => {
  changePasswordMock.mockReset();
  changePasswordMock.mockResolvedValue({ ok: true });
});

async function openDialog() {
  render(<ChangePasswordDialog />);
  const trigger = screen.getByRole("button", { name: "修改密码" });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "修改密码" });
  const current = screen.getByLabelText("当前密码") as HTMLInputElement;
  const next = screen.getByLabelText("新密码") as HTMLInputElement;
  const confirm = screen.getByLabelText("确认新密码") as HTMLInputElement;
  const form = current.closest("form")!;
  return { trigger, dialog, current, next, confirm, form };
}

function fillPasswords(current = "old-password", next = "new-password", confirm = next) {
  fireEvent.change(screen.getByLabelText("当前密码"), { target: { value: current } });
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value: next } });
  fireEvent.change(screen.getByLabelText("确认新密码"), { target: { value: confirm } });
}

describe("ChangePasswordDialog", () => {
  it("密码遮罩、自动填充和初始焦点正确，Tab 焦点留在弹窗内", async () => {
    const { current, next, confirm, dialog } = await openDialog();
    expect(current).toHaveFocus();
    for (const input of [current, next, confirm]) {
      expect(input).toHaveAttribute("type", "password");
      expect(input).toBeRequired();
    }
    expect(current).toHaveAttribute("autocomplete", "current-password");
    expect(next).toHaveAttribute("autocomplete", "new-password");
    expect(confirm).toHaveAttribute("autocomplete", "new-password");
    expect(dialog).toHaveAccessibleDescription("修改当前账号密码。当前设备保持登录，其他设备需重新登录。");

    const submit = screen.getByRole("button", { name: "确认修改" });
    submit.focus();
    fireEvent.keyDown(submit, { key: "Tab", code: "Tab" });
    expect(current).toHaveFocus();
    fireEvent.keyDown(current, { key: "Tab", code: "Tab", shiftKey: true });
    expect(submit).toHaveFocus();
  });

  it.each([
    ["", "new-password", "new-password", "请输入当前密码", "当前密码"],
    ["old-password", "", "", "新密码须为 6–128 个字符", "新密码"],
    ["old-password", "12345", "12345", "新密码须为 6–128 个字符", "新密码"],
    ["old-password", "a".repeat(129), "a".repeat(129), "新密码须为 6–128 个字符", "新密码"],
    ["old-password", "old-password", "old-password", "新密码不能与当前密码相同", "新密码"],
    ["old-password", "new-password", "different-password", "两次输入的新密码不一致", "确认新密码"],
    ["old-password", "new-password", "", "两次输入的新密码不一致", "确认新密码"],
  ])("本地拦截非法输入 %#", async (current, next, confirm, message, field) => {
    const { form } = await openDialog();
    fillPasswords(current, next, confirm);
    fireEvent.submit(form);

    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(screen.getByLabelText(field)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText(field)).toHaveFocus();
    expect(screen.getByLabelText(field)).toHaveAccessibleDescription(new RegExp(message));
    expect(changePasswordMock).not.toHaveBeenCalled();
  });

  it.each(["123456", "a".repeat(128), "密".repeat(128), "😀".repeat(128), " new-password "])(
    "接受合法边界和原始密码 %#，成功清空并明确提示其他设备重新登录",
    async (next) => {
      const { form } = await openDialog();
      fillPasswords(" old-password ", next);
      fireEvent.submit(form);

      expect(changePasswordMock).toHaveBeenCalledTimes(1);
      expect(changePasswordMock).toHaveBeenCalledWith(" old-password ", next);
      const status = await screen.findByRole("status");
      expect(status).toHaveTextContent("密码已修改，其他设备需重新登录");
      expect(status).toHaveFocus();
      expect(screen.queryByLabelText("当前密码")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("确认新密码")).not.toBeInTheDocument();
    },
  );

  it("请求中防重复提交，并阻止取消、Esc 和遮罩关闭", async () => {
    let resolve!: (value: { ok: true }) => void;
    changePasswordMock.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const { form, dialog, current } = await openDialog();
    fillPasswords();
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });

    expect(changePasswordMock).toHaveBeenCalledTimes(1);
    expect(form).toHaveAttribute("aria-busy", "true");
    expect(current).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "修改中…" })).toBeDisabled();
    const cancel = screen.getByRole("button", { name: "取消" });
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });
    // Radix 延后一拍注册外部点击，等待监听器就绪。
    await act(async () => { await new Promise((done) => setTimeout(done, 0)); });
    fireEvent.pointerDown(document.querySelector(".change-password-overlay")!, { button: 0, pointerType: "mouse" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => { resolve({ ok: true }); });
    expect(await screen.findByRole("status")).toHaveTextContent("密码已修改，其他设备需重新登录");
  });

  it("后端错误保留表单，允许修改后重试", async () => {
    changePasswordMock.mockRejectedValueOnce(new Error("当前密码错误"));
    const { current, next, confirm, form } = await openDialog();
    fillPasswords();
    fireEvent.submit(form);

    expect(await screen.findByRole("alert")).toHaveTextContent("当前密码错误");
    expect(current).toHaveValue("old-password");
    expect(next).toHaveValue("new-password");
    expect(confirm).toHaveValue("new-password");
    expect(form).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "确认修改" })).toBeEnabled();

    fireEvent.change(current, { target: { value: "correct-password" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.submit(form);
    expect(await screen.findByRole("status")).toBeInTheDocument();
    expect(changePasswordMock).toHaveBeenLastCalledWith("correct-password", "new-password");
  });

  it.each(["取消", "Escape", "遮罩"])("通过 %s 关闭后恢复入口焦点，重开清空密码和校验错误", async (method) => {
    const { trigger, dialog, form } = await openDialog();
    fillPasswords("old-password", "short");
    fireEvent.submit(form);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    if (method === "取消") {
      fireEvent.click(screen.getByRole("button", { name: "取消" }));
    } else if (method === "Escape") {
      fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });
    } else {
      await act(async () => { await new Promise((done) => setTimeout(done, 0)); });
      fireEvent.pointerDown(document.querySelector(".change-password-overlay")!, { button: 0, pointerType: "mouse" });
    }
    await waitFor(() => { expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); });
    await waitFor(() => { expect(trigger).toHaveFocus(); });
    fireEvent.click(trigger);
    expect(await screen.findByLabelText("当前密码")).toHaveValue("");
    expect(screen.getByLabelText("新密码")).toHaveValue("");
    expect(screen.getByLabelText("确认新密码")).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("关闭重开清空服务端错误", async () => {
    changePasswordMock.mockRejectedValueOnce(new Error("当前密码错误"));
    const { trigger, form } = await openDialog();
    fillPasswords();
    fireEvent.submit(form);
    expect(await screen.findByRole("alert")).toHaveTextContent("当前密码错误");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => { expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); });
    fireEvent.click(trigger);
    expect(await screen.findByLabelText("当前密码")).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("成功后完成关闭，重开回到空白表单", async () => {
    const { trigger, form } = await openDialog();
    fillPasswords();
    fireEvent.submit(form);
    fireEvent.click(await screen.findByRole("button", { name: "完成" }));
    await waitFor(() => { expect(trigger).toHaveFocus(); });
    fireEvent.click(trigger);
    expect(await screen.findByLabelText("当前密码")).toHaveValue("");
    expect(screen.getByLabelText("新密码")).toHaveValue("");
    expect(screen.getByLabelText("确认新密码")).toHaveValue("");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
