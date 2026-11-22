import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import PageAgentSelector from "@/components/PageAgentSelector";
import { getExperiment, getActiveRuns, stopExperiment, type ActiveRun, type Experiment } from "@/lib/api";
import { EXPERIMENT_STATUS, PHASE_LABELS, candidatePlaceholder, experimentTaskLabel } from "@/lib/experiment-types";
import { ExperimentScores, ModelIdentity, formatCaseCoverage } from "@/components/bench/ExperimentEvidence";
import { ExperimentTimeline } from "@/components/bench/ExperimentTimeline";
import { hasExperimentReview } from "@/lib/experiment-timeline";
import { downloadTextFile } from "@/lib/download";
import { withAgent } from "@/lib/writing-agent";
import "@/components/bench/experiment-observation.css";
import "@/components/bench/experiments.css";

const STOPPABLE = new Set(["pending", "running", "recovering", "stopping"]);

export default function ExperimentDetail() {
  const { experimentId = "" } = useParams<{ experimentId: string }>();
  const location = useLocation();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveRuns, setLiveRuns] = useState<ActiveRun[]>([]);
  const [liveError, setLiveError] = useState("");
  const request = useRef(0);
  const livePending = useRef<object | null>(null);
  const mounted = useRef(true);
  const pending = useRef(false);
  const refresh = useCallback(async () => {
    const sequence = ++request.current;
    // A slow Trace query must not hold up the authoritative experiment record.
    if (!livePending.current) {
      const token = {};
      livePending.current = token;
      void getActiveRuns("experiment").then(active => {
        if (mounted.current && livePending.current === token) {
          setLiveRuns(active.filter(run => (run.experiment_id ?? run.external_refs?.experiment_id) === experimentId));
          setLiveError("");
        }
      }).catch(() => {
        if (mounted.current && livePending.current === token) setLiveError("运行 Trace 更新失败，保留最近成功的运行信息。");
      }).finally(() => { if (livePending.current === token) livePending.current = null; });
    }
    try {
      const record = await getExperiment(experimentId);
      if (mounted.current && sequence === request.current) { setExperiment(record); setError(""); }
    } catch (cause) {
      if (mounted.current && sequence === request.current) setError(cause instanceof Error ? cause.message : "读取实验失败");
    }
  }, [experimentId]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    mounted.current = true;
    pending.current = false;
    livePending.current = null;
    setBusy(false);
    setExperiment(null);
    setLiveRuns([]);
    setLiveError("");
    const poll = async () => {
      if (!pending.current) await refresh();
      if (alive) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {
      alive = false;
      mounted.current = false;
      livePending.current = null;
      request.current += 1;
      clearTimeout(timer);
    };
  }, [refresh]);

  const returnKey = "bench.recordReturn." + experimentId + "." + location.key;
  const loadedId = experiment?.id;
  useEffect(() => {
    if (!loadedId) return;
    const saved = sessionStorage.getItem(returnKey);
    const target = new URLSearchParams(saved ?? location.search);
    const sampleId = target.get("sample");
    const section = target.get("section") === "evaluation" ? "evaluation" : "writing";
    const anchor = target.get("anchor") ?? (sampleId ? `record-${section}-${sampleId}` : "");
    const row = document.getElementById(anchor);
    if (row) {
      const focusTarget = row.querySelector<HTMLElement>("a, button") ?? row;
      focusTarget.focus({ preventScroll: true });
      const scroll = target.get("scroll");
      if (scroll !== null && Number.isFinite(Number(scroll))) {
        const container = document.querySelector<HTMLElement>(".shell-main");
        if (container) container.scrollTop = Number(scroll);
        else window.scrollTo({ top: Number(scroll), behavior: "instant" });
        // New scores can shift the record after the old scroll position was saved.
        const viewport = container?.getBoundingClientRect();
        const recordBounds = focusTarget.getBoundingClientRect();
        if (recordBounds.top < (viewport?.top ?? 0) || recordBounds.bottom > (viewport?.bottom ?? window.innerHeight)) {
          focusTarget.scrollIntoView?.({ block: "nearest" });
        }
      } else row.scrollIntoView?.({ block: "nearest" });
    }
    sessionStorage.removeItem(returnKey);
  }, [loadedId, location.search, returnKey]);

  function remember(anchor: string) {
    const scroll = document.querySelector<HTMLElement>(".shell-main")?.scrollTop ?? window.scrollY;
    sessionStorage.setItem(returnKey, new URLSearchParams({ anchor, scroll: String(scroll) }).toString());
  }
  async function stop() {
    if (pending.current) return;
    const sequence = ++request.current;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await stopExperiment(experimentId);
      if (mounted.current && sequence === request.current) setExperiment(result);
    } catch (cause) {
      if (mounted.current && sequence === request.current) {
        setError(cause instanceof Error ? cause.message : "停止实验失败");
        try {
          const result = await getExperiment(experimentId);
          if (mounted.current && sequence === request.current) setExperiment(result);
        } catch { /* The next poll reconciles an operation whose outcome is uncertain. */ }
      }
    } finally {
      if (mounted.current && sequence === request.current) { pending.current = false; setBusy(false); }
    }
  }

  if (!experiment) return <div className="bench-page"><h1>实验详情</h1><PageAgentSelector />
    {error ? <><p role="alert" className="experiment-error">{error}</p>
      <button className="config-button" onClick={() => void refresh()}>重试</button></> : <p role="status">正在加载实验…</p>}
  </div>;
  const current = experiment.current_candidate ?? (experiment.current_round === experiment.best_candidate?.round ? experiment.best_candidate : null);
  const published = experiment.publish_status && experiment.publish_status !== "not_started";
  const hasTimeline = experiment.samples.length > 0 || experiment.rounds.length > 0 || STOPPABLE.has(experiment.status);
  return <div className="bench-page experiment-detail">
    <header className="page-header">
      <Link to={withAgent("/bench/experiments")} className="action-link">返回实验记录</Link>
      <h1>{experiment.name}</h1>
      <div className="experiment-priority-summary">
        <p className="page-desc">
          <span className={"experiment-state " + experiment.status}>{EXPERIMENT_STATUS[experiment.status] ?? experiment.status}</span>
          {experiment.phase && <strong className="experiment-current-phase">{PHASE_LABELS[experiment.phase] ?? experiment.phase}</strong>}
          <span>{experimentTaskLabel(experiment.task_type, experiment.flow_version)}</span>
        </p>
        <ExperimentScores report={experiment.report} task={experiment.task_type} />
      </div>
      <div className="experiment-actions">
        <button className="config-button" type="button" disabled={busy} onClick={() => void refresh()}>刷新</button>
        {STOPPABLE.has(experiment.status) && <button className="config-button" type="button" disabled={busy || experiment.status === "stopping"}
          onClick={() => void stop()}>停止实验</button>}
        <button className="config-button" type="button" onClick={() => downloadTextFile(experiment.id + "-report.json",
          JSON.stringify(experiment, null, 2), "application/json;charset=utf-8")}>导出报告</button>
      </div>
    </header>
    <PageAgentSelector />
    {error && <p role="alert" className="experiment-error">{error}</p>}
    <dl className="experiment-overview" aria-label="实验概况">
      {experiment.case_coverage && <div><dt>评测范围</dt><dd>{formatCaseCoverage(experiment.case_coverage)}</dd></div>}
      <div><dt>Harness 原版 → 当前候选</dt><dd>v{experiment.baseline.version} → {current ? `候选改版 ${current.round}`
        : candidatePlaceholder(experiment.phase, experiment.status, experiment.current_round)}</dd></div>
      <div><dt>执行模型</dt><dd><ModelIdentity model={experiment.models?.executor} /></dd></div>
      <div><dt>评测模型</dt><dd>{experiment.models?.judges?.length ? experiment.models.judges.map((judge, index) =>
        <ModelIdentity key={judge.id ?? index} model={judge} />) : "未记录模型"}</dd></div>
      <div><dt>保留最佳候选</dt><dd>{experiment.best_candidate ? `候选改版 ${experiment.best_candidate.round}` : "尚无合格候选"}</dd></div>
    </dl>
    {liveError && <p role="status" className="experiment-error">{liveError}</p>}
    {hasTimeline ? <ExperimentTimeline experiment={experiment} liveRuns={liveRuns} onOpenRecord={remember} /> :
      <section className="experiment-timeline" aria-label="实验流程">
        <p className="experiment-empty">暂无实验流程记录。</p>
      </section>}
    <footer className="experiment-review-entry" id="experiment-review" tabIndex={-1}>
      {experiment.end_reason && <p className="experiment-note">{experiment.end_reason}</p>}
      {experiment.stale_reason && <p role="alert" className="experiment-error">{experiment.stale_reason}</p>}
      {hasExperimentReview(experiment) && <Link className="config-button primary"
        to={"/bench/experiments/" + encodeURIComponent(experiment.id) + "/review" + location.search}
        state={{ fromRecords: true, returnTo: location.pathname + location.search, returnAnchor: "experiment-review" }}
        onClick={() => remember("experiment-review")}>{published ? "查看发布状态" : "进入审核"}</Link>}
    </footer>
  </div>;
}
