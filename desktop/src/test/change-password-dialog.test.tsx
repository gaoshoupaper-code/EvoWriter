import { useRef, useState } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChangePasswordDialog } from "@/components/workspace/ChangePasswordDialog";
import { changePassword } from "@/lib/api";

vi.mock("@/lib/api", () => ({ changePassword: vi.fn() }));
const change = vi.mocked(changePassword);

function DialogHarness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(true);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={trigger} type="button" onClick={() => setOpen(true)}>打开改密弹窗</button>
      {open ? (
        <ChangePasswordDialog returnFocusRef={trigger} onClose={() => { onClose(); setOpen(false); }} />
      ) : null}
    </>
  );
}

function mountDialog() {
  const onClose = vi.fn();
  render(<DialogHarness onClose={onClose} />);
  return onClose;
}

function fields() {
  const dialog = within(screen.getByRole("dialog", { name: "修改密码" }));
  return {
    current: dialog.getByLabelText("当前密码") as HTMLInputElement,
    next: dialog.getByLabelText("新密码", { exact: true }) as HTMLInputElement,
    confirmation: dialog.getByLabelText("确认新密码") as HTMLInputElement,
  };
}

function fill(current = "old-secret", next = "new-secret", confirmation = next) {
  const inputs = fields();
  fireEvent.change(inputs.current, { target: { value: current } });
  fireEvent.change(inputs.next, { target: { value: next } });
  fireEvent.change(inputs.confirmation, { target: { value: confirmation } });
}

beforeEach(() => {
  change.mockReset();
  change.mockResolvedValue({ ok: true });
});

describe("修改密码表单", () => {
  it("三个字段均遮蔽密码，自动填充语义正确，并关联规则说明", async () => {
    mountDialog();
    const inputs = fields();
    const rules = screen.getByText("新密码须为 6–128 个字符，且不能与当前密码相同。");
    const dialog = screen.getByRole("dialog", { name: "修改密码" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-describedby", rules.id);
    expect(inputs.current).toHaveAttribute("autocomplete", "current-password");
    expect(inputs.next).toHaveAttribute("autocomplete", "new-password");
    expect(inputs.confirmation).toHaveAttribute("autocomplete", "new-password");
    for (const input of Object.values(inputs)) {
      expect(input).toHaveAttribute("type", "password");
      expect(input).toHaveAttribute("aria-describedby", rules.id);
      expect(input).toBeRequired();
      expect(input).toHaveValue("");
    }
    await waitFor(() => expect(inputs.current).toHaveFocus());
    expect(change).not.toHaveBeenCalled();
  });

  it.each([
    { current: "", next: "new-secret", confirmation: "new-secret", field: "current", message: "请输入当前密码。" },
    { current: "old-secret", next: "", confirmation: "", field: "next", message: "新密码须为 6–128 个字符。" },
    { current: "old-secret", next: "12345", confirmation: "12345", field: "next", message: "新密码须为 6–128 个字符。" },
    { current: "old-secret", next: "a".repeat(129), confirmation: "a".repeat(129), field: "next", message: "新密码须为 6–128 个字符。" },
    { current: "old-secret", next: "old-secret", confirmation: "old-secret", field: "next", message: "新密码不能与当前密码相同。" },
    { current: "old-secret", next: "new-secret", confirmation: "", field: "confirmation", message: "请再次输入新密码。" },
    { current: "old-secret", next: "new-secret", confirmation: "different-secret", field: "confirmation", message: "两次输入的新密码不一致。" },
  ] as const)("阻止无效输入并聚焦错误字段：$message / $field", async ({ current, next, confirmation, field, message }) => {
    const user = userEvent.setup();
    mountDialog();
    fill(current, next, confirmation);
    await user.click(screen.getByRole("button", { name: "确认修改" }));
    const error = screen.getByRole("alert");
    const invalidInput = fields()[field];
    expect(error).toHaveTextContent(message);
    expect(invalidInput).toHaveFocus();
    expect(invalidInput).toHaveAttribute("aria-invalid", "true");
    expect(invalidInput.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
    expect(change).not.toHaveBeenCalled();
    fireEvent.change(invalidInput, { target: { value: "corrected-secret" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(invalidInput).toHaveAttribute("aria-invalid", "false");
  });

  it.each(["123456", "a".repeat(128), "密".repeat(6), "\u{1D49C}".repeat(128), " new secret "])(
    "接受合法边界和完整字符，不裁剪密码：%s",
    async (password) => {
      const user = userEvent.setup();
      mountDialog();
      fill(" old secret ", password);
      await user.click(screen.getByRole("button", { name: "确认修改" }));
      expect(await screen.findByRole("status")).toHaveTextContent("密码已修改，其他设备需重新登录");
      expect(change).toHaveBeenCalledExactlyOnceWith(" old secret ", password);
    },
  );

  it.each([
    { cause: new Error("当前密码错误"), message: "当前密码错误" },
    { cause: new Error("网络连接失败"), message: "网络连接失败" },
    { cause: "offline", message: "修改密码失败，请稍后重试。" },
  ])("失败保留可见错误和表单，允许重试：$message", async ({ cause, message }) => {
    const user = userEvent.setup();
    change.mockRejectedValueOnce(cause);
    const onClose = mountDialog();
    fill();
    await user.click(screen.getByRole("button", { name: "确认修改" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(fields().current).toHaveValue("old-secret");
    expect(fields().next).toHaveValue("new-secret");
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "确认修改" })).toBeEnabled();
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "确认修改" }));
    expect(await screen.findByRole("status")).toHaveTextContent("密码已修改，其他设备需重新登录");
    expect(change).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("提交中防重复并禁止按钮、Esc、遮罩关闭，失败后解锁", async () => {
    const user = userEvent.setup();
    let rejectRequest!: (cause: Error) => void;
    change.mockReturnValueOnce(new Promise((_resolve, reject) => { rejectRequest = reject; }));
    const onClose = mountDialog();
    fill();
    const form = screen.getByRole("form", { name: "修改密码表单" });
    act(() => {
      fireEvent.submit(form);
      fireEvent.submit(form);
    });
    expect(change).toHaveBeenCalledTimes(1);
    expect(form).toHaveAttribute("aria-busy", "true");
    for (const input of Object.values(fields())) expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "修改中…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("dialog").parentElement!);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(change).toHaveBeenCalledTimes(1);

    await act(async () => { rejectRequest(new Error("网络连接失败")); });
    expect(await screen.findByRole("alert")).toHaveTextContent("网络连接失败");
    expect(form).toHaveAttribute("aria-busy", "false");
    for (const input of Object.values(fields())) expect(input).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it.each(["取消", "Esc", "遮罩"])("通过 %s 关闭后重新打开，密码和错误全部清空", async (method) => {
    const user = userEvent.setup();
    const onClose = mountDialog();
    fill("old-secret", "new-secret", "mismatch");
    await user.click(screen.getByRole("button", { name: "确认修改" }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    if (method === "取消") await user.click(screen.getByRole("button", { name: "取消" }));
    else if (method === "Esc") await user.keyboard("{Escape}");
    else await user.click(screen.getByRole("dialog").parentElement!);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onClose).toHaveBeenCalledTimes(1);
    const trigger = screen.getByRole("button", { name: "打开改密弹窗" });
    await waitFor(() => expect(trigger).toHaveFocus());
    await user.click(trigger);
    for (const input of Object.values(fields())) expect(input).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(fields().current).toHaveFocus());
  });

  it("Tab 与 Shift+Tab 将焦点保持在弹窗内", async () => {
    const user = userEvent.setup();
    mountDialog();
    await waitFor(() => expect(fields().current).toHaveFocus());
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "确认修改" })).toHaveFocus();
    await user.tab();
    expect(fields().current).toHaveFocus();
    await user.tab();
    expect(fields().next).toHaveFocus();
    await user.tab();
    expect(fields().confirmation).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
  });

  it("成功移除密码字段，明确会话影响，关闭重开为空表单且不写存储", async () => {
    const user = userEvent.setup();
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    try {
      const onClose = mountDialog();
      fill();
      await user.click(screen.getByRole("button", { name: "确认修改" }));
      const status = await screen.findByRole("status");
      expect(status).toHaveTextContent("密码已修改，其他设备需重新登录");
      expect(screen.getByText("当前设备保持登录。")).toBeInTheDocument();
      await waitFor(() => expect(status).toHaveFocus());
      expect(screen.queryByLabelText("当前密码")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("新密码", { exact: true })).not.toBeInTheDocument();
      expect(screen.queryByLabelText("确认新密码")).not.toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "完成" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await user.click(screen.getByRole("button", { name: "打开改密弹窗" }));
      for (const input of Object.values(fields())) expect(input).toHaveValue("");
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(storageWrite).not.toHaveBeenCalled();
    } finally {
      storageWrite.mockRestore();
    }
  });
});
