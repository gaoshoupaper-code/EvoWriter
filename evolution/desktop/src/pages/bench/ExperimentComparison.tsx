import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ChapterComparison } from "@/components/bench/ChapterComparison";
import { ExperimentArtifacts } from "@/components/bench/ExperimentArtifacts";
import { ComparisonReport } from "@/components/bench/ExperimentEvidence";
import { PHASE_LABELS, TASK_LABELS, TASK_TYPES, type ExperimentSample } from "@/lib/experiment-types";
import { pageAgent, withAgent } from "@/lib/writing-agent";
import { ExperimentDetailPage, useExperimentPage } from "./ExperimentDetailPage";

export default function ExperimentComparison() {
  const { experimentId = "" } = useParams();
  const { search } = useLocation();
  return <ComparisonContent key={experimentId + search} id={experimentId} search={search} />;
}

function ComparisonContent({ id, search }: { id: string; search: string }) {
  const { experiment, retry, error } = useExperimentPage(id);
  const navigate = useNavigate();
  const query = new URLSearchParams(search);
  const requestedRound = Number(query.get("round")) || null;
  const requestedPhase = query.get("phase");
  const phase = requestedPhase === "regression" || requestedPhase === "validation" ? requestedPhase : "exploration";
  const regressionTask = phase === "regression" ? TASK_TYPES.find(task => task === query.get("task")) : undefined;
  const round = experiment?.rounds?.find(item => item.round === requestedRound);
  const report = experiment?.report;
  const belongsToRound = (reportRound?: number) => requestedRound == null || reportRound === requestedRound;
  const regressions = {
    ...(belongsToRound(report?.regression_round ?? experiment?.current_round) ? report?.regressions : undefined),
    ...round?.regressions,
  };
  const comparison = phase === "validation"
    ? (belongsToRound(report?.validation_round ?? experiment?.current_round) ? report?.validation : undefined)
    : round?.report?.baseline ? round.report
    : belongsToRound(report?.exploration_round ?? experiment?.current_round) ? report?.exploration : undefined;
  const sampleTask = (sample: ExperimentSample) => sample.task_type ?? experiment?.task_type;
  const task = phase === "regression" ? regressionTask : experiment?.task_type;
  const samples = experiment?.samples.filter(sample => (!task || sampleTask(sample) === task) && (
    !requestedPhase || sample.phase === phase || (phase === "exploration" && sample.phase === "baseline")
  )) ?? [];
  const batch = samples.filter(sample => !requestedRound || sample.round === requestedRound || sample.phase === "baseline");
  const ownChapterComparison = experiment?.task_type === "chapter_text" && (!task || task === "chapter_text");
  const hasChapterComparison = ownChapterComparison || (
    !!experiment?.rounds.some(item => !requestedRound || item.round === requestedRound)
    && batch.some(sample => sampleTask(sample) === "chapter_text" && sample.target?.chapter)
  );
  const otherArtifacts = hasChapterComparison ? batch.filter(sample => sampleTask(sample) !== "chapter_text"
    || (experiment?.can_read_content && !sample.target?.chapter && Object.keys(sample.artifacts ?? {}).length)) : batch;
  const suffix = experiment && regressionTask && regressionTask !== experiment.task_type ? "-" + regressionTask : "";
  const anchor = requestedRound ? `comparison-${phase}-${requestedRound}${suffix}` : undefined;
  return <ExperimentDetailPage id={id} title="对比详情" anchor={anchor} loading={!experiment} error={error} retry={retry}>
    {experiment && <>
      <header className="record-heading"><h2>{experiment.name}</h2>
        {requestedRound && <p>{PHASE_LABELS[phase]} · 候选第 {requestedRound} 轮</p>}</header>
      {phase === "regression" ? TASK_TYPES.filter(kind => !regressionTask || kind === regressionTask).map(kind => {
        const regression = regressions[kind];
        return regression && <ComparisonReport key={kind} title={TASK_LABELS[kind] + "回归"} report={regression} />;
      }) : comparison?.baseline && comparison.candidate
        && <ComparisonReport title={PHASE_LABELS[phase]} report={comparison} />}
      {hasChapterComparison && <ChapterComparison experiment={{ ...experiment,
          rounds: experiment.can_read_content ? experiment.rounds : experiment.rounds.map(item => ({ ...item, trace_ids: [] })),
          samples: samples.filter(sample => sampleTask(sample) === "chapter_text").map(sample => ({ ...sample, artifacts: sample.artifacts ?? {},
            trace_ids: experiment.can_read_content ? sample.trace_ids ?? [] : [],
            evaluation_trace_ids: experiment.can_read_content ? sample.evaluation_trace_ids : [] })),
        }} initialRound={round?.round ?? null}
          onTrace={traceId => {
            if (experiment.can_read_content) {
              navigate(withAgent("/traces/" + encodeURIComponent(traceId) + search, pageAgent("evaluation", search)));
            }
          }} />}
      {(!hasChapterComparison || otherArtifacts.length !== 0) && <section aria-label="对比产物">
          <h2>产物文件</h2><ExperimentArtifacts samples={otherArtifacts} canReadContent={experiment.can_read_content} />
        </section>}
    </>}
  </ExperimentDetailPage>;
}
