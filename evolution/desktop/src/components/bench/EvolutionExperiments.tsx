import "@/components/bench/experiment-observation.css";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Play, RefreshCw, SlidersHorizontal } from "lucide-react";
import {
  getExperimentOptions, listExperimentPackages, listExperimentRules, startExperiment,
  type ExperimentOptions, type ExperimentPackage, type ExperimentRule, type ExperimentStart,
} from "@/lib/api";
import {
  BUILTIN_SUITE, RULE_PROFILE, EXPERIMENT_STATUS, TASK_LABELS, TASK_TYPES,
  experimentTaskLabel, type ExperimentTaskType,
} from "@/lib/experiment-types";
import { ExperimentProgress } from "./ExperimentEvidence";
import { useExperiments } from "@/hooks/useExperiments";
import "./experiments.css";

export function EvolutionExperiments() {
  const navigate = useNavigate();
  const [options, setOptions] = useState<ExperimentOptions | null>(null);
  const [packages, setPackages] = useState<ExperimentPackage[]>([]);
  const [rules, setRules] = useState<ExperimentRule[]>([]);
  const { runs, loading: listLoading, error: listError } = useExperiments(true);
  const activeRuns = runs.filter((run) => ["pending", "running", "recovering", "stopping", "publishing"].includes(run.status));
  const [kind, setKind] = useState<ExperimentTaskType>("volume_outlines");
  const [baseline, setBaseline] = useState("");
  const [model, setModel] = useState("");
  const [judges, setJudges] = useState("");
  const [ruleIds, setRuleIds] = useState<Partial<Record<ExperimentTaskType, string>>>({});
  const [completion, setCompletion] = useState("100");
  const [gain, setGain] = useState("0.1");
  const [tolerances, setTolerances] = useState<Partial<Record<ExperimentTaskType, string>>>({});
  const [rounds, setRounds] = useState("3");
  const [advanced, setAdvanced] = useState(false);
  const [customData, setCustomData] = useState(false);
  const [chosenIds, setChosenIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const others = TASK_TYPES.filter((type) => type !== kind);
  const selectedGroup = options?.judge_groups.find((group) => String(group.id) === judges);
  const suite = options?.builtin_suite?.id ?? BUILTIN_SUITE;
  const samples = packages.filter((item) => item.suite === suite);
  const compatibleSamples = packages.filter((item) => item.task_type === "volume_outlines"
    ? item.target.volume_ids.join(",") === "1,2,3" && item.target.chapters_per_volume === 20
    : item.target.chapter != null);
  const selectedSamples = customData ? compatibleSamples.filter((item) => chosenIds.has(item.id)) : samples;
  const selectedIds = (type: ExperimentTaskType, purpose: ExperimentPackage["purpose"]) =>
    selectedSamples.filter((item) => item.task_type === type && item.purpose === purpose).map((item) => item.id);
  const optimization = selectedIds(kind, "optimization");
  const validation = selectedIds(kind, "validation");
  const missing = !loading ? [
    ...((customData ? !optimization.length : optimization.length !== 2) ? ["主攻优化题" + (customData ? "至少选择 1 条" : "需 2 条，当前 " + optimization.length + " 条")] : []),
    ...((customData ? !validation.length : validation.length !== 1) ? ["独立终验题" + (customData ? "至少选择 1 条" : "需 1 条，当前 " + validation.length + " 条")] : []),
    ...others.filter((type) => customData ? !selectedIds(type, "regression").length : selectedIds(type, "regression").length !== 1).map((type) => TASK_LABELS[type] + "缺少回归题"),
    ...TASK_TYPES.filter((type) => !ruleIds[type]).map((type) => TASK_LABELS[type] + "缺少对应的新规则"),
    ...(!options?.versions.length ? ["没有可用的 Harness 版本"] : []),
    ...(!options?.models.some((item) => item.has_key !== false) ? ["没有可用的执行模型"] : []),
    ...(!options?.judge_groups.some((group) => group.members.length && group.members.every((member) => member.has_key !== false && !member.stale)) ? ["没有已配置的评测模型"] : []),
  ] : [];

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(""); setOptions(null);
    Promise.all([getExperimentOptions(), listExperimentPackages(), listExperimentRules()]).then(([configuration, data, scoring]) => {
      if (!alive) return;
      setOptions(configuration); setPackages(data.items); setRules(scoring.items);
      setCustomData(false); setChosenIds(new Set(data.items.filter((item) => item.suite === (configuration.builtin_suite?.id ?? BUILTIN_SUITE)).map((item) => item.id)));
      setBaseline(configuration.production_version == null ? "" : String(configuration.production_version));
      const activeModel = configuration.models.find((item) => item.is_active && item.has_key !== false);
      setModel(activeModel ? String(activeModel.id) : "");
      setJudges((current) => configuration.judge_groups.some((group) => String(group.id) === current) ? current : "");
      const latest: Partial<Record<ExperimentTaskType, string>> = {};
      for (const type of TASK_TYPES) {
        const candidates = scoring.items.filter((rule) => rule.task_type === type && rule.profile === RULE_PROFILE).sort((a, b) => b.version - a.version);
        const preferred = configuration.defaults?.rule_versions[type];
        latest[type] = candidates.find((rule) => rule.id === preferred)?.id ?? candidates[0]?.id;
      }
      setRuleIds(latest);
      setCompletion(String((configuration.defaults?.thresholds.completion_rate ?? 1) * 100));
      setGain(String(configuration.defaults?.thresholds.quality_gain ?? 0.1));
      setRounds(String(configuration.defaults?.max_rounds ?? 3));
      setTolerances(Object.fromEntries(TASK_TYPES.map((type) => [type, String(configuration.defaults?.thresholds.regression_tolerance[type] ?? 0)])));
    }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : "读取实验设置失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload]);

  function samplePicker(title: string, type: ExperimentTaskType, purpose: ExperimentPackage["purpose"]) {
    const items = compatibleSamples.filter((item) => item.task_type === type && item.purpose === purpose);
    return <fieldset className="experiment-picker" disabled={starting}><legend>{title}</legend>
      {items.length ? items.map((item) => <label key={item.id}><input type="checkbox" aria-label={item.title} checked={chosenIds.has(item.id)} onChange={() => {
        setChosenIds((current) => { const next = new Set(current); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next; });
      }} /><span>{item.title}</span><small>{item.suite === suite ? "内置" : "自备"}</small></label>) :
        <p className="experiment-note">没有适用样本。请到<Link to="/bench/dataset">评测数据集</Link>导入。</p>}
    </fieldset>;
  }

  async function start(event: React.FormEvent) {
    event.preventDefault(); if (starting) return;
    setError("");
    if (missing.length) { setError(missing.join("；")); return; }
    if (!baseline || !model || !judges) { setError("请选择 Harness、执行模型和评测模型。"); return; }
    const numeric = [completion, gain, rounds, ...others.map((type) => tolerances[type] ?? "")];
    if (numeric.some((value) => !value.trim() || !Number.isFinite(Number(value))) || Number(completion) < 0 || Number(completion) > 100 ||
      Number(gain) < 0 || !Number.isSafeInteger(Number(rounds)) || Number(rounds) < 1 || others.some((type) => Number(tolerances[type]) < 0)) {
      setError("请填写有效门槛；完成率为 0–100%，最大轮数为正整数。"); return;
    }
    const payload: ExperimentStart = {
      task_type: kind, baseline_version: Number(baseline), model_config_id: Number(model), judge_group_id: Number(judges),
      rule_versions: ruleIds as Record<ExperimentTaskType, string>, optimization_ids: optimization, validation_ids: validation,
      regression_ids: Object.fromEntries(others.map((type) => [type, selectedIds(type, "regression")])),
      thresholds: { completion_rate: Number(completion) / 100, quality_gain: Number(gain),
        regression_tolerance: Object.fromEntries(others.map((type) => [type, Number(tolerances[type])])) },
      max_rounds: Number(rounds),
    };
    setStarting(true);
    try { const result = await startExperiment(payload); navigate("/bench/experiments/" + encodeURIComponent(result.id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "启动实验失败"); }
    finally { setStarting(false); }
  }

  return <section className="experiment-section experiment-console" aria-labelledby="evolution-experiments-title">
    <div className="experiment-heading"><div><h2 id="evolution-experiments-title">启动自动进化</h2>
      <p className="experiment-note">选好目标与模型，自动完成评测、改版和复测。独立终验通过后，由你决定发布。</p></div>
      <button className="config-button" type="button" disabled={loading || starting} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} />刷新配置</button>
    </div>
    {error && <p role="alert" className="experiment-error">{error}</p>}
    {loading ? <p role="status">正在加载实验设置…</p> : options && <form className="experiment-form" onSubmit={(event) => void start(event)}>
      <fieldset disabled={starting} className="experiment-form-main">
        <legend className="sr-only">实验常用选择</legend>
        <div className="experiment-launch-grid">
          <label>主攻阶段<select className="config-input" value={kind} onChange={(event) => { setKind(event.target.value as ExperimentTaskType); setCustomData(false); }}>
            {TASK_TYPES.map((type) => <option key={type} value={type}>{TASK_LABELS[type]}</option>)}</select></label>
          <label>Harness 版本<select className="config-input" required value={baseline} onChange={(event) => setBaseline(event.target.value)}>
            <option value="">选择版本</option>{options.versions.map((version) => <option key={version.version} value={version.version}>
              v{version.version}{version.version === options.production_version ? " · 当前生产" : ""}{version.note ? " · " + version.note : ""}
            </option>)}</select></label>
          <label>执行模型<select className="config-input" required value={model} onChange={(event) => setModel(event.target.value)}>
            <option value="">选择执行模型</option>{options.models.map((item) => <option key={item.id} value={item.id} disabled={item.has_key === false}>
              {item.name} · {item.model}{item.has_key === false ? " · 缺少凭据" : ""}
            </option>)}</select></label>
          <label>评测模型<select className="config-input" required value={judges} onChange={(event) => setJudges(event.target.value)}>
            <option value="">选择异源评测模型</option>{options.judge_groups.map((group) => <option key={group.id} value={group.id}
              disabled={!group.members.length || group.members.some((member) => member.has_key === false || member.stale)}>
              {group.name} · {group.members.map((member) => member.model).join(" + ") || "未配置模型"}
            </option>)}</select></label>
        </div>
      </fieldset>
      {!!missing.length && <div role="alert" className="experiment-readiness"><strong>实验资料尚未就绪</strong>
        <ul>{missing.map((message) => <li key={message}>{message}</li>)}</ul>
        <Link to="/bench/dataset" className="action-link">查看试验数据集</Link>
      </div>}
      {!options.judge_groups.length && <p className="experiment-error">暂无评测模型，请先在模型配置中设置异源评委组。</p>}
      <div className="experiment-launch-summary" aria-label="启动前配置摘要">
        <div><span>本次数据</span><strong>{TASK_LABELS[kind]} · {optimization.length} 条优化 / {validation.length} 条独立终验</strong>
          <small>{others.map((type) => TASK_LABELS[type] + "回归 " + selectedIds(type, "regression").length + " 条").join(" · ")}</small></div>
        <div><span>过关门槛</span><strong>完成率 ≥ {completion}% · 主攻涨幅 ≥ {gain} / 5</strong>
          <small>其他阶段容忍 {others.map((type) => tolerances[type] ?? "0").join(" / ")} 分退化 · 最多 {rounds} 轮</small></div>
        <div><span>评测模型</span><strong>{selectedGroup?.members.map((member) => member.model).join(" + ") || "等待选择"}</strong>
          <small>规则、输入与模型在启动后固定</small></div>
      </div>
      <details className="experiment-advanced" open={advanced}>
        <summary aria-expanded={advanced} onClick={(event) => { event.preventDefault(); setAdvanced((current) => !current); }}><SlidersHorizontal size={15} />高级设置</summary>
        {advanced && <>
        <fieldset className="experiment-form-main" disabled={starting}><legend className="sr-only">固定门槛与规则</legend>
          <div className="experiment-fields">{TASK_TYPES.map((type) => <label key={type}>{TASK_LABELS[type]}规则版本
            <select className="config-input" required value={ruleIds[type] ?? ""} onChange={(event) => setRuleIds({ ...ruleIds, [type]: event.target.value })}>
              <option value="">选择规则</option>{rules.filter((rule) => rule.task_type === type && rule.profile === RULE_PROFILE).sort((a, b) => b.version - a.version).map((rule) =>
                <option key={rule.id} value={rule.id}>v{rule.version} · {rule.name}</option>)}</select></label>)}</div>
          <div className="experiment-fields"><label>完成率下限（%）<input className="config-input" type="number" required min={0} max={100} step="any" value={completion} onChange={(event) => setCompletion(event.target.value)} /></label>
            <label>主攻最低涨幅<input className="config-input" type="number" required min={0} step="any" value={gain} onChange={(event) => setGain(event.target.value)} /></label>
            {others.map((type) => <label key={type}>{TASK_LABELS[type]}退化容忍<input className="config-input" type="number" required min={0} step="any" value={tolerances[type] ?? ""} onChange={(event) => setTolerances({ ...tolerances, [type]: event.target.value })} /></label>)}
            <label>最大迭代轮数<input className="config-input" type="number" required min={1} step={1} value={rounds} onChange={(event) => setRounds(event.target.value)} /></label>
          </div>
        </fieldset>
        <div className="experiment-sample-summary">{[
          ...selectedSamples.filter((item) => item.task_type === kind && item.purpose !== "regression"),
          ...selectedSamples.filter((item) => others.includes(item.task_type) && item.purpose === "regression"),
        ].map((item) => <div key={item.id}><span>{item.purpose === "optimization" ? "优化" : item.purpose === "validation" ? "独立终验" : TASK_LABELS[item.task_type] + "回归"}</span><strong>{item.title}</strong></div>)}</div>
        <label className="experiment-custom-data"><input type="checkbox" checked={customData} disabled={starting} onChange={(event) => setCustomData(event.target.checked)} />自选样本</label>
        {customData && <div>{samplePicker("主攻优化题", kind, "optimization")}{samplePicker("主攻独立终验题", kind, "validation")}
          {others.map((type) => <div key={type}>{samplePicker(TASK_LABELS[type] + "回归题", type, "regression")}</div>)}</div>}
        <p className="experiment-note">优化每题一次；回归与终验由新旧版本各运行三次。独立终验内容不提供给进化 Agent。</p>
        </>}
      </details>
      <div className="experiment-launch-footer"><button className="config-button primary" type="submit" disabled={starting || missing.length > 0}>
        <Play size={15} />{starting ? "正在启动…" : "启动自进化实验"}</button><span className="experiment-note">自动命名，启动后即可查看实时进度。</span></div>
    </form>}
    <div className="experiment-heading experiment-records-heading"><div><h2>正在运行的实验</h2>
      <p className="experiment-note">每 5 秒更新。点击实验查看运行过程、评分和文稿。</p></div>
      <Link className="action-link" to="/bench/experiments">全部实验记录</Link></div>
    {listError && <p role="alert" className="experiment-error">更新失败：{listError}</p>}
    {listLoading ? <p role="status">正在读取运行概况…</p> : activeRuns.length ? <div className="experiment-active-list">
      {activeRuns.map((run) => <article className="experiment-active-run" key={run.id}>
        <div><span className="experiment-type-label">{experimentTaskLabel(run.task_type, run.flow_version)}</span>
          <Link className="experiment-run-name" to={"/bench/experiments/" + encodeURIComponent(run.id)}>{run.name}</Link>
          <span className={"experiment-state " + run.status}>{EXPERIMENT_STATUS[run.status] ?? run.status}</span></div>
        <ExperimentProgress experiment={run} compact />
      </article>)}
    </div> : !listError && <p className="experiment-empty">当前没有正在运行的实验。历史运行保存在实验记录中。</p>}
  </section>;
}
