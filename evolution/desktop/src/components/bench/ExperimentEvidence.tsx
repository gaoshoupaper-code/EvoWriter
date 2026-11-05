import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { downloadTextFile } from "@/lib/download";
import type { ExperimentComparison } from "@/lib/experiment-types";

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
