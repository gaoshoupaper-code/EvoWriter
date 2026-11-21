import { useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ImprovementSources } from "@/components/bench/ChapterComparison";
import { ExperimentChangeDiff } from "@/components/bench/ExperimentChangeDiff";
import { ExperimentVersionFiles } from "@/components/bench/ExperimentVersionFiles";
import type { ActiveRun } from "@/lib/api";
import { EXPERIMENT_STATUS } from "@/lib/experiment-types";
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
  const entry = experiment?.rounds.find(item => item.round === round);
  const waiting = !!(experiment && experimentRunning(experiment) && experiment.phase === "evolving"
    && experiment.current_round === round && entry?.status === "evolving");
  const track = !!(experiment?.can_read_content && entry && experimentRunning(experiment));
  const { runs, error: liveError } = useExperimentActivity(track);
  const live = track ? runs.filter(run => matchesImprovement(run, id, round)) : [];
  const archivedFiles = experiment?.task_type === "chapter_text" && (experiment.assessment_version ?? 0) >= 1;
  return <ExperimentDetailPage id={id} title="进化详情" anchor={"record-improvement-" + round}
    loading={!experiment} error={error} retry={retry}>
    {experiment && (!entry ? <p role="alert">本实验中没有这轮进化记录。</p> : <>
      <header className="record-heading"><h2>候选第 {round} 轮</h2>
        <p>{experiment.name} · {EXPERIMENT_STATUS[entry.status] ?? entry.status}</p></header>
      <section aria-labelledby="improvement-changes-heading">
        <h2 id="improvement-changes-heading">实际改动</h2>
        {experiment.can_read_content
          ? entry.diff?.trim() ? <ExperimentChangeDiff diff={entry.diff} />
            : <p className="experiment-note" role={waiting ? "status" : undefined}>
              {waiting ? "进化中，等待保存文件改动。" : "本轮没有已保存的文件差异。"}
            </p>
          : <p className="experiment-note">查看文件改动需要正文读取权限。</p>}
      </section>
      {entry.error && <p role="alert" className="experiment-error">{experiment.can_read_content
        ? entry.error : "本轮异常。查看完整原因需要正文读取权限。"}</p>}
      <section aria-label="进化运行详情">
        <details className="experiment-improvement-disclosure">
          <summary>进化运行详情</summary>
          <ExperimentRecordTrace title="进化记录" prioritizeModelIO experiment={experiment} waiting={waiting} unavailable={liveError}
            archived={entry.trace_ids} live={live} context={{ experiment_name: experiment.name, task_type: experiment.task_type,
              operation: "improvement", phase: "improvement", round }} />
        </details>
      </section>
      {experiment.can_read_content && (entry.design || !!entry.evidence_samples?.length) && <section aria-label="改进依据">
        <details className="experiment-improvement-disclosure">
          <summary>改进依据</summary>
          {entry.design && <p className="experiment-prewrap">{entry.design}</p>}
          {experiment.settings && <ImprovementSources experiment={experiment} references={entry.evidence_samples ?? []}
            onTrace={traceId => navigate(withAgent("/traces/" + encodeURIComponent(traceId) + search, pageAgent("evaluation", search)))} />}
        </details>
      </section>}
      <section aria-label="Harness 版本">
        <h2>Harness 版本</h2>
        <dl className="record-facts">
          <div><dt>起始版本</dt><dd>{entry.base_commit ? <code>{entry.base_commit}</code> : "未记录"}</dd></div>
          <div><dt>本轮版本</dt><dd>{entry.source_commit ? <code>{entry.source_commit}</code> : "尚无版本标识"}</dd></div>
          <div><dt>启动检查</dt><dd>{entry.startup_check ? entry.startup_check.status === "passed" ? "通过" : "失败" : "尚无结果"}</dd></div>
        </dl>
        {experiment.can_read_content && entry.startup_check?.error && entry.startup_check.error !== entry.error
          && <p role="alert" className="experiment-error">{entry.startup_check.error}</p>}
        {experiment.can_read_content && archivedFiles && <>
          {experiment.baseline.commit && <VersionFiles id={id} round={0} commit={experiment.baseline.commit} />}
          {entry.source_commit && <VersionFiles id={id} round={round} commit={entry.source_commit} />}
        </>}
        {!experiment.can_read_content && <p className="experiment-note">查看版本文件需要正文读取权限。</p>}
      </section>
    </>)}
  </ExperimentDetailPage>;
}

function VersionFiles({ id, round, commit }: { id: string; round: number; commit: string }) {
  const [open, setOpen] = useState(false);
  const label = round ? "本轮文件" : "原版文件";
  return <div className="record-version-files">
    <button type="button" className="action-link" aria-expanded={open}
      onClick={() => setOpen(current => !current)}>{open ? "收起" + label : "查看" + label}</button>
    {open && <ExperimentVersionFiles key={commit} id={id} round={round} commit={commit} />}
  </div>;
}
