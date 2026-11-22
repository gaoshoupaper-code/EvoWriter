import PageAgentSelector from "@/components/PageAgentSelector";
import { currentTask, withAgent } from "@/lib/writing-agent";
import "@/components/bench/experiment-observation.css";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useExperiments } from "@/hooks/useExperiments";
import { getExperiment } from "@/lib/api";
import { EXPERIMENT_STATUS, PHASE_LABELS, experimentTaskLabel, type Experiment, type ExperimentSummary } from "@/lib/experiment-types";
import { ChapterComparison } from "@/components/bench/ChapterComparison";
import { ExperimentScores, formatCaseCoverage } from "@/components/bench/ExperimentEvidence";
import "@/components/bench/experiments.css";

function scoringDetailsPath(run: ExperimentSummary) {
  const query = new URLSearchParams({ scoring: run.id });
  if (run.current_round > 0) query.set("round", String(run.current_round));
  if (run.phase === "exploration" || run.phase === "regression") query.set("phase", run.phase);
  if (run.phase === "regression") query.set("task", "chapter_text");
  return withAgent("/bench/experiments?" + query);
}

export default function ExperimentRecords() {
  const [query] = useSearchParams();
  const scoringId = query.get("scoring");
  if (scoringId && currentTask() === "chapter_text") {
    const round = Number(query.get("round")) || null;
    const phase = query.get("phase");
    return <ChapterScoringRecord key={query.toString()} id={scoringId} round={round}
      phase={phase === "exploration" || phase === "regression" ? phase : null} />;
  }
  return <ExperimentRecordList />;
}

function ChapterScoringRecord({ id, round, phase }: {
  id: string; round: number | null; phase: "exploration" | "regression" | null;
}) {
  const navigate = useNavigate();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    getExperiment(id).then((record) => {
      if (!alive) return;
      if (record.task_type === "chapter_text") setExperiment(record);
      else setError("请选择正文实验记录。");
    }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : "读取评分明细失败"); });
    return () => { alive = false; };
  }, [id, attempt]);

  const samples = experiment?.samples.filter((sample) => {
    if (sample.task_type !== "chapter_text") return false;
    if (sample.phase === "baseline" && phase !== "regression") return true;
    return (!phase || sample.phase === phase) && (round == null || sample.round === round);
  });
  const rounds = experiment?.rounds.filter((item) => round == null || item.round === round);
  return <div className="bench-page experiment-scoring-page">
    <header className="page-header"><Link to={withAgent("/bench/experiments")} className="action-link">返回实验记录</Link>
      <h1>评分明细</h1>{experiment && <p className="page-desc">{experiment.name}</p>}</header>
    <PageAgentSelector />
    {experiment && samples && rounds ? <ChapterComparison experiment={{ ...experiment,
      rounds: experiment.can_read_content ? rounds : rounds.map((item) => ({ ...item, trace_ids: [] })),
      samples: experiment.can_read_content ? samples : samples.map((sample) => ({ ...sample, trace_ids: [], evaluation_trace_ids: [] })),
    }} initialRound={rounds.length ? round : null} onTrace={(traceId) => {
      if (experiment.can_read_content) navigate(withAgent("/traces/" + encodeURIComponent(traceId)));
    }} /> : error ? <><p role="alert" className="experiment-error">{error}</p>
      <button className="config-button" type="button" onClick={() => { setError(""); setAttempt((value) => value + 1); }}>重试</button>
    </> : <p role="status">正在读取评分明细…</p>}
  </div>;
}

function ExperimentRecordList() {
  const { runs, loading, error, refresh } = useExperiments();

  const task = currentTask();
  const visible = runs.filter((run) => run.task_type === task && run.flow_version === 3);
  return <div className="bench-page experiment-records-page">
    <header className="page-header experiment-heading"><div><h1>实验记录</h1>
      <p className="page-desc">一次实验，一份完整记录。按写作阶段整理，最新创建的实验在前。</p></div>
      <Link className="config-button" to="/bench">前往工作台</Link></header>
    <PageAgentSelector />
    {error && <p role="alert" className="experiment-error">更新失败：{error} <button className="action-link" type="button" onClick={refresh}>重试</button></p>}
    {loading ? <p role="status">正在加载实验记录…</p> : visible.length ? <div className="experiment-record-list">
      {visible.map((run) => <article className="experiment-record" key={run.id}>
        <div className="experiment-record-main">
          <h2><Link className="experiment-run-name" to={"/bench/experiments/" + encodeURIComponent(run.id)}>{run.name}</Link></h2>
          <div className="experiment-record-status">
            <span className={"experiment-state " + run.status}>{EXPERIMENT_STATUS[run.status] ?? run.status}</span>
            {run.phase && <strong>{PHASE_LABELS[run.phase] ?? run.phase}</strong>}
          </div>
          <span className="experiment-cell-note">{experimentTaskLabel(run.task_type, run.flow_version)} · 原版 v{run.baseline.version} · 第 {run.current_round} / {run.max_rounds} 轮</span>
          {run.case_coverage && <span className="experiment-cell-note">{formatCaseCoverage(run.case_coverage)}</span>}
          <time className="experiment-cell-note" dateTime={run.created_at}>创建于 {new Date(run.created_at).toLocaleString("zh-CN", { hour12: false })}</time>
        </div>
        <div className="experiment-record-results">
          <ExperimentScores report={run.report} task={task} />
          {run.task_type === "chapter_text" && run.judge_repeats != null && <Link className="action-link"
            to={scoringDetailsPath(run)}>查看评分明细</Link>}
        </div>
      </article>)}
    </div> : !error && <p className="experiment-empty">暂无该阶段实验。到评测工作台启动实验后，记录会保存在这里。</p>}
  </div>;
}
