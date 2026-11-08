import "@/components/bench/experiment-observation.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getExperiment, getExperimentPackage, getActiveRuns, publishExperiment, restoreExperiment, stopExperiment,
  type ActiveRun, type Experiment, type ExperimentInput,
} from "@/lib/api";
import {
  EXPERIMENT_STATUS, PHASE_LABELS, TASK_TYPES,
  candidatePlaceholder, executionPhaseLabel, experimentTaskLabel, experimentTargetLabel,
} from "@/lib/experiment-types";
import {
  ArtifactFiles, ComparisonReport, EvidenceValues, ExperimentProgress, ExperimentScores, ModelIdentity, SampleScoreDetails,
} from "@/components/bench/ExperimentEvidence";
import { downloadTextFile } from "@/lib/download";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import TraceDetailPage from "@/pages/trace-detail";
import { ExperimentArtifacts } from "@/components/bench/ExperimentArtifacts";
import { ExperimentUsage } from "@/components/bench/ExperimentUsage";
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
  const [selectedTrace, setSelectedTrace] = useState("");
  const [liveRuns, setLiveRuns] = useState<ActiveRun[]>([]);
  const [liveError, setLiveError] = useState("");
  const [selectedSample, setSelectedSample] = useState("");
  const [input, setInput] = useState<ExperimentInput | null>(null);
  const [inputError, setInputError] = useState("");
  const [inputLoading, setInputLoading] = useState(false);
  const request = useRef(0);
  const inputRequest = useRef(0);
  const livePending = useRef<object | null>(null);
  const mounted = useRef(true);
  const pending = useRef(false);
  const refresh = useCallback(async () => {
    const sequence = ++request.current;
    // 活跃查询独立单飞，慢请求不能阻塞权威详情和下一轮刷新。
    if (!livePending.current) {
      const token = {};
      livePending.current = token;
      void getActiveRuns("experiment").then((active) => {
        if (mounted.current && livePending.current === token) {
          setLiveRuns(active.filter((run) => (run.experiment_id ?? run.external_refs?.experiment_id) === experimentId));
          setLiveError("");
        }
      }).catch(() => {
        if (mounted.current && livePending.current === token) setLiveError("运行 Trace 更新失败，保留最近成功的运行信息。");
      }).finally(() => { if (livePending.current === token) livePending.current = null; });
    }
    try {
      const record = await getExperiment(experimentId);
      if (mounted.current && sequence === request.current) { setExperiment(record); setError(""); }
    } catch (cause) {
      if (mounted.current && sequence === request.current) setError(cause instanceof Error ? cause.message : "读取实验失败");
    }
  }, [experimentId]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    mounted.current = true; pending.current = false; livePending.current = null; setBusy(false); setExperiment(null);
    setSelectedTrace(""); setLiveRuns([]); setLiveError(""); setSelectedSample(""); setSampleFilter(""); setInput(null); setInputError(""); setInputLoading(false);
    const poll = async () => {
      if (!pending.current) await refresh();
      if (alive) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { alive = false; mounted.current = false; livePending.current = null; request.current += 1; inputRequest.current += 1; clearTimeout(timer); };
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
  const recordedTraceIds = new Set([...experiment.samples.flatMap((item) => [...item.trace_ids, ...(item.evaluation_trace_ids ?? [])]),
    ...experiment.rounds.flatMap((round) => round.trace_ids ?? [])]);
  const pendingTraces = liveRuns.filter((run) => !recordedTraceIds.has(run.trace_id));
  const taskLabel = (kind: Experiment["task_type"]) => experimentTaskLabel(kind, experiment.flow_version);

  return <div className="bench-page experiment-detail">
    <header className="page-header"><Link to="/bench/experiments" className="action-link">返回实验记录</Link><h1>{experiment.name}</h1>
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
      <ExperimentUsage experimentId={experimentId} usage={experiment.usage} />
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
    <Tabs defaultValue="process" className="experiment-detail-tabs">
      <TabsList aria-label="实验详情内容"><TabsTrigger value="process">运行过程</TabsTrigger>
        <TabsTrigger value="scores">评分对比</TabsTrigger><TabsTrigger value="artifacts">产物文件</TabsTrigger></TabsList>
      <TabsContent value="process">
        {liveError && <p role="alert" className="experiment-error">{liveError}</p>}
        {pendingTraces.length > 0 && <section className="experiment-section"><h2>正在执行的 Trace</h2>
          <p className="experiment-note">运行中即时关联，完成后归入下方样本与轮次。</p>
          {pendingTraces.map((run) => {
            const linkedSample = experiment.samples.find((item) => item.id === run.external_refs?.sample_id);
            const label = run.run_purpose === "evolution_evolve" ? "改版" : run.run_purpose === "evolution_eval" ? "评分" :
              ["optimization", "evolution"].includes(run.run_purpose ?? "") ? "生成" : "实验";
            return <div key={run.trace_id} className="experiment-active-run"><div>
              <strong>{linkedSample?.package_title ?? (label === "改版" ? "候选改版" : "样本信息同步中")}</strong>
              <span className="experiment-cell-note">{PHASE_LABELS[linkedSample?.phase ?? experiment.phase] ?? experiment.phase}
                {linkedSample ? " · 第 " + linkedSample.round + " 轮 · 重复 " + linkedSample.repeat + " / 尝试 " + linkedSample.attempt : ""}</span>
            </div><button type="button" className="action-link mono" onClick={() => setSelectedTrace(run.trace_id)}>{label} Trace {run.trace_id.slice(0, 20)}</button></div>;
          })}
        </section>}
        <section className="experiment-section"><div className="experiment-heading"><h2>运行与 Trace</h2>
          <label className="experiment-filter">评测环节<select className="config-input" value={sampleFilter} onChange={(event) => setSampleFilter(event.target.value)}>
            <option value="">全部环节</option>{["baseline", "exploration", "regression", "validation"].map((phase) =>
              <option key={phase} value={phase}>{PHASE_LABELS[phase]}</option>)}</select></label></div>
          <div className="experiment-table-scroll"><table className="data-table"><thead><tr><th>样本 / 目标</th><th>环节 / 版本</th><th>轮次 / 重复 / 尝试</th><th>状态</th><th>Trace / 输入</th></tr></thead><tbody>
            {samples.map((item) => <tr key={item.id}><td><strong>{item.package_title}</strong>{item.target && <small className="experiment-cell-note">{experimentTargetLabel(item.target)}</small>}</td>
              <td>{PHASE_LABELS[item.phase] ?? item.phase}<small className="experiment-cell-note">{item.arm === "baseline" ? "固定原版" : "候选第 " + item.round + " 轮"}</small></td>
              <td>{item.round} / {item.repeat} / {item.attempt}</td>
              <td><span className={"experiment-state " + item.status}>{EXPERIMENT_STATUS[item.status] ?? item.status}</span>
                {item.execution_phase && <small className="experiment-cell-note">{executionPhaseLabel(item.execution_phase)}</small>}
                {item.error && <p className="experiment-error">{item.error}</p>}</td>
              <td><div className="experiment-trace-links">{item.trace_ids.map((id) => <button className="action-link mono" type="button" key={id} onClick={() => setSelectedTrace(id)}>生成 Trace {id.slice(0, 20)}</button>)}
                {item.evaluation_trace_ids?.map((id) => <button className="action-link mono" type="button" key={id} onClick={() => setSelectedTrace(id)}>评分 Trace {id.slice(0, 20)}</button>)}
                {!item.trace_ids.length && !item.evaluation_trace_ids?.length && <span className="experiment-cell-note">{["pending", "running", "scoring", "recovering"].includes(item.status) ? "Trace 正在同步" : "没有关联 Trace"}</span>}
                <button className="action-link" type="button" onClick={() => void viewSample(item.id)}>查看固定输入与检查</button></div></td></tr>)}
          </tbody></table></div>
          {!samples.length && <p className="experiment-note">暂无该环节运行记录。</p>}
          {sample && <section className="experiment-sample"><div className="experiment-heading"><h3>{sample.package_title} · 固定输入与检查</h3>
            <button className="action-link" type="button" onClick={() => { inputRequest.current += 1; setSelectedSample(""); setInput(null); }}>收起</button></div>
            <p>目标完成：{sample.completed ? "是" : "否"} · 输入版本 <code>{sample.input_revision}</code></p>
            <p>运行版本 <code>{sample.source_commit}</code></p>
            {experiment.can_read_content ? <details open><summary>本题固定输入</summary>
              {inputLoading && <p role="status">正在读取固定输入…</p>}
              {inputError && <p role="alert" className="experiment-error">{inputError}</p>}
              {input && <><h4>完整需求</h4><p className="experiment-prewrap">{input.demand_md || "使用下方固定上游文稿"}</p>
                <h4>预设确认规则</h4><p className="experiment-prewrap">{input.confirmation_rules}</p><ArtifactFiles files={input.files} />
                {!!input.skills.length && <details><summary>固定技能</summary><EvidenceValues value={input.skills} /></details>}</>}
            </details> : <p className="experiment-note">当前账号没有正文读取权限。</p>}
            <details open><summary>完成检查</summary><EvidenceValues value={sample.checks} /></details>
          </section>}
        </section>
        <section className="experiment-section"><h2>候选改动与轮次</h2>
          {!experiment.rounds.length && <p className="experiment-note">尚未产生改进轮次。</p>}
          {experiment.rounds.map((round) => <details className="experiment-round" key={round.round}>
            <summary>第 {round.round} 轮 · {EXPERIMENT_STATUS[round.status] ?? round.status} · {round.summary || "查看候选"}</summary>
            <p className="mono">{round.base_commit} → {round.source_commit || "尚无提交"}</p>
            {round.error && <p className="experiment-error">{round.error}</p>}
            <div className="experiment-trace-links">{round.trace_ids?.map((id) => <button className="action-link mono" key={id} type="button" onClick={() => setSelectedTrace(id)}>改版 Trace {id.slice(0, 20)}</button>)}</div>
            {round.diff ? <pre className="experiment-diff">{round.diff}</pre> : <p className="experiment-note">没有可展示的文件差异。</p>}
          </details>)}
        </section>
        {selectedTrace && <section className="experiment-trace-inspector" aria-label="实验 Trace 详情"><div className="experiment-heading"><div>
          <h2>Trace 详情</h2><p className="experiment-note">{experiment.name} · {selectedTrace}</p></div>
          <button type="button" className="action-link" onClick={() => setSelectedTrace("")}>收起 Trace</button></div>
          <TraceDetailPage key={selectedTrace} traceIdOverride={selectedTrace} embedded canReadContent={experiment.can_read_content} />
        </section>}
    <section className="experiment-section"><h2>恢复与操作记录</h2>{!experiment.events.length ? <p className="experiment-note">暂无操作记录。</p> :
      <ol className="experiment-events">{experiment.events.map((event) => <li key={event.id}>
        <time>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</time><span>{event.message}</span></li>)}</ol>}
    </section>
      </TabsContent>
      <TabsContent value="scores">
    <section className="experiment-section"><h2>质量与完成率</h2><ExperimentScores report={experiment.report} />
      {experiment.report.reason && <p>{experiment.report.reason}</p>}
      {experiment.report.exploration && <ComparisonReport title="主攻优化复测" report={experiment.report.exploration} />}
      {TASK_TYPES.filter((kind) => kind !== experiment.task_type).map((kind) => experiment.report.regressions?.[kind] &&
        <ComparisonReport key={kind} title={taskLabel(kind) + "回归"} report={experiment.report.regressions[kind]!} />)}
      {experiment.report.validation && <ComparisonReport title="主攻独立终验" report={experiment.report.validation} />}
      {!experiment.report.exploration && !experiment.report.validation && !experiment.report.reason && <p className="experiment-note">尚无完整比较报告。</p>}
    </section>

        <section className="experiment-section"><h2>各轮评分对比</h2>
          {experiment.rounds.map((round) => <section key={round.round} className="experiment-round"><h3>第 {round.round} 轮</h3>
            <ComparisonReport title="本轮优化复测" report={round.report} />
            {TASK_TYPES.filter((kind) => kind !== experiment.task_type).map((kind) => round.regressions?.[kind] &&
              <ComparisonReport key={kind} title={taskLabel(kind) + "本轮回归"} report={round.regressions[kind]!} />)}
          </section>)}
        </section>
        <section className="experiment-section"><h2>逐样本评分与依据</h2>
          {!experiment.can_read_content && <p className="experiment-note">当前账号可以查看分数，评分正文依据需要正文读取权限。</p>}
          {experiment.samples.map((item) => <details className="experiment-round" key={item.id}><summary>{item.package_title} · {PHASE_LABELS[item.phase] ?? item.phase} ·
            {item.arm === "baseline" ? " 原版" : " 候选第 " + item.round + " 轮"} · 重复 {item.repeat} / 尝试 {item.attempt} · {item.scores?.overall == null ? "未评分" : item.scores.overall.toFixed(3) + " / 5"}</summary>
            {item.error && <p className="experiment-error">{item.error}</p>}
            {item.scores ? <SampleScoreDetails sample={item} judges={experiment.models?.judges ?? []} /> : <p className="experiment-note">尚无有效评分。</p>}
          </details>)}
          {!experiment.samples.length && <p className="experiment-note">尚无样本评分记录。</p>}
        </section>
      </TabsContent>
      <TabsContent value="artifacts"><ExperimentArtifacts samples={experiment.samples} canReadContent={experiment.can_read_content} /></TabsContent>
    </Tabs>
  </div>;
}
