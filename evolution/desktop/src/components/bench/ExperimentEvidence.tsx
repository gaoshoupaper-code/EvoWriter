import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { downloadTextFile } from "@/lib/download";
import { EXPERIMENT_STATUS, PHASE_LABELS, type Experiment, type ExperimentCaseCoverage, type ExperimentComparison, type ExperimentModel, type ExperimentSample, type ExperimentScores as Score } from "@/lib/experiment-types";

export function formatCaseCoverage({ optimization, regression }: ExperimentCaseCoverage) {
  const partial = optimization.selected < optimization.total || regression.selected < regression.total;
  return `${partial ? "局部评测" : "全量评测"} · 改进 ${optimization.selected}/${optimization.total} 道，回归 ${regression.selected}/${regression.total} 道`;
}

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
const percent = (value: number) => value.toLocaleString("zh-CN", { style: "percent", maximumFractionDigits: 2 });
export const RUNTIME_DIMENSION = "运行时";

export function RuntimeScoringWeights({ rule, version, weights }: {
  rule?: Experiment["scoring_rule"]; version?: number; weights?: Score["weights"];
}) {
  if (!version) return null;
  const dimensions = Object.entries(rule?.dimension_weights ?? {});
  const total = dimensions.reduce((sum, [, weight]) => sum + weight, 0);
  return <div className="experiment-note">
    <p>{rule?.name && <>{rule.name} · v{rule.version} · </>}运行计分 v{version}</p>
    {weights && <p>总分权重 · 正文质量 {percent(weights.quality)} · 运行时 {percent(weights.runtime)}
      {"（工具 " + percent(weights.tools) + " · Token " + percent(weights.tokens) + " · Skill " + percent(weights.skill) + "）"}</p>}
    {total > 0 && <p>正文内权重 · {dimensions.map(([name, weight]) => name + " " + percent(weight / total)).join(" · ")}</p>}
  </div>;
}

export function RuntimeScoreDetails({ sample, showQuality = true }: { sample: ExperimentSample; showQuality?: boolean }) {
  const final = sample.scores?.runtime;
  const { tools, tokens } = final ?? sample.runtime_metrics ?? {};
  if (!tools && !tokens && !final) return null;
  return <dl className="record-facts" aria-label="运行时评分依据">
    {showQuality && sample.scores?.quality != null && <div><dt>正文质量分</dt><dd>{number(sample.scores.quality)} / 5</dd></div>}
    {tools && <div><dt>工具分</dt><dd>{number(tools.score)} / 5</dd></div>}
    {tokens?.score != null && <div><dt>Token 分</dt><dd>{number(tokens.score)} / 5</dd></div>}
    {final && <>
      <div><dt>Skill 分</dt><dd>{number(final.skill.score)} / 5</dd></div>
      <div><dt>运行时综合分</dt><dd>{number(final.score)} / 5</dd></div>
    </>}
    {tools && <div><dt>工具调用成功率</dt><dd>{tools.successes} / {tools.total}（{percent(tools.rate)}）</dd></div>}
    {tokens && <>
      <div><dt>本次 Token</dt><dd>{tokens.value.toLocaleString("zh-CN")}</dd></div>
      <div><dt>同题同次原版 Token</dt><dd>{tokens.baseline?.toLocaleString("zh-CN") ?? "未知"}</dd></div>
      {tokens.saving != null && <div><dt>Token 节省比例</dt><dd>{percent(tokens.saving)}</dd></div>}
    </>}
  </dl>;
}

export function ComparisonReport({ title, report, composite }: { title: string; report: Partial<ExperimentComparison>; composite?: boolean }) {
  if (!report.baseline || !report.candidate) return null;
  return <section className="experiment-comparison">
    <h3>{title} <span className={`experiment-badge ${report.passed ? "success" : "warning"}`}>{report.passed ? "通过" : "未通过"}</span></h3>
    <table className="data-table"><thead><tr><th>版本</th><th>完成 / 总数</th><th>完成率</th><th>{composite ? "综合均分" : "质量均分"}</th><th>评测异常</th></tr></thead>
      <tbody>{(["baseline", "candidate"] as const).map((arm) => {
        const metrics = report[arm]!;
        return <tr key={arm}><td>{arm === "baseline" ? "固定原版" : "候选版本"}</td><td>{metrics.completed} / {metrics.total}</td>
          <td>{(metrics.completion_rate * 100).toFixed(1)}%</td><td>{number(metrics.quality)}</td><td>{metrics.evaluation_errors}</td></tr>;
      })}</tbody></table>
    <p className="experiment-note">可比样本 {report.paired_count ?? "未知"} · {composite ? "综合分变化" : "质量变化"} {number(report.quality_delta)}</p>
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

export function ExperimentScores({ report, task }: { report: Experiment["report"]; task: Experiment["task_type"] }) {
  const regression = report?.regressions?.[task];
  if (!report?.exploration && !regression) return <p className="experiment-note">暂无汇总评分</p>;
  return <div className="experiment-score-summary" role="group" aria-label="已有评分">{([
    ["优化", report.exploration, report.exploration_round],
    ["回归", regression, report.regression_round],
  ] as const).map(([label, comparison, round]) => comparison && <div key={label}>
    <span>{label}{round ? ` · 第${round}轮` : ""}</span>
    <strong>{number(comparison.baseline?.quality)} → {number(comparison.candidate?.quality)}</strong>
      <small className={comparison.passed ? "experiment-positive" : "experiment-note"}>
        {comparison.quality_delta == null ? "无有效分差" : `${comparison.quality_delta >= 0 ? "+" : ""}${comparison.quality_delta.toFixed(3)}`}
        {comparison.passed ? " · 通过" : " · 未达标"}
      </small>
  </div>)}</div>;
}

export function SampleScoreDetails({ sample, judges, canReadEvidence = true, runtimeScoringVersion }: {
  sample: ExperimentSample; judges: ExperimentModel[]; canReadEvidence?: boolean; runtimeScoringVersion?: number;
}) {
  const scores = sample.scores;
  const reasonsByDimension = scores?.reasons ?? sample.score_progress?.reasons;
  if (!reasonsByDimension) return null;
  const dimensions = scores ? Object.keys(scores.dimensions) : Object.entries(sample.score_progress?.reasons ?? {})
    .filter(([, reasons]) => reasons.length > 0).map(([dimension]) => dimension);
  return <div className="experiment-score-details">{dimensions.map((dimension) => {
    const reasons = reasonsByDimension[dimension];
    const skill = dimension === RUNTIME_DIMENSION && (runtimeScoringVersion || scores?.runtime);
    const score = skill ? scores?.runtime?.skill.score : scores?.dimensions[dimension];
    return <article key={dimension}><h4>{skill ? "Skill 评委评分" : dimension}
      {scores && <span>{number(score)} / 5</span>}</h4>
      {Array.isArray(reasons) ? reasons.map((entry, index) => {
        if (!entry || typeof entry !== "object") return canReadEvidence ? <EvidenceValues key={index} value={entry} /> : null;
        const reason = entry as Record<string, unknown>;
        const judge = judges.find((item) => item.id === reason.judge_id);
        return <div className="experiment-judge-reason" key={index}>
          <p className="experiment-note">{judge?.name || judge?.model || `评委 ${index + 1}`}
            {typeof reason.repeat === "number" && ` · 第 ${reason.repeat} 次`} · {reason.error ? "评分失败" : number(typeof reason.score === "number" ? reason.score : null)}</p>
          {canReadEvidence && (reason.error ? <p className="experiment-error">{String(reason.error)}</p> : typeof reason.reason === "string" ? <>
            <p className="experiment-prewrap">{reason.reason}</p>
            <div><strong>评分依据</strong>{Array.isArray(reason.evidence_details) && reason.evidence_details.length
              ? <ul>{reason.evidence_details.map(({ ref, text }, i) => <li key={i}><code>{ref}</code> {text}</li>)}</ul>
              : <EvidenceValues value={reason.evidence} />}</div>
          </> : <div className="experiment-reason-columns">
            <div><strong>达标依据</strong><EvidenceValues value={reason["达标"]} /></div>
            <div><strong>具体不足</strong><EvidenceValues value={reason["不足"]} /></div>
          </div>)}
        </div>;
      }) : canReadEvidence && <EvidenceValues value={reasons} />}
    </article>;
  })}</div>;
}
