import { useEffect, useId, useRef, useState, type FormEvent, type RefObject } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { changePassword } from "@/lib/api";

type PasswordField = "current" | "new" | "confirmation";
type FormError = { message: string; field?: PasswordField };

type ChangePasswordDialogProps = {
  onClose: () => void;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
};

// 由入口按需挂载；卸载即丢弃密码和结果，下次打开从空表单开始。
export function ChangePasswordDialog({ onClose, returnFocusRef }: ChangePasswordDialogProps) {
  const id = useId();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<FormError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const pending = useRef(false);
  const currentInput = useRef<HTMLInputElement>(null);
  const newInput = useRef<HTMLInputElement>(null);
  const confirmationInput = useRef<HTMLInputElement>(null);
  const successMessage = useRef<HTMLParagraphElement>(null);
  const rulesId = `${id}-rules`;
  const errorId = `${id}-error`;

  useEffect(() => {
    if (success) successMessage.current?.focus();
  }, [success]);

  function rejectField(field: PasswordField, message: string) {
    setError({ field, message });
    const inputs = { current: currentInput, new: newInput, confirmation: confirmationInput };
    inputs[field].current?.focus();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current || success) return;
    setError(null);

    if (!currentPassword) return rejectField("current", "请输入当前密码。");
    const length = Array.from(newPassword).length;
    if (length < 6 || length > 128) return rejectField("new", "新密码须为 6–128 个字符。");
    if (newPassword === currentPassword) return rejectField("new", "新密码不能与当前密码相同。");
    if (!confirmation) return rejectField("confirmation", "请再次输入新密码。");
    if (confirmation !== newPassword) return rejectField("confirmation", "两次输入的新密码不一致。");

    // 同步锁防住连续提交，也让 Esc、遮罩与关闭按钮共享同一请求状态。
    pending.current = true;
    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setSuccess(true);
    } catch (cause) {
      setError({ message: cause instanceof Error && cause.message ? cause.message : "修改密码失败，请稍后重试。" });
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  }

  function describedBy(field: PasswordField) {
    return error?.field === field ? `${rulesId} ${errorId}` : rulesId;
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !pending.current) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay">
          <Dialog.Content
            className="modal-content change-password-dialog"
            aria-modal="true"
            aria-describedby={rulesId}
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              currentInput.current?.focus();
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              returnFocusRef.current?.focus();
            }}
            onEscapeKeyDown={(event) => { if (pending.current) event.preventDefault(); }}
            onInteractOutside={(event) => { if (pending.current) event.preventDefault(); }}
          >
            <Dialog.Title className="modal-title">修改密码</Dialog.Title>
            <Dialog.Description className="modal-description" id={rulesId}>
              {success ? "当前设备保持登录。" : "新密码须为 6–128 个字符，且不能与当前密码相同。"}
            </Dialog.Description>
            {success ? (
              <div className="auth-form">
                <p className="change-password-success" role="status" tabIndex={-1} ref={successMessage}>
                  密码已修改，其他设备需重新登录
                </p>
                <div className="modal-actions">
                  <Dialog.Close asChild>
                    <button className="modal-button modal-primary" type="button">完成</button>
                  </Dialog.Close>
                </div>
              </div>
            ) : (
              <form className="auth-form" aria-label="修改密码表单" aria-busy={submitting} noValidate onSubmit={handleSubmit}>
                <label className="auth-field" htmlFor={`${id}-current`}>
                  当前密码
                  <input
                    ref={currentInput}
                    id={`${id}-current`}
                    name="current_password"
                    className="auth-input"
                    type="password"
                    autoComplete="current-password"
                    required
                    disabled={submitting}
                    aria-invalid={error?.field === "current"}
                    aria-describedby={describedBy("current")}
                    value={currentPassword}
                    onChange={(event) => { setCurrentPassword(event.target.value); setError(null); }}
                  />
                </label>
                <label className="auth-field" htmlFor={`${id}-new`}>
                  新密码
                  <input
                    ref={newInput}
                    id={`${id}-new`}
                    name="new_password"
                    className="auth-input"
                    type="password"
                    autoComplete="new-password"
                    required
                    disabled={submitting}
                    aria-invalid={error?.field === "new"}
                    aria-describedby={describedBy("new")}
                    value={newPassword}
                    onChange={(event) => { setNewPassword(event.target.value); setError(null); }}
                  />
                </label>
                <label className="auth-field" htmlFor={`${id}-confirmation`}>
                  确认新密码
                  <input
                    ref={confirmationInput}
                    id={`${id}-confirmation`}
                    name="confirm_password"
                    className="auth-input"
                    type="password"
                    autoComplete="new-password"
                    required
                    disabled={submitting}
                    aria-invalid={error?.field === "confirmation"}
                    aria-describedby={describedBy("confirmation")}
                    value={confirmation}
                    onChange={(event) => { setConfirmation(event.target.value); setError(null); }}
                  />
                </label>
                {error ? <p className="change-password-error" role="alert" id={errorId}>{error.message}</p> : null}
                <div className="modal-actions">
                  <Dialog.Close asChild>
                    <button className="modal-button modal-cancel" type="button" disabled={submitting}>取消</button>
                  </Dialog.Close>
                  <button className="modal-button modal-primary" type="submit" disabled={submitting}>
                    {submitting ? "修改中…" : "确认修改"}
                  </button>
                </div>
              </form>
            )}
          </Dialog.Content>
        </Dialog.Overlay>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
