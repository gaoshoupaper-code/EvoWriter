import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import {
  fetchAllTransactions, fetchUserTransactions, fetchUsers, fetchBillingOperations, fetchBillingOperation,
  type AdminUser, type CreditTransaction, type BillingOperation, type BillingOperationDetail,
} from "@/lib/api";
import { BILLING_STATUS, formatCredits } from "@/lib/billing";

const TYPE_LABELS: Record<string, string> = {
  invite_grant: "邀请码到账", admin_adjust: "管理员调整", creation_consume: "创作消耗",
  creation_refund: "系统故障退分", migration_adjust: "旧负余额归零",
};

export default function AdminCredits() {
  const [searchParams, setSearchParams] = useSearchParams();
  const userId = searchParams.get("user_id") ?? "";
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersError, setUsersError] = useState("");
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);
  const [operations, setOperations] = useState<BillingOperation[]>([]);
  const [loadedUserId, setLoadedUserId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(50);
  const refreshId = useRef(0);
  const loadUsers = useCallback(async () => {
    try { setUsers(await fetchUsers()); setUsersError(""); }
    catch (err) { setUsersError(err instanceof Error ? err.message : "读取用户列表失败"); }
  }, []);
  useEffect(() => { void loadUsers(); }, [loadUsers]);
  const refresh = useCallback(async () => {
    const request = ++refreshId.current;
    setLoading(true);
    setError("");
    try {
      const [ops, txs] = await Promise.all([
        fetchBillingOperations(limit, userId),
        userId ? fetchUserTransactions(userId, limit) : fetchAllTransactions(limit),
      ]);
      if (request !== refreshId.current) return;
      setOperations(ops);
      setTransactions(txs);
      setLoadedUserId(userId);
      setError("");
    } catch (err) {
      if (request === refreshId.current) setError(err instanceof Error ? err.message : "读取积分账目失败");
    } finally {
      if (request === refreshId.current) setLoading(false);
    }
  }, [limit, userId]);
  useEffect(() => { void refresh(); return () => { refreshId.current += 1; }; }, [refresh]);
  const visibleOperations = loadedUserId === userId ? operations : [];
  const visibleTransactions = loadedUserId === userId ? transactions : [];

  return <div className="admin-page billing-admin">
    <header className="page-header billing-admin-heading">
      <div><h1>积分账目</h1><p className="page-desc">按操作核对预算、实际扣费和退款，再查看余额变动流水。</p></div>
      <div className="billing-admin-actions">
        <select className="evolve-select" aria-label="筛选用户" value={userId} onChange={(event) => {
          const next = new URLSearchParams(searchParams);
          if (event.target.value) next.set("user_id", event.target.value); else next.delete("user_id");
          setSearchParams(next);
        }}>
          <option value="">全部用户</option>
          {userId && !users.some((user) => user.user_id === userId) && <option value={userId}>用户 {userId}</option>}
          {users.map((user) => <option key={user.user_id} value={user.user_id}>{user.username}</option>)}
        </select>
        <select className="evolve-select" aria-label="账目条数" value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
          <option value={50}>最近 50 条</option><option value={100}>最近 100 条</option><option value={200}>最近 200 条</option>
        </select>
        <button className="config-button secondary" onClick={refresh} disabled={loading}>{loading ? "刷新中…" : "刷新"}</button>
      </div>
    </header>
    {usersError && <p role="alert" className="config-test-result fail">用户列表未更新：{usersError} <button className="config-button small" onClick={loadUsers}>重试用户列表</button></p>}
    {error && <p role="alert" className="config-test-result fail">账目未更新：{error}。请重试，旧数据不代表当前状态。</p>}
    {loading && !visibleOperations.length && !visibleTransactions.length && <div className="page-loading">加载账目…</div>}
    <section className="billing-admin-section">
      <div className="admin-section-head"><h2>创作操作</h2><span className="text-mute">预留不是消费，展开可看每次模型调用</span></div>
      {!loading && !error && !visibleOperations.length && <p className="config-empty">暂无新计费操作</p>}
      <div className="billing-operation-list">{visibleOperations.map((operation) => <OperationRow key={operation.operation_id} operation={operation} />)}</div>
    </section>
    <section className="billing-admin-section">
      <h2>余额变动流水</h2>
      <div className="billing-table-scroll"><table className="data-table">
        <thead><tr><th>时间 / 操作者</th><th>用户</th><th>类型</th><th>变动积分</th><th>变动后总余额</th><th>原因</th></tr></thead>
        <tbody>{visibleTransactions.map((row) => <tr key={(row.legacy ? "old:" : "new:") + row.tx_id}>
          <td className="mono text-mute">{fmtTime(row.created_at)}<div>{row.created_by ?? "system"}</div></td>
          <td className="mono" title={row.user_id}>{row.user_id.slice(0, 8)}</td>
          <td>{row.legacy && row.type === "creation_refund" ? "旧预扣退还" : TYPE_LABELS[row.type] ?? row.type}{row.legacy && <span className="billing-legacy">旧账</span>}</td>
          <td className="mono">{row.amount > 0 ? "+" : ""}{formatCredits(row.amount)}</td>
          <td className="mono">{formatCredits(row.balance_after)}</td>
          <td>{row.note ?? "无备注"}</td>
        </tr>)}</tbody>
      </table></div>
      {!loading && !error && !visibleTransactions.length && <p className="config-empty">暂无积分流水</p>}
    </section>
  </div>;
}

function OperationRow({ operation }: { operation: BillingOperation }) {
  const [detail, setDetail] = useState<BillingOperationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  async function load() {
    if (loading) return;
    setLoading(true);
    try {
      setDetail(await fetchBillingOperation(operation.operation_id));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取调用明细失败");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { if (open) void load(); }, [open, operation.operation_id, operation.status, operation.consumed, operation.charged, operation.missing_usage]);
  const pending = ["reserved", "running", "processing"].includes(operation.status);
  return <details className="billing-operation" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>
      <span className="billing-operation-identity"><span className="mono">{operation.user_id.slice(0, 8)}</span><span className="text-mute">{fmtTime(operation.created_at)}</span></span>
      <Badge variant={operation.status === "failed" ? "failed" : pending ? "running" : "completed"}>{BILLING_STATUS[operation.status] ?? operation.status}</Badge>
      <span className="mono">预算 {formatCredits(operation.budget)}</span>
      <strong className="mono">{pending ? "待结算" : "实际扣 " + formatCredits(operation.charged) + " 分"}</strong>
    </summary>
    <div className="billing-operation-detail">
      <p className="config-hint">操作 {operation.operation_id} · 会话 {operation.thread_id} {operation.trace_id ? "· 运行 " + operation.trace_id : ""}</p>
      {operation.status === "failed" && <p>本笔操作因系统故障全退，最终净消费为 0.00 分。</p>}
      {operation.status === "processing" && <p>结算处理中。服务和账目数据恢复后 5 分钟内补齐。</p>}
      {operation.missing_usage > 0 && <p>有 {operation.missing_usage} 次调用缺失用量，未知部分由平台承担。</p>}
      {loading && <p role="status">加载调用明细…</p>}
      {error && <p role="alert">{error} <button className="config-button small" onClick={load}>重试</button></p>}
      {detail && <>
        <div className="billing-table-scroll"><table className="data-table">
          <thead><tr><th>模型</th><th>输入 Token</th><th>其中缓存</th><th>输出 Token</th><th>用量与计费</th><th>积分原值</th></tr></thead>
          <tbody>{detail.calls.map((call) => <tr key={call.call_id}>
            <td className="mono">{call.model}</td><td className="mono">{call.input_tokens ?? "未返回"}</td>
            <td className="mono">{call.cached_tokens ?? "未返回"}</td><td className="mono">{call.output_tokens ?? "未返回"}</td>
            <td>{!call.usage_known ? "用量缺失，不估扣" : !call.billable ? "故障重试，平台承担" : "正常创作计费"}</td>
            <td className="mono">{call.cost}</td>
          </tr>)}</tbody>
        </table></div>
        {!detail.calls.length && <p className="config-hint">本笔尚无模型调用记录。</p>}
        <p className="config-hint">调用积分保留计算原值，最终按整笔汇总后四舍五入到 0.01 分；实际扣费不超过确认预算。</p>
        <details className="billing-locked-prices"><summary>本笔操作采用的积分系数</summary>
          <div className="billing-table-scroll"><table className="data-table"><thead><tr><th>模型</th><th>正常输入</th><th>缓存输入</th><th>输出</th></tr></thead>
            <tbody>{Object.values(detail.prices).map((price) => <tr key={price.model}><td>{price.model}</td><td>{price.input_per_million}</td><td>{price.cached_per_million}</td><td>{price.output_per_million}</td></tr>)}</tbody></table></div>
          <p className="config-hint">单位：积分 / 百万 Token。已有操作不会因中途调整而改用新系数；系数仅管理员可见。</p>
        </details>
      </>}
    </div>
  </details>;
}

function fmtTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
