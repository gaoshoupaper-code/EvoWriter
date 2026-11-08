import { useCallback, useEffect, useState } from "react";
import { listExperiments, type ExperimentSummary } from "@/lib/api";

export function useExperiments() {
  const [runs, setRuns] = useState<ExperimentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await listExperiments();
        if (alive) {
          setRuns([...response.items].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? "") || a.id.localeCompare(b.id)));
          setError("");
        }
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : "读取实验记录失败");
      } finally { if (alive) { setLoading(false); timer = setTimeout(poll, 5000); } }
    }
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [revision]);
  return { runs, loading, error, refresh };
}
