import { useCallback, useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import {
  fetchBillingConfig, saveBillingConfig, saveBillingPrice,
  type BillingConfig, type BillingPrice,
} from "@/lib/api";
import { parseCreditAmount } from "@/lib/billing";

const BUDGET_LABELS: Record<string, string> = {
  default: "通用操作", generation: "创作生成", character: "人物生成", style: "文风优化",
  theme: "主题", outline: "总纲", volume: "卷纲", text: "正文",
};

export default function AdminCreditsSettings() {
  const [config, setConfig] = useState<BillingConfig | null>(null);
  const [budgets, setBudgets] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchBillingConfig();
      setConfig(data);
      setEnabled(data.enabled);
      setBudgets(Object.keys(data.default_budgets).length
        ? Object.fromEntries(Object.entries(data.default_budgets).map(([key, value]) => [key, String(value)]))
        : { default: "" });
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取积分设置失败");
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function saveConfig(event: FormEvent) {
    event.preventDefault();
    const values = Object.fromEntries(Object.entries(budgets).map(([key, value]) => [key, parseCreditAmount(value)]));
    if (Object.values(values).some((value) => !Number.isFinite(value) || value <= 0)) {
      toast.error("默认预算必须大于零，最多保留两位小数");
      return;
    }
    if (enabled && !config?.prices.length) {
      toast.error("请先配置当前模型的积分系数");
      return;
    }
    setSaving(true);
    try {
      await saveBillingConfig(enabled, values);
      setConfig((current) => current ? { ...current, enabled, default_budgets: values } : current);
      toast.success("计费状态和默认预算已保存");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  function priceSaved(price: BillingPrice) {
    setConfig((current) => current ? { ...current, prices: [...current.prices.filter((row) => row.model !== price.model), price] } : current);
    setAdding(false);
  }

  return (
    <div className="admin-page billing-admin">
      <header className="page-header">
        <h1>积分设置</h1>
        <p className="page-desc">按模型用量和后台积分系数结算，整笔操作保留两位小数。积分系数仅管理员可见。</p>
      </header>
      {error && <div role="alert" className="config-test-result fail">设置未更新：{error} <button className="config-button small" onClick={refresh}>重试</button></div>}
      {!config && !error && <div className="page-loading">加载积分设置…</div>}
      {config && <>
        <form className="billing-admin-section" onSubmit={saveConfig}>
          <div className="admin-section-head">
            <h2>计费状态与默认预算</h2>
            <span className="text-mute">当前：{config.enabled ? "已启用计费" : "计费未启用"}</span>
          </div>
          <label className="billing-checkbox">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} disabled={saving} />
            对新接受的操作启用积分计费
          </label>
          <p className="config-hint">关闭计费后，已有操作仍按接受时的规则结算。启用前请确认当前模型已配置积分系数，默认预算已有历史用量依据。</p>
          <div className="billing-budget-fields">
            {Object.entries(budgets).map(([key, value]) => <label className="config-field" key={key}>
              <span className="config-label">{BUDGET_LABELS[key] ?? key}默认预算（积分）</span>
              <input className="config-input" type="number" min="0.01" step="0.01" value={value} required disabled={saving}
                onChange={(event) => setBudgets((current) => ({ ...current, [key]: event.target.value }))} />
            </label>)}
          </div>
          <p className="config-hint">用户可在开工前修改预算。预算是最多扣分，不会自动追加；未用预留退回。</p>
          <button type="submit" className="config-button primary" disabled={saving}>{saving ? "保存中…" : "保存计费设置"}</button>
        </form>

        <section className="billing-admin-section">
          <div className="admin-section-head">
            <div><h2>模型积分系数</h2><p className="config-hint">单位：积分 / 百万 Token。新系数只影响之后接受的操作，已有操作保持原系数。</p></div>
            <button className="config-button secondary" type="button" disabled={adding} onClick={() => setAdding(true)}>添加模型系数</button>
          </div>
          {!config.prices.length && <p className="config-empty">还没有模型积分系数。未配置系数的模型无法启动新的计费操作。</p>}
          {config.prices.map((price) => <PriceForm key={price.model + ":" + price.updated_at} price={price} onSaved={priceSaved} />)}
          {adding && <PriceForm onSaved={priceSaved} onCancel={() => setAdding(false)} />}
        </section>
        <aside className="billing-admin-note">
          <strong>固定规则</strong>
          <p>积分永久有效，不允许欠费。系统故障整笔退分，故障重试由平台承担；主动停止和预算用尽按已用量结算。</p>
          <p>缺失用量不估扣。平台测试、评估与进化不扣用户积分；管理员普通创作照常计费。</p>
        </aside>
      </>}
    </div>
  );
}

function PriceForm({ price, onSaved, onCancel }: { price?: BillingPrice; onSaved: (price: BillingPrice) => void; onCancel?: () => void }) {
  const [model, setModel] = useState(price?.model ?? "");
  const [input, setInput] = useState(price?.input_per_million ?? "");
  const [output, setOutput] = useState(price?.output_per_million ?? "");
  const [cached, setCached] = useState(price?.cached_per_million ?? "");
  const [saving, setSaving] = useState(false);
  const label = price?.model ?? "新模型";
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!model.trim() || [input, output, cached].some((value) => !value.trim() || !Number.isFinite(Number(value)) || Number(value) < 0)) {
      toast.error("请填写模型名称和非负积分系数；未填写不等于零");
      return;
    }
    setSaving(true);
    try {
      const saved = await saveBillingPrice(model.trim(), { input_per_million: input, output_per_million: output, cached_per_million: cached });
      onSaved(saved);
      toast.success("模型积分系数已保存，仅新操作使用新系数");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "模型积分系数保存失败");
    } finally {
      setSaving(false);
    }
  }
  return <form className="billing-price-form" onSubmit={save}>
    {price ? <div className="billing-price-title"><strong>{price.model}</strong><span className="text-mute">更新于 {new Date(price.updated_at).toLocaleString("zh-CN", { hour12: false })}</span></div> :
      <label className="config-field"><span className="config-label">模型名称</span><input className="config-input" value={model} onChange={(event) => setModel(event.target.value)} required maxLength={200} disabled={saving} /></label>}
    <div className="billing-price-fields">
      {[
        { title: "正常输入系数", value: input, change: setInput },
        { title: "缓存输入系数", value: cached, change: setCached },
        { title: "输出系数", value: output, change: setOutput },
      ].map((field) => <label className="config-field" key={field.title}>
        <span className="config-label">{field.title}</span>
        <input className="config-input mono" aria-label={label + " · " + field.title} type="number" min="0" step="any" required value={field.value}
          onChange={(event) => field.change(event.target.value)} disabled={saving} />
      </label>)}
      <button className="config-button secondary" type="submit" disabled={saving}>{saving ? "保存中…" : "保存模型系数"}</button>
      {onCancel && <button className="config-button ghost" type="button" onClick={onCancel} disabled={saving}>取消</button>}
    </div>
  </form>;
}
