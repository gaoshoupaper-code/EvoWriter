import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, Download } from "lucide-react";
import {
  exportTraceContent,
  listBenchmarkBatches,
  listBenchmarkRuns,
  type BenchmarkBatchSummary,
  type BenchmarkRunRow,
} from "@/lib/api";
import { downloadTextFile } from "@/lib/download";
import { DeliveriesView } from "@/components/bench/DeliveriesView";
import { batchStatusLabel, batchStatusClass } from "@/components/bench/batchStatus";

/**
 * 评测产物库（REQ-20260921-135543 FR-009，DEC-004/012/022）。
 *
 * 批次→case→seed 三级下钻，产物为主角（评分仅总分徽章，不展开评分区——
 * 评分全貌在批次详情页）。三件套 Markdown 渲染（DeliveriesView 复用）；
 * 调用链等交互式观测跳 trace 详情页（DEC-007）。
 * 导出：单篇 Markdown（DeliveriesView 内）+ 整 trace JSON（本页）。
 */

const STATUS_LABEL: Record<string, string> = {
  done: "完成",
  failed: "失败",
  pending: "未跑",
  running: "运行中",
  evaluating: "评分中",
  cancelled: "已取消",
};

export default function BenchArtifacts() {
  const [batches, setBatches] = useState<BenchmarkBatchSummary[]>([]);
  const [batchesLoading, setBatchesLoading] = useState(true);
  const [batchesError, setBatchesError] = useState<string | null>(null);

  // 三级下钻状态
  const [batchId, setBatchId] = useState<string | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [runId, setRunId] = useState<number | null>(null);

  const [rows, setRows] = useState<BenchmarkRunRow[]>([]);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    listBenchmarkBatches(20)
      .then((resp) => setBatches(resp.batches))
      .catch((e: unknown) => {
        setBatchesError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setBatchesLoading(false));
  }, []);

  const loadRows = useCallback(async (id: string) => {
    setRowsLoading(true);
    setRowsError(null);
    try {
      const resp = await listBenchmarkRuns(id);
      setRows(resp.items);
    } catch (e) {
      setRowsError(e instanceof Error ? e.message : String(e));
    } finally {
      setRowsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (batchId) void loadRows(batchId);
    else {
      setRows([]);
      setCaseId(null);
      setRunId(null);
    }
  }, [batchId, loadRows]);

  const byCase = useMemo(() => {
    const grouped = new Map<string, BenchmarkRunRow[]>();
    for (const row of rows) {
      const list = grouped.get(row.case_id) ?? [];
      list.push(row);
      grouped.set(row.case_id, list);
    }
    return [...grouped.entries()].map(([cid, caseRows]) => ({
      caseId: cid,
      caseRows: [...caseRows].sort((a, b) => (a.seed ?? 0) - (b.seed ?? 0)),
    }));
  }, [rows]);

  const activeRun = rows.find((r) => r.id === runId) ?? null;

  async function handleExportTrace(traceId: string) {
    setExporting(true);
    try {
      const data = await exportTraceContent(traceId);
      downloadTextFile(
        `trace-${traceId.slice(0, 8)}.json`,
        JSON.stringify(data, null, 2),
        "application/json;charset=utf-8",
      );
      toast.success(`trace ${traceId.slice(0, 8)} 已导出（JSON 全量）`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "导出失败（需超级管理员权限）");
    } finally {
      setExporting(false);
    }
  }

  function renderBreadcrumb() {
    const parts: string[] = ["评测产物"];
    if (batchId) parts.push(`批次 ${batchId.slice(0, 8)}`);
    if (caseId) parts.push(caseId);
    if (activeRun) parts.push(`seed #${activeRun.seed ?? "-"}`);
    return (
      <nav className="bench-artifacts-crumbs">
        {parts.map((p, i) => (
          <span key={i} className={i === parts.length - 1 ? "bench-crumbs-current" : ""}>
            {i > 0 && <span className="bench-crumbs-sep"> / </span>}
            {p}
          </span>
        ))}
        {(batchId || caseId || runId) && (
          <button
            type="button"
            className="action-link bench-crumbs-back"
            onClick={() => {
              if (runId) setRunId(null);
              else if (caseId) setCaseId(null);
              else if (batchId) setBatchId(null);
            }}
          >
            <ArrowLeft size={13} aria-hidden />
            返回上级
          </button>
        )}
      </nav>
    );
  }

  return (
    <div className="bench-page bench-artifacts-page">
      <header className="page-header">
        <h1>评测产物</h1>
        <p className="page-desc">
          批次 → case → seed 三级下钻 · 三件套与观测产物正文 · 单篇/整包导出
        </p>
      </header>

      {renderBreadcrumb()}

      {/* 第一级：批次列表 */}
      {!batchId && (
        <>
          {batchesLoading ? (
            <div className="page-loading">加载批次…</div>
          ) : batchesError ? (
            <div className="error-card">
              <span className="error-icon">⚠</span>
              <div className="error-body">
                <div className="error-title">批次加载失败</div>
                <div className="error-desc">{batchesError}</div>
              </div>
            </div>
          ) : batches.length === 0 ? (
            <div className="monitor-empty">暂无评测批次——先在工作台触发一次</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>批次</th>
                  <th>状态</th>
                  <th>harness</th>
                  <th>进度</th>
                  <th>触发时间</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr
                    key={b.batch_id}
                    className="bench-row-click"
                    onClick={() => setBatchId(b.batch_id)}
                  >
                    <td className="mono" title={b.batch_id}>{b.batch_id.slice(0, 8)}</td>
                    <td>
                      <span className={`session-status ${batchStatusClass(b.status)}`}>
                        {batchStatusLabel(b.status)}
                      </span>
                    </td>
                    <td>{b.harness_version != null ? `v${b.harness_version}` : "—"}</td>
                    <td>{b.progress.done}/{b.progress.total}</td>
                    <td>{b.triggered_at?.slice(0, 19).replace("T", " ") ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* 第二级：case 列表 */}
      {batchId && !caseId && (
        <>
          {rowsLoading ? (
            <div className="page-loading">加载 case 列表…</div>
          ) : rowsError ? (
            <div className="monitor-empty">读取行数据失败：{rowsError}</div>
          ) : byCase.length === 0 ? (
            <div className="monitor-empty">该批次无行数据</div>
          ) : (
            <div className="bench-artifacts-case-grid">
              {byCase.map(({ caseId: cid, caseRows }) => {
                const doneScores = caseRows
                  .map((r) => r.scores?.overall)
                  .filter((v): v is number => v != null);
                const avg = doneScores.length
                  ? (doneScores.reduce((s, v) => s + v, 0) / doneScores.length).toFixed(2)
                  : null;
                return (
                  <article
                    key={cid}
                    className="bench-case-card bench-row-click"
                    onClick={() => setCaseId(cid)}
                  >
                    <header className="bench-case-head">
                      <span className="bench-case-id">{cid}</span>
                      {avg != null && (
                        <span className="bench-score-overview-value bench-dim-high" title="总分均分（评分详情在批次报告页）">
                          {avg}
                        </span>
                      )}
                    </header>
                    <div className="bench-seed-row">
                      {caseRows.map((r) => (
                        <span key={r.id} className={`bench-seed-chip status-${r.status}`}>
                          #{r.seed ?? "-"}
                          <span className="bench-seed-status">
                            {STATUS_LABEL[r.status] ?? r.status}
                          </span>
                        </span>
                      ))}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* 第三级：seed 行 → 产物为主角 */}
      {batchId && caseId && (
        <>
          {!runId && (
            <div className="bench-artifacts-seeds">
              {rowsLoading ? (
                <div className="page-loading">加载行数据…</div>
              ) : (
                byCase
                  .find((c) => c.caseId === caseId)
                  ?.caseRows.map((r) => (
                    <button
                      key={r.id}
                      type="button"
                      className={`bench-seed-chip status-${r.status}`}
                      onClick={() => setRunId(r.id)}
                    >
                      #{r.seed ?? "-"}
                      <span className="bench-seed-status">
                        {STATUS_LABEL[r.status] ?? r.status}
                      </span>
                      {r.scores?.overall != null && (
                        <span className="bench-seed-score">{r.scores.overall.toFixed(2)}</span>
                      )}
                    </button>
                  ))
              )}
            </div>
          )}

          {activeRun && (
            <div className="bench-artifacts-run">
              <div className="bench-overall-row">
                <span>
                  总分{" "}
                  <strong>
                    {activeRun.scores?.overall != null ? activeRun.scores.overall.toFixed(2) : "—"}
                  </strong>
                  <span className="bench-artifacts-hint">（评分详情在批次详情页）</span>
                </span>
                {activeRun.trace_id && (
                  <>
                    <button
                      className="action-link"
                      onClick={() => handleExportTrace(activeRun.trace_id!)}
                      disabled={exporting}
                      title="导出该 trace 全量 JSON（需超级管理员）"
                    >
                      <Download size={13} aria-hidden />
                      {exporting ? "导出中…" : "导出整 trace"}
                    </button>
                    <Link
                      className="action-link"
                      to={`/traces/${activeRun.trace_id}`}
                      title="调用链 / Token 图等交互式观测在运行观测页"
                    >
                      调用链与事件观测 →
                    </Link>
                  </>
                )}
              </div>

              {activeRun.status === "failed" ? (
                <div className="bench-error-box">
                  <p className="bench-error-title">
                    失败（重试 {activeRun.retries} 次）· 无评分
                  </p>
                  <p className="bench-error-msg">{activeRun.error || "未知错误"}</p>
                </div>
              ) : !activeRun.trace_id ? (
                <div className="monitor-empty">该行无 trace（未跑或已取消），无产物可看</div>
              ) : (
                <DeliveriesView traceId={activeRun.trace_id} />
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
