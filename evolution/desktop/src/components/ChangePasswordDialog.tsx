import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { changePassword } from "@/lib/api";
import "./ChangePasswordDialog.css";

type PasswordField = "current" | "new" | "confirm";
type PasswordError = { message: string; field?: PasswordField };

export default function ChangePasswordDialog() {
  const [open, setOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<PasswordError | null>(null);
  const [success, setSuccess] = useState(false);
  // 同步锁同时拦住连续提交、Esc 和遮罩关闭，不依赖下一次渲染。
  const pending = useRef(false);
  const currentRef = useRef<HTMLInputElement>(null);
  const newRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);
  const successRef = useRef<HTMLParagraphElement>(null);
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  useEffect(() => {
    if (success) successRef.current?.focus();
  }, [success]);

  function clearPasswords() {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmation("");
  }

  function handleOpenChange(nextOpen: boolean) {
    if (pending.current) return;
    clearPasswords();
    setError(null);
    setSuccess(false);
    setOpen(nextOpen);
  }

  function showValidationError(field: PasswordField, message: string) {
    setError({ field, message });
    const refs = { current: currentRef, new: newRef, confirm: confirmRef };
    refs[field].current?.focus();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current || success) return;
    if (!currentPassword) {
      showValidationError("current", "请输入当前密码");
      return;
    }
    // 按 Unicode 字符计数，与服务端一致；密码不裁剪空格。
    const length = Array.from(newPassword).length;
    if (length < 6 || length > 128) {
      showValidationError("new", "新密码须为 6–128 个字符");
      return;
    }
    if (newPassword === currentPassword) {
      showValidationError("new", "新密码不能与当前密码相同");
      return;
    }
    if (confirmation !== newPassword) {
      showValidationError("confirm", "两次输入的新密码不一致");
      return;
    }

    pending.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await changePassword(currentPassword, newPassword);
      clearPasswords();
      setSuccess(true);
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : "修改密码失败，请稍后重试" });
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Trigger asChild>
        <button type="button" className="shell-change-password">修改密码</button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay change-password-overlay" />
        <Dialog.Content
          className="modal-card change-password-dialog"
          onEscapeKeyDown={(event) => { if (pending.current) event.preventDefault(); }}
          onInteractOutside={(event) => { if (pending.current) event.preventDefault(); }}
        >
          <Dialog.Title className="modal-title">修改密码</Dialog.Title>
          <Dialog.Description className="modal-hint">
            修改当前账号密码。当前设备保持登录，其他设备需重新登录。
          </Dialog.Description>
          {success ? (
            <>
              <p className="change-password-success" role="status" tabIndex={-1} ref={successRef}>
                密码已修改，其他设备需重新登录
              </p>
              <div className="modal-actions">
                <Dialog.Close asChild>
                  <button type="button" className="modal-button">完成</button>
                </Dialog.Close>
              </div>
            </>
          ) : (
            <form onSubmit={handleSubmit} noValidate aria-busy={submitting} aria-describedby={error ? errorId : undefined}>
              <label className="modal-field">
                <span>当前密码</span>
                <input
                  ref={currentRef}
                  name="current_password"
                  type="password"
                  autoComplete="current-password"
                  required
                  readOnly={submitting}
                  value={currentPassword}
                  onChange={(event) => { setCurrentPassword(event.target.value); setError(null); }}
                  aria-invalid={error?.field === "current"}
                  aria-describedby={error?.field === "current" ? errorId : undefined}
                />
              </label>
              <label className="modal-field">
                <span>新密码</span>
                <input
                  ref={newRef}
                  name="new_password"
                  type="password"
                  autoComplete="new-password"
                  required
                  readOnly={submitting}
                  value={newPassword}
                  onChange={(event) => { setNewPassword(event.target.value); setError(null); }}
                  aria-invalid={error?.field === "new"}
                  aria-describedby={error?.field === "new" ? `${hintId} ${errorId}` : hintId}
                />
              </label>
              <p id={hintId} className="modal-hint">6–128 个字符，且不能与当前密码相同。</p>
              <label className="modal-field">
                <span>确认新密码</span>
                <input
                  ref={confirmRef}
                  name="confirm_password"
                  type="password"
                  autoComplete="new-password"
                  required
                  readOnly={submitting}
                  value={confirmation}
                  onChange={(event) => { setConfirmation(event.target.value); setError(null); }}
                  aria-invalid={error?.field === "confirm"}
                  aria-describedby={error?.field === "confirm" ? errorId : undefined}
                />
              </label>
              {error && <p id={errorId} className="change-password-error" role="alert">{error.message}</p>}
              <div className="modal-actions">
                <Dialog.Close asChild>
                  <button type="button" className="modal-button" disabled={submitting}>取消</button>
                </Dialog.Close>
                <button type="submit" className="modal-button change-password-submit" disabled={submitting}>
                  {submitting ? "修改中…" : "确认修改"}
                </button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
