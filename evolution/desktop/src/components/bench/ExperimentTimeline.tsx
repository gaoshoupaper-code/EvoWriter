import type { MouseEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import type { ActiveRun, Experiment } from "@/lib/api";
import { EXPERIMENT_STATUS, PHASE_LABELS, executionPhaseLabel, experimentTaskLabel,
  type ExperimentComparison, type ExperimentSample } from "@/lib/experiment-types";
import { experimentTimeline, evaluationFinished, type ExperimentTimelineRow } from "@/lib/experiment-timeline";
import { sampleActivitySection } from "@/lib/experiment-record-traces";
import { mergeTraceDisplay, traceDisplayContext, type TraceIdentityRun } from "@/lib/trace-display";
import "./experiment-timeline.css";

type Props = {
  experiment: Experiment;
  liveRuns: ActiveRun[];
  onOpenRecord: (anchor: string) => void;
};
const labels = { writing: "运行", evaluation: "评测", improvement: "进化" };

function scoringState(sample: ExperimentSample) {
  if (sample.status === "scoring") return "评分中";
  if (sample.status === "evaluation_error") return sample.score_retry_at ? "等待重试评分" : "评分异常";
  if (evaluationFinished(sample)) return sample.status === "failed" ? "评分失败" : sample.status === "stopped" ? "评分已停止" : "已评分";
  if (!sample.completed && sample.status === "failed") return "生成失败，未评测";
  if (!sample.completed && sample.status === "stopped") return "生成已停止，未评测";
  return "等待评测";
}

export function ExperimentTimeline({ experiment, liveRuns, onOpenRecord }: Props) {
  const location = useLocation();
  const rows = experimentTimeline(experiment);
  const judges = experiment.models?.judges?.map(model => model.model || model.name).filter(Boolean).join("、") || "未记录";
  function linkProps(path: string, anchor: string) {
    return {
      to: path + location.search,
      state: { fromRecords: true, returnTo: location.pathname + location.search, returnAnchor: anchor },
      onClick: (event: MouseEvent<HTMLAnchorElement>) => {
        if (event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) onOpenRecord(anchor);
      },
    };
  }
  const base = "/bench/experiments/" + encodeURIComponent(experiment.id);
  function record(sample: ExperimentSample, kind: "writing" | "evaluation") {
    const anchor = `record-${kind}-${sample.id}`;
    const live = liveRuns.find(run => sampleActivitySection(run, experiment.id, sample.id) === "writing");
    const traces = sample.trace_ids.map(id => mergeTraceDisplay<TraceIdentityRun>(
      experiment.trace_runs?.[id] ?? { trace_id: id }, liveRuns.find(run => run.trace_id === id)));
    const trace = traces.find(run => run.started_at) ?? live ?? traces[0] ?? {};
    const title = sample.package_title?.split(/\s+·\s+/, 1)[0].trim() || "未命名作品";
    const phase = traceDisplayContext(trace, { phase: sample.phase }).phase ?? sample.phase;
    const purpose = phase === "baseline" ? "原版基线" : PHASE_LABELS[phase] ?? phase;
    const date = trace.started_at ? new Date(trace.started_at) : null;
    const time = date && !Number.isNaN(date.getTime()) ? date : null;
    const version = sample.arm === "baseline" ? "固定原版" : `候选改版 ${sample.round}`;
    const started = sample.status !== "pending" || sample.trace_ids.length > 0 || !!live;
    const available = kind === "writing" ? started : evaluationFinished(sample);
    const score = sample.scores?.overall;
    const content = kind === "writing" ? title : <>{title} · 评分
      <strong className="timeline-score">{score == null ? scoringState(sample) : `${score.toFixed(2)} / 5`}</strong></>;
    return <li id={anchor} key={sample.id} tabIndex={-1} className={kind === "writing" ? "timeline-run-record" : undefined}>
      <div className="timeline-record-heading">
        {available ? <Link {...linkProps(base + "/records/" + encodeURIComponent(sample.id) + "/" + kind, anchor)}>{content}</Link>
          : <span>{content}</span>}
        {kind === "writing" && <div className="timeline-run-meta">
          {time && <time dateTime={trace.started_at ?? undefined}>{time.toLocaleString("zh-CN", {
            year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
          })}</time>}
          <span>{purpose}</span>
        </div>}
        {kind === "writing" && !sample.completed && <span className="timeline-record-status">{EXPERIMENT_STATUS[sample.status] ?? sample.status}</span>}
        {kind === "evaluation" && score != null && <span className="timeline-record-status">{scoringState(sample)}</span>}
      </div>
      {kind === "evaluation" && <p>{version} · 第{sample.repeat}次重复 · 评测模型 {judges}</p>}
      {kind === "writing" && sample.execution_phase && !sample.completed &&
        <p>{executionPhaseLabel(sample.execution_phase)}</p>}
    </li>;
  }
  function comparison(row: ExperimentTimelineRow) {
    const round = experiment.rounds.find(item => item.round === row.round);
    const final = experiment.report;
    const matchesRound = (recorded?: number) => (recorded ?? experiment.current_round) === row.round;
    let report: Partial<ExperimentComparison> | undefined;
    if (row.phase === "exploration") {
      report = round?.report?.baseline ? round.report : matchesRound(final.exploration_round) ? final.exploration : undefined;
    }
    if (row.phase === "validation" && matchesRound(final.validation_round)) report = final.validation;
    const regressions = round?.regressions ?? (matchesRound(final.regression_round) ? final.regressions : undefined);
    const reports: [string, Partial<ExperimentComparison> | undefined][] = row.phase === "regression"
      ? Object.entries(regressions ?? {}) : [[experiment.task_type, report]];
    return reports.map(([task, result]) => {
      if (!result?.baseline || !result.candidate) return null;
      const query = new URLSearchParams(location.search);
      query.set("round", String(row.round));
      query.set("phase", row.phase);
      query.set("task", task);
      const anchor = `comparison-${row.phase}-${row.round}` + (task === experiment.task_type ? "" : "-" + task);
      return <div className="timeline-comparison" id={anchor} key={task}>
        {task !== experiment.task_type && <span>{experimentTaskLabel(task as Experiment["task_type"])}</span>}
        <strong className={result.passed ? "experiment-positive" : "experiment-error"}>{result.passed ? "通过" : "未达标"}</strong>
        <span>原版 {result.baseline.quality?.toFixed(2) ?? "未评分"} → 候选 {result.candidate.quality?.toFixed(2) ?? "未评分"}</span>
        {result.quality_delta != null && <span>变化 {result.quality_delta > 0 ? "+" : ""}{result.quality_delta.toFixed(2)}</span>}
        <Link to={base + "/comparison?" + query} state={{ fromRecords: true, returnAnchor: anchor }} onClick={() => onOpenRecord(anchor)}>
          {task === "chapter_text" ? "正文对比" : "查看对比详情"}</Link>
      </div>;
    });
  }
  return <section className="experiment-timeline" aria-label="实验流程">
    {!rows.length && <p className="experiment-note">正在准备实验记录。</p>}
    {rows.map(row => {
      const name = `${labels[row.kind]} · 第 ${row.number} 轮`;
      const improvement = row.improvement;
      const anchor = "record-improvement-" + row.round;
      return <section key={row.key} className={"timeline-stage " + row.kind} aria-label={name}>
        <div className="timeline-stage-label"><h2>{labels[row.kind]}</h2><span>第 {row.number} 轮</span></div>
        <div className="timeline-stage-content">
          {improvement ? <article id={anchor} tabIndex={-1}>
            <div className="timeline-stage-heading"><Link {...linkProps(base + "/rounds/" + row.round, anchor)}>Harness · 第 {row.round} 次改版</Link>
              <span>{improvement.status === "evolving" ? "进化中" : improvement.status === "failed" ? "进化失败" : "进化完成"}</span></div>
            <p className="timeline-improvement-summary">{experiment.can_read_content
              ? improvement.error || improvement.summary || (improvement.status === "evolving" ? "正在改进 Harness" : "查看进化详情")
              : "查看进化记录，完整内容需读取权限。"}</p>
          </article> : <>
            <ul className="timeline-records">{row.samples.map(sample => record(sample, row.kind as "writing" | "evaluation"))}</ul>
            {row.kind === "evaluation" && comparison(row)}
          </>}
        </div>
      </section>;
    })}
  </section>;
}
