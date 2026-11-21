import type { Experiment, ExperimentSample } from "./experiment-types";

export type ExperimentTimelineRow = {
  kind: "writing" | "evaluation" | "improvement";
  number: number;
  key: string;
  phase: string;
  round: number;
  samples: ExperimentSample[];
  improvement?: Experiment["rounds"][number];
};

export function experimentTimeline(experiment: Pick<Experiment, "samples" | "rounds">) {
  const batches = new Map<string, ExperimentSample[]>();
  for (const sample of experiment.samples) {
    const key = `${sample.round}:${sample.phase}`;
    const batch = batches.get(key) ?? [];
    batch.push(sample);
    batches.set(key, batch);
  }
  const rows: ExperimentTimelineRow[] = [];
  let number = 0;
  function addBatch(key: string, samples: ExperimentSample[]) {
    number += 1;
    const { phase, round } = samples[0];
    for (const kind of ["writing", "evaluation"] as const) {
      rows.push({ kind, number, key: `${kind}:${key}`, phase, round, samples });
    }
    batches.delete(key);
  }
  for (const [key, samples] of batches) {
    if (samples[0].phase === "baseline") addBatch(key, samples);
  }
  const rounds = [...new Set([
    ...experiment.rounds.map(item => item.round),
    ...Array.from(batches.values(), samples => samples[0].round),
  ])].sort((a, b) => a - b);
  for (const round of rounds) {
    const improvement = experiment.rounds.find(item => item.round === round);
    if (improvement) rows.push({ kind: "improvement", number: Math.max(1, number),
      key: `improvement:${round}`, phase: "evolving", round, samples: [], improvement });
    // The service appends the actual batches in execution order, including legacy validation.
    for (const [key, samples] of batches) {
      if (samples[0].round === round) addBatch(key, samples);
    }
  }
  return rows;
}

export function evaluationFinished(sample: ExperimentSample) {
  if (["pending", "running", "recovering", "interrupted", "stopping", "scoring", "evaluation_error"].includes(sample.status)) return false;
  return sample.status === "completed" || (
    ["failed", "stopped"].includes(sample.status) && (sample.completed || !!sample.evaluation_trace_ids?.length)
  );
}

export function hasExperimentReview(experiment: Experiment) {
  const published = ["publishing", "activated", "failed", "unconfirmed", "restored"].includes(experiment.publish_status);
  const ready = experiment.report.passed && experiment.best_candidate
    && ["awaiting_review", "stale", "publish_failed"].includes(experiment.status);
  return !!(experiment.can_publish || ready || published || experiment.can_restore);
}
