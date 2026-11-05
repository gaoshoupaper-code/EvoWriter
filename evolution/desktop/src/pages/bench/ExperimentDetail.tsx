import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getExperiment, publishExperiment, restoreExperiment, stopExperiment, type Experiment } from "@/lib/api";
import { EXPERIMENT_STATUS, PHASE_LABELS, TASK_LABELS, TASK_TYPES } from "@/lib/experiment-types";
import { ArtifactFiles, ComparisonReport, EvidenceValues } from "@/components/bench/ExperimentEvidence";
import { downloadTextFile } from "@/lib/download";
import "@/components/bench/experiments.css";

const ACTIVE = new Set(["pending", "running", "recovering", "stopping", "publishing"]);
const PUBLISH_LABELS: Record<string, string> = { not_started: "尚未发布", publishing: "发布中", activated: "已生效", failed: "发布失败", unconfirmed: "加载尚未确认", restored: "已恢复旧版" };

export default function ExperimentDetail() {
  const { experimentId = "" } = useParams<{ experimentId: string }>();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sampleFilter, setSampleFilter] = useState("");
  const [selectedSample, setSelectedSample] = useState("");
  const request = useRef(0);
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
    mounted.current = true; pending.current = false; setBusy(false); setExperiment(null); setSelectedSample(""); setSampleFilter("");
    const poll = async () => {
      if (!pending.current) await refresh();
      if (alive) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { alive = false; mounted.current = false; request.current += 1; clearTimeout(timer); };
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
        try { const result = await getExperiment(experimentId); if (mounted.current && sequence === request.current) setExperiment(result); } catch { /* 保留原始操作错误，下一次轮询继续核对。 */ }
      }
    } finally { if (mounted.current && sequence === request.current) { pending.current = false; setBusy(false); } }
  }
  if (!experiment) return <div className="bench-page">{error ? <><p role="alert" className="experiment-error">{error}</p><button className="config-button" onClick={() => void refresh()}>重试</button></> : <p role="status">正在加载实验…</p>}</div>;
  const sample = experiment.samples.find((item) => item.id === selectedSample);
  const samples = experiment.samples.filter((item) => !sampleFilter || item.phase === sampleFilter);
  const canRestore = ["failed", "unconfirmed", "activated"].includes(experiment.publish_status) || !!experiment.load_error;
  return <div className="bench-page experiment-detail">
    <header className="page-header"><Link to="/bench" className="action-link">返回评测工作台</Link><h1>{experiment.name}</h1><p className="page-desc">{TASK_LABELS[experiment.task_type]} · {EXPERIMENT_STATUS[experiment.status] ?? experiment.status} · {PHASE_LABELS[experiment.phase] ?? experiment.phase}</p></header>
    {error && <p role="alert" className="experiment-error">{error}</p>}
    <section className="experiment-section"><div className="experiment-heading"><h2>固定条件与进度</h2><div className="experiment-actions">
      <button className="config-button" type="button" disabled={busy} onClick={() => void refresh()}>刷新</button>
      {ACTIVE.has(experiment.status) && <button className="config-button" type="button" disabled={busy || experiment.status === "stopping"} onClick={() => void operate(stopExperiment)}>停止实验</button>}
      <button className="config-button" type="button" onClick={() => downloadTextFile(`${experiment.id}-report.json`, JSON.stringify(experiment, null, 2), "application/json;charset=utf-8")}>导出报告</button>
    </div></div>
      <dl className="experiment-facts"><div><dt>固定原版</dt><dd>v{experiment.baseline.version} <code>{experiment.baseline.commit}</code></dd></div>
        <div><dt>已尝试轮次</dt><dd>{experiment.current_round} / {experiment.max_rounds}</dd></div>
        {experiment.progress && <div><dt>运行进度</dt><dd>{experiment.progress.done} / {experiment.progress.total} · 目标完成 {experiment.progress.completed}</dd></div>}
        <div><dt>保留最佳候选</dt><dd>{experiment.best_candidate ? <><code>{experiment.best_candidate.commit}</code> · 第 {experiment.best_candidate.round} 轮</> : "尚无"}</dd></div>
        <div><dt>结束原因</dt><dd>{experiment.end_reason || "尚未结束"}</dd></div></dl>
      <details><summary>启动时固定的实验设置</summary><EvidenceValues value={experiment.settings} /></details>
      <details><summary>调用用量（未知值单独标记）</summary><EvidenceValues value={experiment.usage} /></details>
    </section>
    <section className="experiment-section"><h2>质量与完成率报告</h2>
      {experiment.report.reason && <p>{experiment.report.reason}</p>}
      {experiment.report.exploration && <ComparisonReport title="主攻探索" report={experiment.report.exploration} />}
      {experiment.report.validation && <ComparisonReport title="主攻独立终验" report={experiment.report.validation} />}
      {TASK_TYPES.filter((kind) => kind !== experiment.task_type).map((kind) => experiment.report.regressions?.[kind] &&
        <ComparisonReport key={kind} title={`${TASK_LABELS[kind]}回归`} report={experiment.report.regressions[kind]!} />)}
      {!experiment.report.exploration && !experiment.report.validation && !experiment.report.reason && <p className="experiment-note">尚无完整比较报告。</p>}
    </section>
    <section className="experiment-section"><h2>候选改动与轮次</h2>{!experiment.rounds.length && <p className="experiment-note">尚未产生改进轮次。</p>}
      {experiment.rounds.map((round) => <details className="experiment-round" key={round.round}><summary>第 {round.round} 轮 · {EXPERIMENT_STATUS[round.status] ?? round.status} · {round.summary || "查看候选"}</summary>
        <p className="mono">{round.base_commit} → {round.source_commit}</p>{round.error && <p className="experiment-error">{round.error}</p>}
        <ComparisonReport title="本轮探索" report={round.report} />
        {round.diff ? <pre className="experiment-diff">{round.diff}</pre> : <p className="experiment-note">没有可展示的文件差异。</p>}
      </details>)}
    </section>
    <section className="experiment-section"><div className="experiment-heading"><h2>样本与运行证据</h2><label className="experiment-filter">阶段<select className="config-input" value={sampleFilter} onChange={(event) => setSampleFilter(event.target.value)}>
      <option value="">全部阶段</option>{["baseline", "exploration", "validation", "regression"].map((phase) => <option key={phase} value={phase}>{PHASE_LABELS[phase]}</option>)}</select></label></div>
      <div className="experiment-table-scroll"><table className="data-table"><thead><tr><th>样本</th><th>阶段 / 版本</th><th>轮次 / 重复 / 尝试</th><th>状态</th><th>质量</th><th>证据</th></tr></thead><tbody>
        {samples.map((item) => <tr key={item.id}><td>{item.package_title}</td><td>{PHASE_LABELS[item.phase] ?? item.phase} / {item.arm === "baseline" ? "原版" : "候选"}</td>
          <td>{item.round} / {item.repeat} / {item.attempt}</td><td>{EXPERIMENT_STATUS[item.status] ?? item.status}{item.error && <p className="experiment-error">{item.error}</p>}</td>
          <td>{item.scores?.overall == null ? "未知" : item.scores.overall.toFixed(3)}</td><td><button className="action-link" type="button" onClick={() => setSelectedSample(item.id)}>查看证据</button></td></tr>)}
      </tbody></table></div>
      {!samples.length && <p className="experiment-note">暂无该阶段运行记录。</p>}
      {sample && <section className="experiment-sample"><div className="experiment-heading"><h3>{sample.package_title}</h3><button className="action-link" type="button" onClick={() => setSelectedSample("")}>收起</button></div>
        <p>目标完成：{sample.completed ? "是" : "否"} · 输入版本 <code>{sample.input_revision}</code></p>
        <p>运行版本 <code>{sample.source_commit}</code></p>
        <details open><summary>完成检查</summary><EvidenceValues value={sample.checks} /></details>
        {sample.scores && <details open><summary>分项评分与依据</summary><EvidenceValues value={sample.scores.dimensions} /><EvidenceValues value={sample.scores.reasons} /></details>}
        <div className="experiment-actions">{sample.trace_ids.map((id) => <Link className="action-link mono" key={id} to={`/traces/${encodeURIComponent(id)}`}>Trace {id.slice(0, 16)}</Link>)}</div>
        {experiment.can_read_content ? <ArtifactFiles files={sample.artifacts} /> : <p className="experiment-note">当前账号没有正文读取权限。</p>}
        <details><summary>本次用量</summary><EvidenceValues value={sample.usage} /></details>
      </section>}
    </section>
    <section className="experiment-section"><h2>人工审阅与发布</h2>
      <p className="experiment-note">发布对象固定为通过终验的候选。生产已变化时，须重新发起验证。</p>
      {experiment.stale_reason && <p role="alert" className="experiment-error">{experiment.stale_reason}</p>}
      <p>发布状态：<strong>{PUBLISH_LABELS[experiment.publish_status] ?? experiment.publish_status}</strong></p>
      {experiment.loaded_revision && <p>执行端已确认版本：<code>{experiment.loaded_revision}</code></p>}
      {experiment.load_error && <p className="experiment-error">{experiment.load_error}</p>}
      {experiment.rollback_status && <p>恢复旧版：{({ restoring: "恢复中", restored: "恢复已确认", failed: "恢复失败" } as Record<string, string>)[experiment.rollback_status] ?? experiment.rollback_status}</p>}
      <div className="experiment-actions"><button className="config-button primary" type="button" disabled={busy || !experiment.can_publish} onClick={() => void operate(publishExperiment)}>批准并发布候选</button>
        {canRestore && <button className="config-button" type="button" disabled={busy || experiment.rollback_status === "restoring"} onClick={() => void operate(restoreExperiment)}>恢复旧版</button>}</div>
    </section>
    <section className="experiment-section"><h2>恢复与操作记录</h2>{!experiment.events.length ? <p className="experiment-note">暂无操作记录。</p> :
      <ol className="experiment-events">{experiment.events.map((event) => <li key={event.id}><time>{event.created_at.slice(0, 19).replace("T", " ")}</time><span>{event.message}</span></li>)}</ol>}
    </section>
  </div>;
}
