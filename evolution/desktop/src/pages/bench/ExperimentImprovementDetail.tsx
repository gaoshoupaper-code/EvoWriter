import { useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ImprovementSources } from "@/components/bench/ChapterComparison";
import { ExperimentChangeDiff } from "@/components/bench/ExperimentChangeDiff";
import { ExperimentVersionFiles } from "@/components/bench/ExperimentVersionFiles";
import { useTracePolling } from "@/hooks/useTraceStream";
import type { ActiveRun } from "@/lib/api";
import { EXPERIMENT_STATUS, FAILURE_REVIEW_DECISION_LABELS } from "@/lib/experiment-types";
import { improvementActivity } from "@/lib/improvement-progress";
import { pageAgent, withAgent } from "@/lib/writing-agent";
import { ExperimentDetailPage, ExperimentRecordTrace, experimentRunning, useExperimentActivity, useExperimentPage } from "./ExperimentDetailPage";

function matchesImprovement(run: ActiveRun, experimentId: string, round: number) {
  const refs = run.external_refs;
  return run.run_purpose === "evolution_evolve" && Number(refs?.round) === round
    && (run.experiment_id ?? refs?.experiment_id) === experimentId
    && (!refs?.experiment_id || refs.experiment_id === experimentId)
    && (!refs?.phase || refs.phase === "improvement")
    && (!run.display_context?.operation || run.display_context.operation === "improvement");
}

export default function ExperimentImprovementDetail() {
  const { experimentId = "", round = "" } = useParams();
  return <ImprovementContent key={experimentId + "/" + round} id={experimentId} round={Number(round)} />;
}

function ImprovementContent({ id, round }: { id: string; round: number }) {
  const { experiment, retry, error } = useExperimentPage(id);
  const { search } = useLocation();
  const navigate = useNavigate();
  const [showLog, setShowLog] = useState(false);
  const entry = experiment?.rounds.find(item => item.round === round);
  const awaitingTrace = !!(experiment && experimentRunning(experiment) && experiment.phase === "evolving"
    && experiment.current_round === round && entry?.status === "evolving");
  const canRead = !!experiment?.can_read_content;
  const track = canRead && !!experiment && experimentRunning(experiment) && entry?.status === "evolving";
  const { runs, error: liveError } = useExperimentActivity(track);
  const live = track ? runs.filter(run => matchesImprovement(run, id, round)) : [];
  const waiting = awaitingTrace || !!live.length;
  const traceId = canRead && waiting && !showLog ? live[0]?.trace_id ?? entry?.trace_ids?.at(-1) ?? null : null;
  const { detail, isStale } = useTracePolling(traceId);
  const activity = traceId && detail?.run.trace_id === traceId ? improvementActivity(detail.nodes) : "";
  const review = canRead ? entry?.failure_review : undefined;
  const rationale = canRead ? entry?.design?.replace(/^# 改动设计文档\s*\n/, "").trim() : undefined;
  const references = [...new Set([...(review?.evidence_refs ?? []), ...(entry?.evidence_samples ?? [])])];
  const hasSources = canRead && experiment?.samples?.some(sample => references.includes(sample.id)
    && experiment.settings?.optimization_ids?.includes(sample.package_id)
    && ["baseline", "exploration"].includes(sample.phase));
  const noChange = entry?.outcome === "no_change";
  const hasDiff = canRead && !!entry?.diff?.trim();
  const failure = entry?.startup_check?.error || entry?.error;
  const baseIsOriginal = !!entry?.base_commit && entry.base_commit === experiment?.baseline.commit;
  const baseRound = experiment?.rounds.find(item => item.round < round && item.source_commit === entry?.base_commit)?.round;
  const start = baseIsOriginal ? `原版${experiment?.baseline.version ? ` v${experiment.baseline.version}` : ""}`
    : baseRound ? `第 ${baseRound} 轮候选` : entry?.base_commit?.slice(0, 8);
  const hasTrace = !!(live.length || entry?.trace_ids?.length);
  const archivedFiles = experiment?.task_type === "chapter_text" && (experiment.assessment_version ?? 0) >= 1;
  const title = Number.isInteger(round) && round > 0 ? `第 ${round} 轮进化` : "进化详情";
  return <ExperimentDetailPage id={id} title={title} anchor={"record-improvement-" + round}
    loading={!experiment} error={error} retry={retry}>
    {experiment && (!entry ? <p role="alert">本实验中没有这轮进化记录。</p> : <>
      <header className="record-heading improvement-heading">
        <p>{experiment.name}{start && <> · 起点 {start}</>}</p>
        <span className="improvement-state">{noChange ? "无改动结束" : entry.status === "generation_failed"
          ? "候选生成失败" : EXPERIMENT_STATUS[entry.status] ?? "已记录"}</span>
      </header>
      <section aria-labelledby="improvement-progress-heading">
        <h2 id="improvement-progress-heading">执行过程</h2>
        <ol className="improvement-steps">
          {waiting && !showLog && <li>
            <div className="improvement-step-label"><h3>当前进度</h3></div>
            <p role="status">{experiment.status === "stopping" ? "正在停止"
              : experiment.status === "recovering" ? "正在恢复进化"
              : isStale || (liveError && !activity) ? "进度暂未更新"
              : activity || "等待执行进度"}</p>
          </li>}
          {review ? <li>
            <div className="improvement-step-label"><h3>复盘第 {review.round} 轮</h3>
              <span>{FAILURE_REVIEW_DECISION_LABELS[review.decision]}</span></div>
            <div><p className="experiment-prewrap">{review.reason}</p>
              <p className="improvement-source">{review.manuscript_location}</p>
              {review.new_evidence && review.new_evidence !== review.reason &&
                <p className="experiment-prewrap">新证据 · {review.new_evidence}</p>}
            </div>
          </li> : null}
          {canRead && (hasSources || rationale) && <li>
            <div className="improvement-step-label"><h3>{rationale ? "改动方案" : "证据"}</h3></div>
            <details className="experiment-improvement-disclosure">
              <summary>{rationale && hasSources ? "查看方案与证据" : rationale ? "查看方案" : "改进依据"}</summary>
              {rationale && <p className="experiment-prewrap">{rationale}</p>}
              {hasSources && <ImprovementSources experiment={experiment} references={references}
                onTrace={sourceId => navigate(withAgent("/traces/" + encodeURIComponent(sourceId) + search, pageAgent("evaluation", search)))} />}
            </details>
          </li>}
          {hasDiff && <li>
            <div className="improvement-step-label"><h3>实际改动</h3></div>
            <ExperimentChangeDiff diff={entry.diff} />
          </li>}
          {entry.startup_check && <li>
            <div className="improvement-step-label"><h3>启动检查</h3></div>
            <p>{entry.startup_check.status === "passed" ? "通过" : "失败"}</p>
          </li>}
          {noChange && !review && canRead && entry.summary?.trim() && <li>
            <div className="improvement-step-label"><h3>结束原因</h3></div><p className="experiment-prewrap">{entry.summary}</p>
          </li>}
        </ol>
        {failure && <p role="alert" className="experiment-error">{canRead
          ? failure : "本轮异常。查看完整原因需要正文读取权限。"}</p>}
        {!canRead && !failure && <p className="experiment-note">查看执行详情需要正文读取权限。</p>}
        {canRead && !waiting && !review && !rationale && !hasSources && !hasDiff && !entry.startup_check && !noChange && !failure &&
          <p className="experiment-note">本轮未保存过程摘要。</p>}
      </section>
      {canRead && (waiting || hasTrace) && <section aria-label="运行日志">
        <details className="experiment-improvement-disclosure" onToggle={event => setShowLog(event.currentTarget.open)}>
          <summary>运行日志</summary>
          {showLog && <ExperimentRecordTrace title="进化记录" prioritizeModelIO experiment={experiment} waiting={waiting} unavailable={liveError}
            archived={entry.trace_ids} live={live} context={{ experiment_name: experiment.name, task_type: experiment.task_type,
              operation: "improvement", phase: "improvement", round }} />}
        </details>
      </section>}
      {canRead && (entry.base_commit || entry.source_commit) && <section aria-label="版本文件">
        <details className="experiment-improvement-disclosure">
          <summary>版本文件</summary>
          <dl className="record-facts">
            {entry.base_commit && <div><dt>起始版本</dt><dd><code>{entry.base_commit}</code></dd></div>}
            {entry.source_commit && entry.source_commit !== entry.base_commit &&
              <div><dt>本轮版本</dt><dd><code>{entry.source_commit}</code></dd></div>}
          </dl>
          {archivedFiles && <>
            {experiment.baseline.commit && <VersionFiles id={id} round={0}
              commit={experiment.baseline.commit} label="原版文件" />}
            {baseRound && <VersionFiles id={id} round={baseRound} commit={entry.base_commit} label="起点文件" />}
            {entry.source_commit && entry.source_commit !== entry.base_commit &&
              <VersionFiles id={id} round={round} commit={entry.source_commit} label="本轮文件" />}
          </>}
        </details>
      </section>}
    </>)}
  </ExperimentDetailPage>;
}

function VersionFiles({ id, round, commit, label }: { id: string; round: number; commit: string; label: string }) {
  const [open, setOpen] = useState(false);
  return <div className="record-version-files">
    <button type="button" className="action-link" aria-expanded={open}
      onClick={() => setOpen(current => !current)}>{open ? "收起" + label : "查看" + label}</button>
    {open && <ExperimentVersionFiles key={commit} id={id} round={round} commit={commit} />}
  </div>;
}
