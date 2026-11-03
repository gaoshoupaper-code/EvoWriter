import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { fetchBillingOperation, type BillingOperation, type BillingOperationDetail } from "@/lib/api";
import { billingStatus, budgetError, useBillingStore } from "@/stores/billing";
import "./billing.css";

export const points = (value: number) => value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const date = (value: string) => new Date(value).toLocaleString("zh-CN");
const token = (value: number | null) => value === null ? "未提供" : value.toLocaleString("zh-CN");

export function BillingBudget({ phase }: { phase: string }) {
  const { account, budget, error, loading, selectPhase, setBudget, refresh } = useBillingStore();
  useEffect(() => { selectPhase(phase); }, [phase, selectPhase]);
  if (!account) return <div className="billing-budget" role="status">{error ? "无法获取积分账户。" : "正在获取本次预算…"}<button type="button" onClick={() => void refresh()} disabled={loading}>重试</button></div>;
  if (!account.enabled) return <div className="billing-budget">积分计费暂未启用，模型由平台提供。</div>;
  const validation = budgetError(budget, account.balance);
  return <div className="billing-budget">
    <label>本次最多扣 <input aria-label="本次最多使用积分" inputMode="decimal" type="number" min="0.01" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} /> 分</label>
    <span>可用 {points(account.balance)} 分{error ? "（未更新）" : ""} · 未用退回</span>
    {error ? <span className="billing-error" role="alert">账户未更新，请重试后发送。<button type="button" disabled={loading} onClick={() => void refresh()}>重试</button></span> : validation ? <span className="billing-error" role="status">{validation}</span> : null}
  </div>;
}

export function BillingBalanceLink() {
  const { account, error, loading, refresh } = useBillingStore();
  return <div className="billing-sidebar">
    <Link to="/settings" title="查看积分与账单">
      <span>可用积分</span><strong>{account ? points(account.balance) : "—"}</strong>
      <span>{account ? `预留 ${points(account.reserved)} 分` : loading ? "获取中" : "查看账单"}{error ? " · 未更新" : ""}</span>
    </Link>
    {error ? <button type="button" onClick={() => void refresh()} aria-label="重新获取积分">重试</button> : null}
  </div>;
}

function OperationDetail({ operation }: { operation: BillingOperation }) {
  const [detail, setDetail] = useState<BillingOperationDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  async function load() {
    setLoading(true);
    try {
      setDetail(await fetchBillingOperation(operation.operation_id));
      setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "无法获取明细");
    } finally { setLoading(false); }
  }
  useEffect(() => { if (open) void load(); }, [open, operation.status, operation.consumed, operation.missing_usage]);
  const outcome = detail?.status ?? operation.status;
  const result = detail?.result;
  const savedText = result && ["content", "markdown", "optimized", "question"].map((key) => result[key]).find((value) => typeof value === "string" && value);
  return <details className="billing-operation" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>
      <span>{date(operation.created_at)}<small>{billingStatus[outcome] ?? outcome}</small></span>
      <span>预算 {points(operation.budget)} 分<strong>{["reserved", "running", "processing"].includes(outcome) ? "尚未结算" : `实扣 ${points(detail?.charged ?? operation.charged)} 分`}</strong></span>
    </summary>
    <div className="billing-operation-content">
      <p className="billing-hint">操作 {operation.request_id}。明细按实际用量记录，最终按整笔汇总保留两位小数。</p>
      {loading ? <p role="status">明细获取中…</p> : error ? <p role="alert" className="billing-error">{error}<button type="button" onClick={() => void load()}>重试</button></p> : detail ? <>
        <div className="billing-table-wrap"><table><caption>模型调用明细</caption><thead><tr><th>模型</th><th>输入</th><th>缓存</th><th>输出</th><th>计费说明</th></tr></thead><tbody>{detail.calls.map((call, index) => <tr key={index}>
          <td>{call.model}</td><td>{token(call.input_tokens)}</td><td>{token(call.cached_tokens)}</td><td>{token(call.output_tokens)}</td>
          <td>{!call.usage_known ? "用量缺失，不估扣" : !call.billable ? "故障重试，平台承担" : "正常创作"}</td>
        </tr>)}</tbody></table></div>
        {detail.calls.length === 0 ? <p>尚无模型调用明细。</p> : null}
        {detail.missing_usage > 0 ? <p>有 {detail.missing_usage} 次调用缺失用量，未知部分由平台承担。</p> : null}
        {typeof savedText === "string" ? <details className="billing-saved-result"><summary>查看已保存结果</summary><pre>{savedText}</pre></details> : <p><Link to="/">返回工作区查看任务与已有内容</Link></p>}
      </> : null}
    </div>
  </details>;
}

export function BillingPanel() {
  const { account, error, loading, refresh } = useBillingStore();
  useEffect(() => {
    void refresh();
    const reconnect = () => { void refresh(); };
    window.addEventListener("online", reconnect);
    return () => window.removeEventListener("online", reconnect);
  }, [refresh]);
  async function copyGroup() {
    try { await navigator.clipboard.writeText("1134559830"); toast.success("QQ群号已复制"); }
    catch { toast.error("复制失败，请手动复制群号 1134559830"); }
  }
  const kinds: Record<string, string> = { admin_adjust: "管理员调整", invite_grant: "邀请码发放", creation_consume: "创作消费", creation_refund: "故障退分", migration_adjust: "旧负余额归零" };
  return <>
    <section className="settings-card billing-account">
      <header className="settings-card-head"><div className="settings-card-title"><h2>积分与账单</h2><p>统一使用平台模型，按实际用量消耗积分。积分永久有效。</p></div><button className="settings-btn" type="button" onClick={() => void refresh()} disabled={loading}>{loading ? "刷新中…" : "刷新账目"}</button></header>
      {error ? <p className="billing-error" role="alert">{error}。{account ? "以下是上次结果，尚未更新。" : "账户暂不可用。"}</p> : null}
      {account ? <>
        <div className="billing-balances"><div><span>可用积分</span><strong>{points(account.balance)}</strong></div><dl><div><dt>预留中</dt><dd>{points(account.reserved)} 分</dd></div><div><dt>总余额</dt><dd>{points(account.total_balance)} 分</dd></div></dl></div>
        <p className="billing-hint">预留不代表已消费。开工前确认预算，未用退回；系统故障全退，主动停止按已用量扣分。掉线后后台继续，不会自动追加预算。</p>
      </> : !error ? <p role="status">正在获取积分账户…</p> : null}
      <div className="billing-contact"><div><strong>补充积分</strong><p>加 QQ 群 <code>1134559830</code>，联系管理员发放。</p></div><button className="settings-btn" type="button" onClick={() => void copyGroup()}>复制群号</button></div>
    </section>
    <section className="settings-card"><header className="settings-card-head"><div className="settings-card-title"><h2>操作账单</h2><p>展开查看每笔预算、模型用量和扣退原因。</p></div></header>
      {account?.operations.length ? account.operations.map((operation) => <OperationDetail key={operation.operation_id} operation={operation} />) : <p className="billing-hint">{account ? "暂无操作账单。" : "账户恢复后显示账单。"}</p>}
    </section>
    <section className="settings-card"><header className="settings-card-head"><div className="settings-card-title"><h2>积分流水</h2><p>记录发放、调整和最终结算。历史流水保留。</p></div></header>
      {account?.transactions.length ? <div className="billing-table-wrap"><table><thead><tr><th>时间</th><th>变动</th><th>积分</th><th>变动后总余额</th><th>原因</th></tr></thead><tbody>{account.transactions.map((entry) => <tr key={entry.tx_id}><td>{date(entry.created_at)}</td><td>{kinds[entry.type] ?? entry.type}{entry.legacy ? " · 旧账" : ""}</td><td>{entry.amount > 0 ? "+" : ""}{points(entry.amount)}</td><td>{points(entry.balance_after)}</td><td>{entry.note}</td></tr>)}</tbody></table></div> : <p className="billing-hint">{account ? "暂无积分变动。" : "账户恢复后显示流水。"}</p>}
    </section>
  </>;
}
