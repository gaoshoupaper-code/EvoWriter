import { useParams } from "react-router-dom";
import { ComparisonReport, formatCaseCoverage } from "@/components/bench/ExperimentEvidence";
import { publishExperiment, restoreExperiment, type Experiment } from "@/lib/api";
import { EXPERIMENT_STATUS, experimentTaskLabel } from "@/lib/experiment-types";
import { hasExperimentReview } from "@/lib/experiment-timeline";
import { ExperimentDetailPage, useExperimentPage } from "./ExperimentDetailPage";

const publishLabels: Record<string, string> = {
  not_started: "尚未发布", publishing: "发布中", activated: "已生效", failed: "发布失败",
  unconfirmed: "加载尚未确认", restored: "已恢复旧版",
};
const restoreLabels: Record<string, string> = { restoring: "恢复中", restored: "已确认恢复", failed: "恢复失败" };

export default function ExperimentReview() {
  const { experimentId = "" } = useParams();
  return <ReviewContent key={experimentId} id={experimentId} />;
}

function ReviewContent({ id }: { id: string }) {
  const { experiment, error, retry, busy, operate } = useExperimentPage(id);
  return <ExperimentDetailPage id={id} title="实验审核" anchor="experiment-review"
    loading={!experiment} error={error} busy={busy} retry={retry}>
    {experiment && (hasExperimentReview(experiment)
      ? <ReviewResult experiment={experiment} busy={busy}
        publish={() => void operate(publishExperiment)} restore={() => void operate(restoreExperiment)} />
      : <section><p>暂无可审核的候选。</p>{experiment.end_reason && <p>{experiment.end_reason}</p>}</section>)}
  </ExperimentDetailPage>;
}

function ReviewResult({ experiment, busy, publish, restore }: {
  experiment: Experiment; busy: boolean; publish: () => void; restore: () => void;
}) {
  const candidate = experiment.best_candidate;
  const round = experiment.rounds?.find(item => item.round === candidate?.round);
  const report = experiment.report;
  const optimization = round?.report?.baseline ? round.report
    : report.exploration_round == null || report.exploration_round === candidate?.round ? report.exploration : undefined;
  const regressions = round?.regressions
    ?? (report.regression_round == null || report.regression_round === candidate?.round ? report.regressions : undefined);
  const composite = !!experiment.runtime_scoring_version;
  const assessment = experiment.task_type === "chapter_text" && experiment.assessment_version === 1;
  const status = experiment.publish_status;
  const publishInProgress = status === "publishing" || experiment.status === "publishing";
  const restoring = experiment.rollback_status === "restoring";
  const retryPublish = experiment.status === "publish_failed";
  const publishText = assessment ? (retryPublish ? "重试启用" : "启用达标版本")
    : (retryPublish ? "重试发布并核对加载" : "批准并发布候选");
  return <>
    <header className="record-heading"><h2>{experiment.name}</h2>
      <p>{EXPERIMENT_STATUS[experiment.status] ?? experiment.status}</p></header>
    <section aria-label="待审核候选"><h2>待审核候选</h2>
      {candidate ? <dl className="record-facts">
        <div><dt>候选</dt><dd>第 {candidate.round} 轮</dd></div>
        <div><dt>版本</dt><dd><code>{candidate.commit}</code></dd></div>
      </dl> : <p className="experiment-note">候选版本未记录。</p>}
      {optimization?.baseline && optimization.candidate && <ComparisonReport title="优化复测" report={optimization} composite={composite} />}
      {Object.entries(regressions ?? {}).map(([kind, comparison]) =>
        <ComparisonReport key={kind} title={experimentTaskLabel(kind as Experiment["task_type"]) + "回归"} report={comparison} composite={composite} />)}
      {report.validation && <ComparisonReport title="独立终验" report={report.validation} composite={composite} />}
    </section>
    <section aria-label="发布与加载"><h2>发布与加载</h2>
      <p><strong>{publishInProgress ? "发布中" : publishLabels[status] ?? status ?? "尚未发布"}</strong></p>
      {experiment.case_coverage && <p id="publish-case-coverage" className="experiment-note">{formatCaseCoverage(experiment.case_coverage)}</p>}
      {experiment.loaded_revision && <p>执行端已确认版本 <code>{experiment.loaded_revision}</code></p>}
      {experiment.stale_reason && <p role="status">{experiment.stale_reason}</p>}
      {experiment.load_error && <p role="alert" className="experiment-error">{experiment.load_error}</p>}
      {experiment.rollback_status && <p>恢复旧版 · {restoreLabels[experiment.rollback_status] ?? experiment.rollback_status}</p>}
      <div className="experiment-actions">
        {experiment.can_publish && <button type="button" className="config-button primary"
          aria-describedby={experiment.case_coverage ? "publish-case-coverage" : undefined}
          disabled={busy || publishInProgress || restoring} onClick={publish}>{publishText}</button>}
        {experiment.can_restore && <button type="button" className="config-button"
          disabled={busy || publishInProgress || restoring} onClick={restore}>恢复旧版</button>}
      </div>
    </section>
  </>;
}
