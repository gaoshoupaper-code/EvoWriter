import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { formatCredits, parseCreditAmount } from "@/lib/billing";
import { toast } from "sonner";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetClose,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import {
  fetchUsers,
  updateUser,
  resetPassword,
  adjustCredits,
  type AdminUser,
} from "@/lib/api";

/**
 * 用户管理（super_admin 专属）。
 *
 * 列出所有注册用户，支持：
 *   - 调积分（Sheet 表单，正=发放 / 负=扣减）
 *   - 重置密码（返回临时密码，toast 10s 展示）
 *   - 禁用 / 启用（updateUser disabled 切换；超管不可禁用）
 */
export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);

  // 调积分 Sheet 状态
  const [creditsTarget, setCreditsTarget] = useState<AdminUser | null>(null);
  const [creditsOpen, setCreditsOpen] = useState(false);
  const [creditsAmount, setCreditsAmount] = useState("");
  const [creditsNote, setCreditsNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const requestId = useRef("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setUsers(await fetchUsers());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取用户列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function openCredits(user: AdminUser) {
    requestId.current = "";
    setCreditsTarget(user);
    setCreditsAmount("");
    setCreditsNote("");
    setCreditsOpen(true);
  }

  async function handleAdjustCredits() {
    if (!creditsTarget || submitting) return;
    const amt = parseCreditAmount(creditsAmount);
    if (!Number.isFinite(amt) || amt === 0) {
      toast.error("请输入非零积分，最多保留两位小数");
      return;
    }
    if (!creditsNote.trim()) {
      toast.error("请填写调整原因");
      return;
    }
    requestId.current ||= crypto.randomUUID();
    setSubmitting(true);
    try {
      await adjustCredits(creditsTarget.user_id, amt, creditsNote.trim(), requestId.current);
      toast.success(`已调整 ${amt > 0 ? "+" : ""}${formatCredits(amt)} 积分`);
      setCreditsOpen(false);
      setCreditsTarget(null);
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "调整积分失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResetPassword(user: AdminUser) {
    try {
      const { temp_password } = await resetPassword(user.user_id);
      toast.success(`「${user.username}」密码已重置，临时密码：${temp_password}`, {
        duration: 10000,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重置密码失败");
    }
  }

  async function handleToggleDisable(user: AdminUser) {
    try {
      await updateUser(user.user_id, { disabled: !user.disabled });
      toast.success(user.disabled ? "已启用" : "已禁用");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败");
    }
  }

  if (loading) {
    return <div className="page-loading">加载中…</div>;
  }

  return (
    <div className="admin-page">
      <header className="page-header">
        <h1>用户管理</h1>
        <p className="page-desc">管理所有注册用户、积分余额和账号状态</p>
      </header>
      {error && <p role="alert" className="config-test-result fail">用户列表未更新：{error} <button className="config-button small" onClick={() => { setError(""); void refresh(); }}>重试</button></p>}

      <table className="data-table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>可用积分</th>
            <th>角色</th>
            <th>状态</th>
            <th>作品数</th>
            <th>注册时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.user_id}>
              <td className="mono">{u.username}</td>
              <td className="mono" style={{ fontWeight: 700 }}>
                <span style={{ color: u.credits_balance <= 0 ? "var(--danger)" : "inherit" }}>
                  {formatCredits(u.credits_balance)}
                </span>
              </td>
              <td>
                {u.is_super_admin ? (
                  <Badge variant="destructive">超管</Badge>
                ) : u.is_admin ? (
                  <Badge variant="secondary">管理员</Badge>
                ) : (
                  <Badge variant="outline">用户</Badge>
                )}
              </td>
              <td>
                {u.disabled ? (
                  <Badge variant="destructive">已禁用</Badge>
                ) : u.credits_balance <= 0 ? (
                  <Badge variant="outline">积分待补充</Badge>
                ) : (
                  <Badge variant="completed">正常</Badge>
                )}
              </td>
              <td>{u.workspace_count}</td>
              <td className="mono text-mute">{fmtTime(u.created_at)}</td>
              <td>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button className="config-button small" onClick={() => openCredits(u)}>
                    调积分
                  </button>
                  <Link className="config-button small" to={`/admin/credits?${new URLSearchParams({ user_id: u.user_id })}`}>
                    查看账单
                  </Link>
                  <button
                    className="config-button small"
                    onClick={() => handleResetPassword(u)}
                  >
                    重置密码
                  </button>
                  <button
                    className={`config-button small ${u.disabled ? "" : "danger"}`}
                    onClick={() => handleToggleDisable(u)}
                    disabled={u.is_super_admin}
                  >
                    {u.disabled ? "启用" : "禁用"}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {users.length === 0 && <div className="config-empty">暂无用户</div>}

      {/* 调积分 Sheet */}
      <Sheet open={creditsOpen} onOpenChange={(o) => !submitting && setCreditsOpen(o)}>
        <SheetContent side="right">
          <SheetHeader>
            <SheetTitle>调整积分 · {creditsTarget?.username}</SheetTitle>
            <SheetClose asChild>
              <button className="sheet-close-x">✕</button>
            </SheetClose>
          </SheetHeader>
          <div className="sheet-body">
            <label className="config-field">
              <span className="config-label">调整额度（正=发放，负=扣减）</span>
              <input
                className="config-input"
                type="number"
                step="0.01"
                value={creditsAmount}
                onChange={(e) => { requestId.current = ""; setCreditsAmount(e.target.value); }}
                placeholder="如 12.34 或 -5.00"
                disabled={submitting}
              />
            </label>
            <label className="config-field">
              <span className="config-label">调整原因（必填）</span>
              <input
                className="config-input"
                value={creditsNote}
                onChange={(e) => { requestId.current = ""; setCreditsNote(e.target.value); }}
                placeholder="填写发放或纠错原因"
                maxLength={200}
                required
                disabled={submitting}
              />
            </label>
            <p className="config-hint">可用积分：{formatCredits(creditsTarget?.credits_balance)}。减分不能动用预留积分，也不能产生负余额。</p>
            <div className="config-actions">
              <button
                className="config-button primary"
                onClick={handleAdjustCredits}
                disabled={submitting}
              >
                {submitting ? "提交中…" : "确认调整"}
              </button>
              <button
                className="config-button ghost"
                onClick={() => setCreditsOpen(false)}
                disabled={submitting}
              >
                取消
              </button>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}
