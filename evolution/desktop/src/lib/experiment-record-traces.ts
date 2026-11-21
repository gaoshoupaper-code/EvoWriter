import type { ActiveRun } from "./types";

export function sampleActivitySection(run: ActiveRun, experimentId: string, sampleId: string) {
  const refs = run.external_refs;
  if (!refs || refs.sample_id !== sampleId || (run.experiment_id ?? refs.experiment_id) !== experimentId
      || (refs.experiment_id && refs.experiment_id !== experimentId)) return;
  const operation = run.display_context?.operation;
  if (run.run_purpose === "evolution_eval" && (!operation || operation === "evaluation")) return "evaluation";
  if ((run.run_purpose === "optimization" || run.run_purpose === "evolution")
      && (!operation || operation === "generation")) return "writing";
}
