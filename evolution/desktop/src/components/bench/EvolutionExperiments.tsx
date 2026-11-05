import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  getExperimentOptions, listExperimentPackages, listExperimentRules, listExperiments, startExperiment,
  type ExperimentOptions, type ExperimentPackage, type ExperimentRule, type ExperimentStart, type ExperimentSummary,
} from "@/lib/api";
import { EXPERIMENT_STATUS, PHASE_LABELS, TASK_LABELS, TASK_TYPES, type ExperimentTaskType } from "@/lib/experiment-types";
import "./experiments.css";

export function EvolutionExperiments() {
  const navigate = useNavigate();
  const [options, setOptions] = useState<ExperimentOptions | null>(null);
  const [packages, setPackages] = useState<ExperimentPackage[]>([]);
  const [rules, setRules] = useState<ExperimentRule[]>([]);
  const [runs, setRuns] = useState<ExperimentSummary[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ExperimentTaskType>("volume_outlines");
  const [baseline, setBaseline] = useState("");
  const [model, setModel] = useState("");
  const [judges, setJudges] = useState("");
  const [ruleIds, setRuleIds] = useState<Partial<Record<ExperimentTaskType, string>>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [completion, setCompletion] = useState("");
  const [gain, setGain] = useState("");
  const [tolerances, setTolerances] = useState<Partial<Record<ExperimentTaskType, string>>>({});
  const [rounds, setRounds] = useState("");
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [reload, setReload] = useState(0);
  const others = TASK_TYPES.filter((type) => type !== kind);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    Promise.all([getExperimentOptions(), listExperimentPackages(), listExperimentRules()]).then(([configuration, data, scoring]) => {
      if (!alive) return;
      setOptions(configuration); setPackages(data.items); setRules(scoring.items);
      setBaseline(configuration.production_version == null ? "" : String(configuration.production_version));
      const activeModel = configuration.models.find((item) => item.is_active);
      setModel(activeModel ? String(activeModel.id) : "");
      const latest: Partial<Record<ExperimentTaskType, string>> = {};
      for (const type of TASK_TYPES) latest[type] = scoring.items.filter((rule) => rule.task_type === type).sort((a, b) => b.version - a.version)[0]?.id;
      setRuleIds(latest);
    }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : "读取实验设置失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await listExperiments();
        if (alive) { setRuns(result.items); setListError(""); }
      } catch (cause) { if (alive) setListError(cause instanceof Error ? cause.message : "读取实验列表失败"); }
      if (alive) timer = setTimeout(refresh, 5000);
    };
    void refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [reload]);

  function toggle(id: string) {
    setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }
  function picker(title: string, type: ExperimentTaskType, purpose: ExperimentPackage["purpose"]) {
    const items = packages.filter((item) => item.task_type === type && item.purpose === purpose);
    return <fieldset className="experiment-picker" disabled={starting}><legend>{title}</legend>
      {items.length ? items.map((item) => <label key={item.id}><input type="checkbox" checked={selected.has(item.id)} onChange={() => toggle(item.id)} aria-label={item.title} />
        <span>{item.title}</span><small className="mono" title={item.input_revision}>{item.input_revision.slice(0, 12)}</small></label>) :
        <p className="experiment-note">没有适用样本。请到<Link to="/bench/dataset">评测数据集</Link>导入。</p>}
    </fieldset>;
  }
  async function start(event: React.FormEvent) {
    event.preventDefault(); if (starting) return;
    setError("");
    const selectedIds = (type: ExperimentTaskType, purpose: ExperimentPackage["purpose"]) => packages
      .filter((item) => item.task_type === type && item.purpose === purpose && selected.has(item.id)).map((item) => item.id);
    const optimization = selectedIds(kind, "optimization"), validation = selectedIds(kind, "validation");
    if (!optimization.length || !validation.length || others.some((type) => !selectedIds(type, "regression").length)) {
      setError("请选择主攻优化题、独立终验题，以及另外两类回归题。"); return;
    }
    if (!name.trim() || !baseline || !model || !judges || TASK_TYPES.some((type) => !ruleIds[type])) {
      setError("请完整填写实验名称、原版基线、创作模型、异源评委及三类规则。"); return;
    }
    const numeric = [completion, gain, rounds, ...others.map((type) => tolerances[type] ?? "")];
    if (numeric.some((value) => !value.trim() || !Number.isFinite(Number(value))) || Number(completion) < 0 || Number(completion) > 100 || Number(gain) < 0 ||
      !Number.isSafeInteger(Number(rounds)) || Number(rounds) < 1 || others.some((type) => Number(tolerances[type]) < 0)) {
      setError("请填写有效门槛；完成率为 0–100%，最大轮数为正整数。"); return;
    }
    const payload: ExperimentStart = {
      name: name.trim(), task_type: kind, baseline_version: Number(baseline), model_config_id: Number(model), judge_group_id: Number(judges),
      rule_versions: ruleIds as Record<ExperimentTaskType, string>, optimization_ids: optimization, validation_ids: validation,
      regression_ids: Object.fromEntries(others.map((type) => [type, selectedIds(type, "regression")])),
      thresholds: { completion_rate: Number(completion) / 100, quality_gain: Number(gain),
        regression_tolerance: Object.fromEntries(others.map((type) => [type, Number(tolerances[type])])) },
      max_rounds: Number(rounds),
    };
    setStarting(true);
    try { const result = await startExperiment(payload); navigate(`/bench/experiments/${encodeURIComponent(result.id)}`); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "启动实验失败"); }
    finally { setStarting(false); }
  }
  return <section className="experiment-section" aria-labelledby="evolution-experiments-title">
    <div className="experiment-heading"><div><h2 id="evolution-experiments-title">自进化实验</h2><p className="experiment-note">每次主攻一类。探索每例一次，终验新旧版各三次；通过后由人决定发布。</p></div>
      <button className="config-button" type="button" disabled={loading || starting} onClick={() => setReload((value) => value + 1)}>刷新配置</button></div>
    {error && <p role="alert" className="experiment-error">{error}</p>}
    {loading ? <p role="status">正在加载实验配置…</p> : options && <form className="experiment-form" onSubmit={(event) => void start(event)}>
      <fieldset disabled={starting} className="experiment-form-main"><legend className="sr-only">固定实验条件</legend>
        <div className="experiment-fields"><label>实验名称<input className="config-input" required value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>主攻类型<select className="config-input" value={kind} onChange={(event) => { setKind(event.target.value as ExperimentTaskType); setSelected(new Set()); }}>
            {TASK_TYPES.map((type) => <option key={type} value={type}>{TASK_LABELS[type]}</option>)}</select></label>
          <label>原版基线<select className="config-input" required value={baseline} onChange={(event) => setBaseline(event.target.value)}><option value="">选择版本</option>
            {options.versions.map((version) => <option key={version.version} value={version.version}>v{version.version}{version.version === options.production_version ? " · 当前生产" : ""} · {version.note}</option>)}</select></label>
          <label>创作模型<select className="config-input" required value={model} onChange={(event) => setModel(event.target.value)}><option value="">选择模型</option>
            {options.models.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model}</option>)}</select></label>
          <label>异源评委组<select className="config-input" required value={judges} onChange={(event) => setJudges(event.target.value)}><option value="">选择评委组</option>
            {options.judge_groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label></div>
        {!options.judge_groups.length && <p className="experiment-error">尚无评委组，请联系管理员配置异源评委。</p>}
        <div className="experiment-fields">{TASK_TYPES.map((type) => <label key={type}>{TASK_LABELS[type]}规则版本<select className="config-input" required value={ruleIds[type] ?? ""} onChange={(event) => setRuleIds({ ...ruleIds, [type]: event.target.value })}>
          <option value="">选择规则</option>{rules.filter((rule) => rule.task_type === type).sort((a, b) => b.version - a.version).map((rule) => <option key={rule.id} value={rule.id}>v{rule.version} · {rule.name}</option>)}</select></label>)}</div>
        <div className="experiment-fields"><label>完成率下限（%）<input className="config-input" type="number" required min={0} max={100} step="any" value={completion} onChange={(event) => setCompletion(event.target.value)} /></label>
          <label>主攻最低涨幅<input className="config-input" type="number" required min={0} step="any" value={gain} onChange={(event) => setGain(event.target.value)} /></label>
          {others.map((type) => <label key={type}>{TASK_LABELS[type]}退化容忍<input className="config-input" type="number" required min={0} step="any" value={tolerances[type] ?? ""} onChange={(event) => setTolerances({ ...tolerances, [type]: event.target.value })} /></label>)}
          <label>最大迭代轮数<input className="config-input" type="number" required min={1} step={1} value={rounds} onChange={(event) => setRounds(event.target.value)} /></label></div>
      </fieldset>
      <details open className="experiment-selection"><summary>本次使用的固定样本</summary>
        {picker("主攻优化题", kind, "optimization")}{picker("主攻独立终验题", kind, "validation")}
        {others.map((type) => <div key={type}>{picker(`${TASK_LABELS[type]}回归题`, type, "regression")}</div>)}
      </details>
      <p className="experiment-note">启动后固定输入、模型、规则与门槛。终验题及逐题证据不向进化 Agent 开放。</p>
      <button className="config-button primary" type="submit" disabled={starting}>{starting ? "正在启动…" : "启动自进化实验"}</button>
    </form>}
    <h3>实验记录</h3>{listError && <p role="alert" className="experiment-error">{listError}</p>}
    {!runs.length ? <p className="experiment-note">暂无实验记录。</p> : <div className="experiment-table-scroll"><table className="data-table"><thead><tr><th>实验</th><th>主攻</th><th>状态</th><th>阶段</th><th>轮次</th><th>原版</th><th>更新时间</th></tr></thead><tbody>
      {runs.map((run) => <tr key={run.id}><td><Link className="action-link" to={`/bench/experiments/${encodeURIComponent(run.id)}`}>{run.name}</Link></td><td>{TASK_LABELS[run.task_type]}</td>
        <td>{EXPERIMENT_STATUS[run.status] ?? run.status}</td><td>{PHASE_LABELS[run.phase] ?? run.phase}</td><td>{run.current_round} / {run.max_rounds}</td><td>v{run.baseline.version}</td>
        <td>{run.updated_at.slice(0, 19).replace("T", " ")}</td></tr>)}
    </tbody></table></div>}
  </section>;
}
