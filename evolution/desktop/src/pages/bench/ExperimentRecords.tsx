import PageAgentSelector from "@/components/PageAgentSelector";
import { currentTask } from "@/lib/writing-agent";
import "@/components/bench/experiment-observation.css";
import { Link } from "react-router-dom";
import { useExperiments } from "@/hooks/useExperiments";
import { EXPERIMENT_STATUS, experimentTaskLabel } from "@/lib/experiment-types";
import { ExperimentScores } from "@/components/bench/ExperimentEvidence";
import "@/components/bench/experiments.css";

export default function ExperimentRecords() {
  const { runs, loading, error, refresh } = useExperiments();

  const visible = runs.filter((run) => run.task_type === currentTask() && run.flow_version === 3);
  return <div className="bench-page experiment-records-page">
    <header className="page-header experiment-heading"><div><h1>实验记录</h1>
      <p className="page-desc">一次实验，一份完整记录。按写作阶段整理，最新创建的实验在前。</p></div>
      <Link className="config-button" to="/bench">前往工作台</Link></header>
    <PageAgentSelector />
    {error && <p role="alert" className="experiment-error">更新失败：{error} <button className="action-link" onClick={refresh}>重试</button></p>}
    {loading ? <p role="status">正在加载实验记录…</p> : visible.length ? <div className="experiment-record-list">
      {visible.map((run) => <article className="experiment-record" key={run.id}>
        <div className="experiment-record-main"><span className="experiment-type-label">{experimentTaskLabel(run.task_type, run.flow_version)}</span>
          <Link className="experiment-run-name" to={"/bench/experiments/" + encodeURIComponent(run.id)}>{run.name}</Link>
          <time className="experiment-cell-note" dateTime={run.created_at}>创建于 {new Date(run.created_at).toLocaleString("zh-CN", { hour12: false })}</time></div>
        <div><span className={"experiment-state " + run.status}>{EXPERIMENT_STATUS[run.status] ?? run.status}</span>
          <span className="experiment-cell-note">原版 v{run.baseline.version} · 第 {run.current_round} / {run.max_rounds} 轮</span></div>
        <ExperimentScores report={run.report} />
      </article>)}
    </div> : !error && <p className="experiment-empty">暂无该阶段实验。到评测工作台启动实验后，记录会保存在这里。</p>}
  </div>;
}
