import { currentTask } from "@/lib/writing-agent";
import "@/components/bench/experiment-observation.css";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Play, RefreshCw, SlidersHorizontal } from "lucide-react";
import {
  getExperimentOptions, listExperimentPackages, listExperimentRules, startExperiment,
  type ExperimentOptions, type ExperimentPackage, type ExperimentRule, type ExperimentStart,
} from "@/lib/api";
import {
  RULE_PROFILE, ruleProfile, EXPERIMENT_STATUS, TASK_LABELS,
  experimentTaskLabel, type ExperimentTaskType,
} from "@/lib/experiment-types";
import { ExperimentProgress } from "./ExperimentEvidence";
import { useExperiments } from "@/hooks/useExperiments";
import "./experiments.css";

function launchRules(rules: ExperimentRule[], kind: ExperimentTaskType) {
  const sameKind = rules.filter((rule) => rule.task_type === kind).sort((a, b) => b.version - a.version);
  const current = sameKind.filter((rule) => rule.profile === ruleProfile(kind));
  return current.length ? current : sameKind.filter((rule) => rule.profile === RULE_PROFILE);
}

export function EvolutionExperiments() {
  const navigate = useNavigate();
  const [options, setOptions] = useState<ExperimentOptions | null>(null);
  const [packages, setPackages] = useState<ExperimentPackage[]>([]);
  const [rules, setRules] = useState<ExperimentRule[]>([]);
  const { runs, loading: listLoading, error: listError } = useExperiments(true);
  const activeRuns = runs.filter((run) => run.flow_version === 3 && run.task_type === currentTask() && ["pending", "running", "recovering", "stopping", "publishing"].includes(run.status));
  const kind = currentTask();
  const [params] = useSearchParams();
  const candidate = kind === "chapter_text" ? null : params.get("candidate");
  const [baseline, setBaseline] = useState("");
  const [model, setModel] = useState("");
  const [judges, setJudges] = useState("");
  const [ruleIds, setRuleIds] = useState<Partial<Record<ExperimentTaskType, string>>>({});
  const [rounds, setRounds] = useState("3");
  const [advanced, setAdvanced] = useState(false);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  const selectedGroup = options?.judge_groups.find((group) => String(group.id) === judges);
  const selectedSamples = packages.filter((item) => item.task_type === kind);
  const selectedIds = (type: ExperimentTaskType, purpose: ExperimentPackage["purpose"]) =>
    selectedSamples.filter((item) => item.task_type === type && item.purpose === purpose).map((item) => item.id);
  const optimization = selectedIds(kind, "optimization");
  const regression = selectedIds(kind, "regression");
  const missing = !loading ? [
    ...(!optimization.length ? ["请选择本套改进题"] : []),
    ...(!regression.length ? ["请选择本套回归题"] : []),
    ...(!ruleIds[kind] ? ["请选择本套评分规则"] : []),
    ...(candidate && !options?.versions.some((v) => v.commit === params.get("base") && v.version === Number(baseline)) ? ["候选与所选基线不匹配"] : []),
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
      setBaseline(configuration.production_version == null ? "" : String(configuration.production_version));
      const activeModel = configuration.models.find((item) => item.is_active && item.has_key !== false);
      setModel(activeModel ? String(activeModel.id) : "");
      setJudges((current) => configuration.judge_groups.some((group) => String(group.id) === current) ? current : "");
      const candidates = launchRules(scoring.items, kind);
      const preferred = configuration.defaults?.rule_versions[kind];
      setRuleIds({[kind]: candidates.find((rule) => rule.id === preferred)?.id ?? candidates[0]?.id});
      setRounds(String(configuration.defaults?.max_rounds ?? 3));
    }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : "读取实验设置失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload, kind]);

  async function start(event: React.FormEvent) {
    event.preventDefault(); if (starting) return;
    setError("");
    if (missing.length) { setError(missing.join("；")); return; }
    if (!baseline || !model || !judges) { setError("请选择 Harness、执行模型和评测模型。"); return; }
    if (!Number.isInteger(Number(rounds)) || Number(rounds) < 1 || Number(rounds) > 3) {
      setError("同一次实验可运行一至三轮。"); return;
    }
    const payload: ExperimentStart = {
      task_type: kind, baseline_version: Number(baseline), model_config_id: Number(model), judge_group_id: Number(judges),
      rule_versions: {[kind]: ruleIds[kind]!}, optimization_ids: optimization, regression_ids: {[kind]: regression},
      thresholds: {completion_rate: 1, quality_gain: .1, regression_tolerance: {[kind]: 0}},
      max_rounds: Number(rounds), ...(candidate ? {candidate_commit: candidate} : {}),
    };
    setStarting(true);
    try { const result = await startExperiment(payload); navigate("/bench/experiments/" + encodeURIComponent(result.id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "启动实验失败"); }
    finally { setStarting(false); }
  }

  return <section className="experiment-section experiment-console" aria-labelledby="evolution-experiments-title">
    <div className="experiment-heading"><div><h2 id="evolution-experiments-title">启动自动进化</h2>
      <p className="experiment-note">{kind === "chapter_text"
        ? "系统根据正文评测自动修改 Harness。原题和其他章节均达标后，可手动启用通过评测的版本。"
        : "选好目标与模型，自动完成评测、改版和复测。改进题与回归题均达标后，由你确认发布。"}</p></div>
      <button className="config-button" type="button" disabled={loading || starting} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} />刷新配置</button>
    </div>
    {error && <p role="alert" className="experiment-error">{error}</p>}
    {loading ? <p role="status">正在加载实验设置…</p> : options && <form className="experiment-form" onSubmit={(event) => void start(event)}>
      <fieldset disabled={starting} className="experiment-form-main">
        <legend className="sr-only">实验常用选择</legend>
        <div className="experiment-launch-grid">
          <div>{TASK_LABELS[kind]}{candidate && <p>指定候选 {candidate.slice(0, 10)}</p>}</div>
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
        <div><span>本次数据</span><strong>{TASK_LABELS[kind]} · {optimization.length} 道改进题 / {regression.length} 道回归题</strong></div>
        <div><span>过关门槛</span><strong>全部完成 · 改进题均分提高至少 0.1 分</strong>
          <small>回归均分不降 · 最多 {rounds} 轮</small></div>
        <div><span>评测模型</span><strong>{selectedGroup?.members.map((member) => member.model).join(" + ") || "等待选择"}</strong>
          <small>规则、输入与模型在启动后固定</small></div>
      </div>
      <details className="experiment-advanced" open={advanced}>
        <summary aria-expanded={advanced} onClick={(event) => { event.preventDefault(); setAdvanced((current) => !current); }}><SlidersHorizontal size={15} />高级设置</summary>
        {advanced && <>
        <fieldset className="experiment-form-main" disabled={starting}><legend className="sr-only">固定门槛与规则</legend>
          <div className="experiment-fields">{[kind].map((type) => <label key={type}>{TASK_LABELS[type]}规则版本
            <select className="config-input" required value={ruleIds[type] ?? ""} onChange={(event) => setRuleIds({ ...ruleIds, [type]: event.target.value })}>
              <option value="">选择规则</option>{launchRules(rules, type).map((rule) =>
                <option key={rule.id} value={rule.id}>v{rule.version} · {rule.name}</option>)}</select></label>)}</div>
          <label>最多轮数<input className="config-input" type="number" min="1" max="3" value={rounds} onChange={(event) => setRounds(event.target.value)} /></label>
        </fieldset>
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
