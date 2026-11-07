import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getExperiment, getExperimentPackage, publishExperiment, restoreExperiment, stopExperiment,
  type Experiment, type ExperimentInput,
} from "@/lib/api";
import {
  EXPERIMENT_STATUS, PHASE_LABELS, TASK_TYPES,
  candidatePlaceholder, executionPhaseLabel, experimentTaskLabel, experimentTargetLabel,
} from "@/lib/experiment-types";
import {
  ArtifactFiles, ComparisonReport, EvidenceValues, ExperimentProgress, ExperimentScores, ModelIdentity, SampleScoreDetails,
} from "@/components/bench/ExperimentEvidence";
import { downloadTextFile } from "@/lib/download";
import "@/components/bench/experiments.css";

const STOPPABLE = new Set(["pending", "running", "recovering", "stopping"]);
const PUBLISH_LABELS: Record<string, string> = {
  not_started: "尚未发布", publishing: "发布中", activated: "已生效", failed: "发布失败",
  unconfirmed: "加载尚未确认", restored: "已恢复旧版",
};

export default function ExperimentDetail() {
  const { experimentId = "" } = useParams<{ experimentId: string }>();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sampleFilter, setSampleFilter] = useState("");
  const [selectedSample, setSelectedSample] = useState("");
  const [input, setInput] = useState<ExperimentInput | null>(null);
  const [inputError, setInputError] = useState("");
  const [inputLoading, setInputLoading] = useState(false);
  const request = useRef(0);
  const inputRequest = useRef(0);
  const mounted = useRef(true);
  const pending = useRef(false);
  const refresh = useCallback(async () => {
    const sequence = ++request.current;
    try {
      const result = await getExperiment(experimentId);
      if (mounted.current && sequence === request.current) { setExperiment(result); setError(""); }
    } catch (cause) { if (mounted.current && sequence === request.current) setError(cause instanceof Error ? cause.message : "读取实验失败"); }
  }, [experimentId]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    mounted.current = true; pending.current = false; setBusy(false); setExperiment(null);
    setSelectedSample(""); setSampleFilter(""); setInput(null); setInputError(""); setInputLoading(false);
    const poll = async () => {
      if (!pending.current) await refresh();
      if (alive) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { alive = false; mounted.current = false; request.current += 1; inputRequest.current += 1; clearTimeout(timer); };
  }, [refresh]);

  async function operate(action: (id: string) => Promise<Experiment>) {
    if (pending.current) return;
    const sequence = ++request.current;
    pending.current = true; setBusy(true); setError("");
    try {
      const result = await action(experimentId);
      if (mounted.current && sequence === request.current) setExperiment(result);
    } catch (cause) {
      if (mounted.current && sequence === request.current) {
        setError(cause instanceof Error ? cause.message : "操作失败");
        try { const result = await getExperiment(experimentId); if (mounted.current && sequence === request.current) setExperiment(result); }
        catch { /* 操作结果暂时不明，保留错误并由下一次轮询核对。 */ }
      }
    } finally { if (mounted.current && sequence === request.current) { pending.current = false; setBusy(false); } }
  }
  async function viewSample(id: string) {
    const selected = experiment?.samples.find((item) => item.id === id);
    const sequence = ++inputRequest.current;
    setSelectedSample(id); setInput(null); setInputError(""); setInputLoading(false);
    if (!selected || !experiment?.can_read_content) return;
    setInputLoading(true);
    try {
      const data = await getExperimentPackage(selected.package_id);
      if (!mounted.current || sequence !== inputRequest.current) return;
      if (data.input_revision !== selected.input_revision) {
        setInputError("资料包版本与本次运行不同，不能用当前资料冒充原始输入。");
      } else setInput(data.input);
    } catch (cause) {
      if (mounted.current && sequence === inputRequest.current) setInputError(cause instanceof Error ? cause.message : "读取固定输入失败");
    } finally { if (mounted.current && sequence === inputRequest.current) setInputLoading(false); }
  }

  if (!experiment) return <div className="bench-page">{error ? <><p role="alert" className="experiment-error">{error}</p>
    <button className="config-button" onClick={() => void refresh()}>重试</button></> : <p role="status">正在加载实验…</p>}</div>;
  const sample = experiment.samples.find((item) => item.id === selectedSample);
  const samples = experiment.samples.filter((item) => !sampleFilter || item.phase === sampleFilter);
  const canRestore = experiment.can_restore ?? (["failed", "unconfirmed", "activated"].includes(experiment.publish_status) || !!experiment.load_error);
  const currentCandidate = experiment.current_candidate ?? (experiment.current_round === experiment.best_candidate?.round ? experiment.best_candidate : null);
  const steps = experiment.flow_version === 2 ? ["baseline", "evolving", "exploration", "regression", "validation", "review", "publish"] :
    ["baseline", "evolving", "exploration", "validation", "review", "publish"];
  const activeStep = steps.indexOf(experiment.phase);
  const activeSamples = experiment.samples.filter((item) => ["running", "scoring", "evaluation_error", "interrupted"].includes(item.status));
  const lastEvent = experiment.events.at(-1);
  const taskLabel = (kind: Experiment["task_type"]) => experimentTaskLabel(kind, experiment.flow_version);

  return <div className="bench-page experiment-detail">
    <header className="page-header"><Link to="/bench" className="action-link">返回评测工作台</Link><h1>{experiment.name}</h1>
      <p className="page-desc"><span className="experiment-type-label">{taskLabel(experiment.task_type)}</span>
        <span className={"experiment-state " + experiment.status}>{EXPERIMENT_STATUS[experiment.status] ?? experiment.status}</span></p>
    </header>
    {error && <p role="alert" className="experiment-error">{error}</p>}
    <section className="experiment-section">
      <div className="experiment-heading"><div><h2>实验进度</h2><p className="experiment-note">优化与回归通过后才做独立终验，通过后等待人工发布。</p></div>
        <div className="experiment-actions"><button className="config-button" type="button" disabled={busy} onClick={() => void refresh()}>刷新</button>
          {STOPPABLE.has(experiment.status) && <button className="config-button" type="button" disabled={busy || experiment.status === "stopping"} onClick={() => void operate(stopExperiment)}>停止实验</button>}
          <button className="config-button" type="button" onClick={() => downloadTextFile(experiment.id + "-report.json", JSON.stringify(experiment, null, 2), "application/json;charset=utf-8")}>导出报告</button>
        </div>
      </div>
      {experiment.flow_version !== 2 && <p className="experiment-note">历史实验沿用创建时的目标和流程，终验与回归按原规则核对。</p>}
      <ol className="experiment-stepper" aria-label="自动进化流程">{steps.map((phase, index) => <li key={phase}
        className={index === activeStep ? "active" : index < activeStep ? "done" : ""}
        aria-current={index === activeStep ? "step" : undefined}><span>{index + 1}</span>
        {experiment.flow_version !== 2 && phase === "validation" ? "终验与回归" : PHASE_LABELS[phase]}</li>)}</ol>
      <ExperimentProgress experiment={experiment} />
      <dl className="experiment-facts experiment-primary-facts">
        <div><dt>Harness 原版 → 当前候选</dt><dd><strong>v{experiment.baseline.version}</strong> → {currentCandidate ? "候选第 " + currentCandidate.round + " 轮" : candidatePlaceholder(experiment.phase, experiment.status, experiment.current_round)}</dd></div>
        <div><dt>执行模型</dt><dd><ModelIdentity model={experiment.models?.executor} /></dd></div>
        <div><dt>评测模型</dt><dd>{experiment.models?.judges?.length ? experiment.models.judges.map((judge, index) => <ModelIdentity key={judge.id ?? index} model={judge} />) : "未记录模型"}</dd></div>
        <div><dt>保留最佳候选</dt><dd>{experiment.best_candidate ? "第 " + experiment.best_candidate.round + " 轮" : "尚无合格候选"}</dd></div>
      </dl>
      {activeSamples.length > 0 && <div className="experiment-active-samples">{activeSamples.map((item) => <div key={item.id}>
        <strong>{item.package_title}</strong><span>{item.target && experimentTargetLabel(item.target)} · {EXPERIMENT_STATUS[item.status] ?? item.status}</span>
        {item.execution_phase && <small>{executionPhaseLabel(item.execution_phase)}</small>}
        {item.error && <p className="experiment-error">{item.error}</p>}
        {item.score_retry_at && <small>下次评分核对：{new Date(item.score_retry_at * 1000).toLocaleTimeString("zh-CN", { hour12: false })}</small>}
      </div>)}</div>}
      <p className="experiment-note">最近活动：{lastEvent ? <>{lastEvent.message}<time dateTime={lastEvent.created_at}> · {new Date(lastEvent.created_at).toLocaleString("zh-CN", { hour12: false })}</time></> : "暂无活动记录"}</p>
      {experiment.end_reason && <p className="experiment-end-reason">结束原因：{experiment.end_reason}</p>}
      <details><summary>固定版本与实验设置</summary><dl className="experiment-facts">
        <div><dt>原版完整标识</dt><dd><code>{experiment.baseline.commit}</code></dd></div>
        {currentCandidate && <div><dt>当前候选完整标识</dt><dd><code>{currentCandidate.commit}</code></dd></div>}
        {experiment.best_candidate && <div><dt>保留最佳完整标识</dt><dd><code>{experiment.best_candidate.commit}</code></dd></div>}
        <div><dt>进化模型</dt><dd><ModelIdentity model={experiment.models?.evolver} /></dd></div>
      </dl><EvidenceValues value={experiment.settings} /></details>
      <details><summary>调用用量，未知值单独标记</summary><EvidenceValues value={experiment.usage} /></details>
    </section>

    <section className="experiment-section"><h2>质量与完成率</h2><ExperimentScores report={experiment.report} />
      {experiment.report.reason && <p>{experiment.report.reason}</p>}
      {experiment.report.exploration && <ComparisonReport title="主攻优化复测" report={experiment.report.exploration} />}
      {TASK_TYPES.filter((kind) => kind !== experiment.task_type).map((kind) => experiment.report.regressions?.[kind] &&
        <ComparisonReport key={kind} title={taskLabel(kind) + "回归"} report={experiment.report.regressions[kind]!} />)}
      {experiment.report.validation && <ComparisonReport title="主攻独立终验" report={experiment.report.validation} />}
      {!experiment.report.exploration && !experiment.report.validation && !experiment.report.reason && <p className="experiment-note">尚无完整比较报告。</p>}
    </section>

    <section className="experiment-section"><h2>候选改动与轮次</h2>{!experiment.rounds.length && <p className="experiment-note">尚未产生改进轮次。</p>}
      {experiment.rounds.map((round) => <details className="experiment-round" key={round.round}>
        <summary>第 {round.round} 轮 · {EXPERIMENT_STATUS[round.status] ?? round.status} · {round.summary || "查看候选"}</summary>
        <p className="mono">{round.base_commit} → {round.source_commit || "尚无提交"}</p>
        {round.error && <p className="experiment-error">{round.error}</p>}
        <ComparisonReport title="本轮优化复测" report={round.report} />
        {TASK_TYPES.filter((kind) => kind !== experiment.task_type).map((kind) => round.regressions?.[kind] &&
          <ComparisonReport key={kind} title={taskLabel(kind) + "本轮回归"} report={round.regressions[kind]!} />)}
        {round.diff ? <pre className="experiment-diff">{round.diff}</pre> : <p className="experiment-note">没有可展示的文件差异。</p>}
      </details>)}
    </section>

    <section className="experiment-section"><div className="experiment-heading"><h2>逐题产物与运行证据</h2>
      <label className="experiment-filter">评测环节<select className="config-input" value={sampleFilter} onChange={(event) => setSampleFilter(event.target.value)}>
        <option value="">全部环节</option>{["baseline", "exploration", "regression", "validation"].map((phase) =>
          <option key={phase} value={phase}>{PHASE_LABELS[phase]}</option>)}</select></label></div>
      <div className="experiment-table-scroll"><table className="data-table"><thead><tr><th>样本 / 目标</th><th>环节 / 版本</th><th>轮次 / 重复 / 尝试</th><th>状态</th><th>质量 / 5</th><th></th></tr></thead><tbody>
        {samples.map((item) => <tr key={item.id}><td><strong>{item.package_title}</strong>{item.target && <small className="experiment-cell-note">{experimentTargetLabel(item.target)}</small>}</td>
          <td>{PHASE_LABELS[item.phase] ?? item.phase}<small className="experiment-cell-note">{item.arm === "baseline" ? "固定原版" : "候选第 " + item.round + " 轮"}</small></td>
          <td>{item.round} / {item.repeat} / {item.attempt}</td>
          <td><span className={"experiment-state " + item.status}>{EXPERIMENT_STATUS[item.status] ?? item.status}</span>
            {item.execution_phase && <small className="experiment-cell-note">{executionPhaseLabel(item.execution_phase)}</small>}
            {item.error && <p className="experiment-error">{item.error}</p>}</td>
          <td>{item.scores?.overall == null ? "未评分" : item.scores.overall.toFixed(3)}</td>
          <td><button className="action-link" type="button" onClick={() => void viewSample(item.id)}>查看证据</button></td></tr>)}
      </tbody></table></div>
      {!samples.length && <p className="experiment-note">暂无该环节运行记录。</p>}
      {sample && <section className="experiment-sample"><div className="experiment-heading"><h3>{sample.package_title}</h3>
        <button className="action-link" type="button" onClick={() => { inputRequest.current += 1; setSelectedSample(""); setInput(null); }}>收起</button></div>
        <p>目标完成：{sample.completed ? "是" : "否"} · 输入版本 <code>{sample.input_revision}</code></p>
        <p>运行版本 <code>{sample.source_commit}</code></p>
        {experiment.can_read_content && <details><summary>本题固定输入</summary>
          {inputLoading && <p role="status">正在读取固定输入…</p>}
          {inputError && <p role="alert" className="experiment-error">{inputError}</p>}
          {input && <><h4>完整需求</h4><p className="experiment-prewrap">{input.demand_md || "使用下方固定上游文稿"}</p>
            <h4>预设确认规则</h4><p className="experiment-prewrap">{input.confirmation_rules}</p><ArtifactFiles files={input.files} />
            {!!input.skills.length && <details><summary>固定技能</summary><EvidenceValues value={input.skills} /></details>}</>}
        </details>}
        <details open><summary>完成检查</summary><EvidenceValues value={sample.checks} /></details>
        {sample.scores && <details open><summary>分项评分与依据</summary><SampleScoreDetails sample={sample} judges={experiment.models?.judges ?? []} /></details>}
        <div className="experiment-actions">{sample.trace_ids.map((id) => <Link className="action-link mono" key={id} to={"/traces/" + encodeURIComponent(id)}>执行 Trace {id.slice(0, 16)}</Link>)}
          {sample.evaluation_trace_ids?.map((id) => <Link className="action-link mono" key={id} to={"/traces/" + encodeURIComponent(id)}>评分 Trace {id.slice(0, 16)}</Link>)}</div>
        {experiment.can_read_content ? <><h4>本题运行产物</h4><ArtifactFiles files={sample.artifacts} /></> : <p className="experiment-note">当前账号没有正文读取权限。</p>}
        <details><summary>本次用量</summary><EvidenceValues value={sample.usage} /></details>
      </section>}
    </section>

    <section className="experiment-section experiment-publication"><div className="experiment-heading"><div><h2>人工审阅与发布</h2>
      <p className="experiment-note">只发布通过独立终验的固定候选。生产变化后，需要重新验证。</p></div>
      <strong className={"experiment-state " + experiment.status}>{PUBLISH_LABELS[experiment.publish_status] ?? experiment.publish_status}</strong></div>
      {experiment.stale_reason && <p role="alert" className="experiment-error">{experiment.stale_reason}</p>}
      {experiment.loaded_revision && <p>执行端已确认版本：<code>{experiment.loaded_revision}</code></p>}
      {experiment.load_error && <p className="experiment-error">{experiment.load_error}</p>}
      {experiment.rollback_status && <p>恢复旧版：{({ restoring: "恢复中", restored: "恢复已确认", failed: "恢复失败" } as Record<string, string>)[experiment.rollback_status] ?? experiment.rollback_status}</p>}
      <div className="experiment-actions"><button className="config-button primary" type="button" disabled={busy || !experiment.can_publish} onClick={() => void operate(publishExperiment)}>
        {experiment.status === "publish_failed" ? "重试发布并核对加载" : "批准并发布候选"}</button>
        {canRestore && <button className="config-button" type="button" disabled={busy || experiment.rollback_status === "restoring"} onClick={() => void operate(restoreExperiment)}>恢复旧版</button>}
      </div>
    </section>
    <section className="experiment-section"><h2>恢复与操作记录</h2>{!experiment.events.length ? <p className="experiment-note">暂无操作记录。</p> :
      <ol className="experiment-events">{experiment.events.map((event) => <li key={event.id}>
        <time>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</time><span>{event.message}</span></li>)}</ol>}
    </section>
  </div>;
}
