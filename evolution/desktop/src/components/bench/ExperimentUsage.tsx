import { useEffect, useState } from "react";
import { getExperimentEvaluationUsage } from "@/lib/api";
import type { ExperimentEvaluationUsagePage, ExperimentUsageSummary } from "@/lib/experiment-types";

const PAGE_SIZE = 50;
const tokenCount = (value: number | null | undefined) => value == null ? "未知" : value.toLocaleString("zh-CN");
type Props = { experimentId: string; usage: ExperimentUsageSummary };

export function ExperimentUsage(props: Props) {
  return <UsagePanel key={props.experimentId} {...props} />;
}

function UsagePanel({ experimentId, usage }: Props) {
  const [open, setOpen] = useState(false);
  return <details open={open}>
    <summary onClick={(event) => { event.preventDefault(); setOpen((value) => !value); }}>调用用量，未知值单独标记</summary>
    {open && <>
      <dl className="experiment-facts">{(["generation", "evaluation", "evolution"] as const).map((kind) => {
        const group = usage[kind];
        const label = { generation: "生成用量", evaluation: "评分用量", evolution: "改版用量" }[kind];
        const countLabel = { generation: "生成记录数", evaluation: "评分调用次数", evolution: "改版记录数" }[kind];
        return <div key={kind}><dt>{label}</dt><dd>
          <span>{countLabel}：{tokenCount(group?.calls)} · {kind === "evaluation" ? "失败调用次数" : "失败记录数"}：{tokenCount(group?.failed_calls)}</span>
          <span className="experiment-cell-note">输入 Token：{tokenCount(group?.input_tokens)} · 输出 Token：{tokenCount(group?.output_tokens)} · 总 Token：{tokenCount(group?.total_tokens)}</span>
          {!group?.known && <small className="experiment-cell-note">{group?.partial ? "部分已知" : "用量未知"}</small>}
        </dd></div>;
      })}</dl>
      <EvaluationUsage experimentId={experimentId} />
    </>}
  </details>;
}

function EvaluationUsage({ experimentId }: { experimentId: string }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ExperimentEvaluationUsagePage | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true); setError(""); setPage(null);
    async function poll() {
      try {
        const result = await getExperimentEvaluationUsage(experimentId, { offset, limit: PAGE_SIZE });
        if (alive) { setPage(result); setTotal(result.total); setError(""); }
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : "评分调用明细读取失败");
      } finally {
        if (alive) { setLoading(false); timer = setTimeout(poll, 5000); }
      }
    }
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [experimentId, offset, revision]);

  return <section aria-label="评分调用用量">
    <h4>评分调用明细</h4>
    {loading && <p role="status">正在读取评分明细…</p>}
    {error && <p role="alert" className="experiment-error">{error} <button type="button" className="action-link" onClick={() => setRevision((value) => value + 1)}>重试评分明细</button></p>}
    {page && (page.items.length ? <div className="experiment-table-scroll"><table className="data-table" aria-label="评分调用明细">
      <thead><tr><th>样本</th><th>评委</th><th>评分维度</th><th>结果</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th></tr></thead>
      <tbody>{page.items.map((item, index) => <tr key={offset + index}>
        <td>{item.sample_id}</td><td>{item.judge_id ?? "未记录"}</td><td>{item.dimension || "未记录"}</td>
        <td>{({ failed: "失败", error: "失败", completed: "成功", success: "成功" } as Record<string, string>)[item.status] ?? item.status}</td>
        <td>{tokenCount(item.usage?.input_tokens ?? item.usage?.prompt_tokens)}</td>
        <td>{tokenCount(item.usage?.output_tokens ?? item.usage?.completion_tokens)}</td><td>{tokenCount(item.usage?.total_tokens)}</td>
      </tr>)}</tbody>
    </table></div> : <p className="experiment-note">暂无评分调用记录</p>)}
    {total > PAGE_SIZE && <div className="experiment-actions">
      <button type="button" className="config-button" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>上一页</button>
      <span>第 {Math.floor(offset / PAGE_SIZE) + 1} / {Math.ceil(total / PAGE_SIZE)} 页 · 共 {total} 次调用</span>
      <button type="button" className="config-button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>下一页</button>
    </div>}
  </section>;
}
