import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import PageAgentSelector from "@/components/PageAgentSelector";
import { getActiveRuns, getExperiment, type ActiveRun, type Experiment } from "@/lib/api";
import { mergeTraceDisplay, traceDisplayContext, type TraceDisplayContext } from "@/lib/trace-display";
import TraceDetailPage from "@/pages/trace-detail";
import "./experiment-record-detail.css";

const RUNNING = new Set(["pending", "running", "recovering", "stopping"]);

export function experimentRunning(experiment: Experiment) {
  return RUNNING.has(experiment.status);
}

function shouldPoll(experiment: Experiment) {
  return experimentRunning(experiment) || experiment.status === "awaiting_review" || experiment.status === "publishing"
    || experiment.publish_status === "publishing" || experiment.publish_status === "unconfirmed"
    || experiment.rollback_status === "restoring";
}

export function useExperimentPage(id: string) {
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [error, setError] = useState("");
  const [operationError, setOperationError] = useState("");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const operation = useRef(false);
  const request = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let poll = false;
    async function load() {
      const sequence = ++request.current;
      if (!operation.current) {
        try {
          const data = await getExperiment(id);
          if (active && sequence === request.current) {
            setExperiment(data);
            setError("");
            poll = shouldPoll(data);
          }
        } catch (cause) {
          if (active && sequence === request.current) {
            setError(cause instanceof Error ? cause.message : "读取实验记录失败。");
          }
        }
      }
      if (active && (poll || operation.current)) timer = setTimeout(load, 5000);
    }
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [id, revision]);
  const refresh = () => setRevision(current => current + 1);
  function retry() {
    setOperationError("");
    refresh();
  }
  async function operate(action: (id: string) => Promise<Experiment>) {
    if (operation.current) return;
    operation.current = true;
    request.current += 1;
    setBusy(true);
    setError("");
    setOperationError("");
    try {
      const data = await action(id);
      if (mounted.current) setExperiment(data);
    } catch (cause) {
      if (mounted.current) setOperationError(cause instanceof Error ? cause.message : "操作失败。");
    } finally {
      operation.current = false;
      if (mounted.current) {
        setBusy(false);
        refresh();
      }
    }
  }
  return { experiment, error: operationError || error, retry, operate, busy };
}

export function useExperimentActivity(track: boolean) {
  const [runs, setRuns] = useState<ActiveRun[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!track) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const activity = await getActiveRuns("experiment");
        if (active) { setRuns(activity); setError(false); }
      } catch {
        if (active) setError(true);
      }
      if (active) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [track]);
  return { runs, error: track && error };
}

export function ExperimentDetailPage({ id, title, anchor, loading, error, retry, children, sampleId, section, busy }: {
  id: string; title: string; anchor?: string; loading?: boolean; error?: string; retry?: () => void; children?: ReactNode;
  sampleId?: string; section?: string; busy?: boolean;
}) {
  const { search, state } = useLocation();
  const navigate = useNavigate();
  const query = new URLSearchParams(search);
  if (anchor) query.set("anchor", anchor);
  if (sampleId) query.set("sample", sampleId);
  if (section) query.set("section", section);
  const back = "/bench/experiments/" + encodeURIComponent(id) + (query.size ? "?" + query : "");
  const backText = sampleId ? "← 返回运行记录" : "← 返回实验详情";
  return <div className="bench-page experiment-record-detail">
    <nav aria-label="返回实验">{state?.fromRecords
      ? <button className="action-link" type="button" onClick={() => navigate(-1)}>{backText}</button>
      : <Link className="action-link" to={back}>{backText}</Link>}</nav>
    <h1>{title}</h1>
    <PageAgentSelector />
    {error && <p role="alert" className="experiment-error">{error} {retry &&
      <button className="action-link" type="button" disabled={busy} onClick={retry}>重试</button>}</p>}
    {loading && !error && <p role="status">正在读取实验记录…</p>}
    {children}
  </div>;
}

export function ExperimentRecordTrace({ title, experiment, archived = [], live = [], waiting, unavailable, context, prioritizeModelIO }: {
  title: string; experiment: Experiment; archived?: string[]; live?: ActiveRun[]; waiting?: boolean;
  unavailable?: boolean; context: TraceDisplayContext; prioritizeModelIO?: boolean;
}) {
  const [selected, setSelected] = useState("");
  const traces = [...new Set([...archived, ...live.map(run => run.trace_id)])];
  const defaultArchived = context.operation === "evaluation" ? archived.at(-1) : archived[0];
  const traceId = traces.includes(selected) ? selected : live[0]?.trace_id ?? defaultArchived;
  const active = live.find(run => run.trace_id === traceId);
  const trace = traceId ? mergeTraceDisplay(experiment.trace_runs?.[traceId] ?? { trace_id: traceId }, active) : {};
  return <section aria-label={title}>
    {!prioritizeModelIO && <h2>{title}</h2>}
    {unavailable && <p role="status">运行中 Trace 暂时无法更新。</p>}
    {!experiment.can_read_content ? <p className="experiment-note">查看 Trace 详情需要正文读取权限。</p>
      : !traceId ? <p className="experiment-note" role={waiting ? "status" : undefined}>
        {waiting ? "等待 Trace 同步…" : "尚无关联 Trace。"}</p> : <>
        {traces.length > 1 && <label className="record-trace-picker">{prioritizeModelIO ? "选择运行记录" : "选择 Trace"}
          <select value={traceId} className="config-input" onChange={event => setSelected(event.target.value)}>
            {traces.map((id, i) => <option key={id} value={id}>{prioritizeModelIO ? `第 ${i + 1} 段运行` : `${i + 1} · ${id}`}</option>)}
          </select></label>}
        <TraceDetailPage key={traceId} traceIdOverride={traceId} embedded canReadContent prioritizeModelIO={prioritizeModelIO}
          displayContextOverride={traceDisplayContext(trace, context)} />
      </>}
  </section>;
}
