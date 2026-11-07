import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { downloadTextFile } from "@/lib/download";
import { EXPERIMENT_STATUS, PHASE_LABELS, type Experiment, type ExperimentComparison, type ExperimentModel, type ExperimentSample } from "@/lib/experiment-types";

export function EvidenceValues({ value }: { value: unknown }) {
  if (value == null) return <span>未知</span>;
  if (typeof value === "boolean") return <span>{value ? "是" : "否"}</span>;
  if (Array.isArray(value)) return <ul>{value.map((item, i) => <li key={i}><EvidenceValues value={item} /></li>)}</ul>;
  if (typeof value === "object") return <dl className="experiment-evidence">{Object.entries(value).map(([key, item]) =>
    <div key={key}><dt>{key}</dt><dd><EvidenceValues value={item} /></dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}

export function ArtifactFiles({ files }: { files: Record<string, string> }) {
  return <div className="experiment-files">{Object.entries(files).map(([path, content]) =>
    <details key={path}>
      <summary>{path}</summary>
      <button type="button" className="action-link" onClick={() => downloadTextFile(path.replace(/[\\/]/g, "_"), content)}>下载文稿</button>
      <div className="prose-doc"><ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{content}</ReactMarkdown></div>
    </details>)}</div>;
}

const number = (value: number | null | undefined) => value == null ? "未知" : value.toFixed(3);
export function ComparisonReport({ title, report }: { title: string; report: Partial<ExperimentComparison> }) {
  if (!report.baseline || !report.candidate) return null;
  return <section className="experiment-comparison">
    <h3>{title} <span className={`experiment-badge ${report.passed ? "success" : "warning"}`}>{report.passed ? "通过" : "未通过"}</span></h3>
    <table className="data-table"><thead><tr><th>版本</th><th>完成 / 总数</th><th>完成率</th><th>质量均分</th><th>评测异常</th></tr></thead>
      <tbody>{(["baseline", "candidate"] as const).map((arm) => {
        const metrics = report[arm]!;
        return <tr key={arm}><td>{arm === "baseline" ? "固定原版" : "候选版本"}</td><td>{metrics.completed} / {metrics.total}</td>
          <td>{(metrics.completion_rate * 100).toFixed(1)}%</td><td>{number(metrics.quality)}</td><td>{metrics.evaluation_errors}</td></tr>;
      })}</tbody></table>
    <p className="experiment-note">可比样本 {report.paired_count ?? "未知"} · 质量变化 {number(report.quality_delta)}</p>
    {!!report.problems?.length && <ul className="experiment-error">{report.problems.map((problem, i) => <li key={i}>{problem}</li>)}</ul>}
    {!!report.new_failures?.length && <p className="experiment-error">新增失败：{report.new_failures.join("、")}</p>}
    {!!report.changes?.length && <details><summary>逐例变化</summary><table className="data-table"><thead><tr><th>样本</th><th>重复次序</th><th>原版</th><th>候选</th><th>变化</th></tr></thead>
      <tbody>{report.changes.map((row) => <tr key={`${row.package_id}-${row.repeat}`}><td>{row.package_id}</td><td>{row.repeat}</td>
        <td>{number(row.baseline)}</td><td>{number(row.candidate)}</td><td>{number(row.delta)}</td></tr>)}</tbody></table></details>}
  </section>;
}

export function ModelIdentity({ model }: { model?: ExperimentModel | null }) {
  if (!model?.model) return <span className="experiment-note">未记录模型</span>;
  return <span className="experiment-model"><strong>{model.name || model.model}</strong>
    {model.name && model.name !== model.model && <small className="mono">{model.model}</small>}</span>;
}

export function ExperimentProgress({ experiment, compact = false }: {
  experiment: Pick<Experiment, "phase" | "status" | "current_round" | "max_rounds" | "progress">; compact?: boolean;
}) {
  const current = experiment.progress?.phase;
  const progress = current ?? experiment.progress;
  const active = ["pending", "running", "recovering", "stopping", "publishing"].includes(experiment.status);
  const emptyLabel = !active ? EXPERIMENT_STATUS[experiment.status] ?? "本环节已结束" :
    experiment.phase === "evolving" ? "正在修改 Harness" : experiment.phase === "publish" ? "正在核对发布与加载" : "正在准备本环节任务";
  return <div className={`experiment-progress${compact ? " compact" : ""}`}>
    <div className="experiment-progress-label"><strong>{PHASE_LABELS[experiment.phase] ?? experiment.phase}</strong>
      <span>改版 {experiment.current_round} / {experiment.max_rounds} 轮</span></div>
    {progress && progress.total > 0 ? <>
      <progress aria-label={current ? "当前环节进度" : "累计任务进度"} max={progress.total} value={progress.done} />
      <small>{current ? "本环节" : "累计"}已处理 {progress.done} / {progress.total} 次 · 目标完成 {progress.completed} 次</small>
    </> : <small>{emptyLabel}</small>}
  </div>;
}

export function ExperimentScores({ report }: { report: Experiment["report"] }) {
  return <div className="experiment-score-summary">{([
    ["优化", report.exploration, report.exploration_round],
    ["独立终验", report.validation, report.validation_round],
  ] as const).map(([label, comparison, round]) => <div key={label}>
    <span>{label}{round ? ` · 第${round}轮` : ""}</span>
    {comparison ? <><strong>{number(comparison.baseline?.quality)} → {number(comparison.candidate?.quality)}</strong>
      <small className={comparison.passed ? "experiment-positive" : "experiment-note"}>
        {comparison.quality_delta == null ? "无有效分差" : `${comparison.quality_delta >= 0 ? "+" : ""}${comparison.quality_delta.toFixed(3)}`}
        {comparison.passed ? " · 通过" : " · 未达标"}
      </small></> : <small>待开始</small>}
  </div>)}</div>;
}

export function SampleScoreDetails({ sample, judges }: { sample: ExperimentSample; judges: ExperimentModel[] }) {
  if (!sample.scores) return null;
  return <div className="experiment-score-details">{Object.entries(sample.scores.dimensions).map(([dimension, score]) => {
    const reasons = sample.scores!.reasons[dimension];
    return <article key={dimension}><h4>{dimension}<span>{number(score)} / 5</span></h4>
      {Array.isArray(reasons) ? reasons.map((entry, index) => {
        if (!entry || typeof entry !== "object") return <EvidenceValues key={index} value={entry} />;
        const reason = entry as Record<string, unknown>;
        const judge = judges.find((item) => item.id === reason.judge_id);
        return <div className="experiment-judge-reason" key={index}>
          <p className="experiment-note">{judge?.name || judge?.model || `评委 ${index + 1}`} · {number(typeof reason.score === "number" ? reason.score : null)}</p>
          {reason.error ? <p className="experiment-error">{String(reason.error)}</p> : <div className="experiment-reason-columns">
            <div><strong>达标依据</strong><EvidenceValues value={reason["达标"]} /></div>
            <div><strong>具体不足</strong><EvidenceValues value={reason["不足"]} /></div>
          </div>}
        </div>;
      }) : <EvidenceValues value={reasons} />}
    </article>;
  })}</div>;
}
