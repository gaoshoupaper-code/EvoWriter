import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, Download } from "lucide-react";
import {
  exportTraceContent,
  getCaseContent,
  getDatasetCases,
  getGoldenRevision,
  listBenchmarkBatches,
  listBenchmarkRuns,
  type BenchmarkBatchSummary,
  type BenchmarkRunRow,
} from "@/lib/api";
import { downloadTextFile } from "@/lib/download";
import { DeliveriesView } from "@/components/bench/DeliveriesView";
import {
  DimensionCard,
  ScoreOverview,
  SeedCompareTable,
  STATUS_LABEL,
} from "@/components/bench/ScoreDetail";
import { batchStatusLabel, batchStatusClass } from "@/components/bench/batchStatus";

/**
 * 评测产物库（REQ-20260921-135543 FR-009；REQ-20260923-131103 改版）。
 *
 * 批次→case→seed 三级下钻。改版后：
 * - 批次列表带数据集指纹与均分列 + 加载更多（FR-002）
 * - case 卡片主显标题、均分升序（FR-003）
 * - seed 详情左右分栏：左评分右产物，需求折叠条（FR-004/005）
 * 三件套 Markdown 渲染（DeliveriesView）；调用链等交互式观测跳 trace
 * 详情页；导出：单篇 Markdown + 整 trace JSON。
 */

/** case 内 done 行 overall 均分；无 done 行返回 null（FR-003 排序键）。 */
function avgOverall(caseRows: BenchmarkRunRow[]): number | null {
  const vals = caseRows
    .map((r) => r.scores?.overall)
    .filter((v): v is number => v != null);
  if (vals.length === 0) return null;
  return vals.reduce((s, v) => s + v, 0) / vals.length;
}

export default function BenchArtifacts() {
  const [batches, setBatches] = useState<BenchmarkBatchSummary[]>([]);
  const [batchesLoading, setBatchesLoading] = useState(true);
  const [batchesError, setBatchesError] = useState<string | null>(null);
  // 加载更多（FR-002）：limit 递增重拉；total 为后端去重批次总数
  const [batchLimit, setBatchLimit] = useState(20);
  const [batchTotal, setBatchTotal] = useState<number | null>(null);

  // 三级下钻状态
  const [batchId, setBatchId] = useState<string | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [runId, setRunId] = useState<number | null>(null);

  const [rows, setRows] = useState<BenchmarkRunRow[]>([]);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  // case 人话标题映射（FR-003）：一次拉全量数据集元数据；失败静默降级回 case_id
  const [caseTitles, setCaseTitles] = useState<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    getDatasetCases()
      .then((resp) => {
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const c of resp.cases) map[c.case_id] = c.title;
        setCaseTitles(map);
      })
      .catch(() => {
        // 失败语义（FR-003）：回退显示 case_id，不隐藏卡片、不打断浏览
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setBatchesLoading(true);
    listBenchmarkBatches(batchLimit)
      .then((resp) => {
        if (cancelled) return;
        setBatches(resp.batches);
        setBatchTotal(resp.total ?? null);
        setBatchesError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setBatchesError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setBatchesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [batchLimit]);

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
    const entries = [...grouped.entries()].map(([cid, caseRows]) => ({
      caseId: cid,
      caseRows: [...caseRows].sort((a, b) => (a.seed ?? 0) - (b.seed ?? 0)),
      avg: avgOverall(caseRows),
    }));
    // FR-003/DEC-008：无均分（失败/未跑完）置前，其余均分升序（低分在前）
    entries.sort((a, b) => {
      if (a.avg == null && b.avg == null) return a.caseId < b.caseId ? -1 : 1;
      if (a.avg == null) return -1;
      if (b.avg == null) return 1;
      return a.avg - b.avg;
    });
    return entries;
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
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>批次</th>
                    <th>状态</th>
                    <th>数据集</th>
                    <th>均分</th>
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
                      <td
                        className="mono"
                        title={b.golden_revision ?? undefined}
                      >
                        {b.golden_revision ? `golden @ ${b.golden_revision.slice(0, 7)}` : "—"}
                      </td>
                      <td>
                        {b.avg_overall != null ? b.avg_overall.toFixed(2) : "—"}
                      </td>
                      <td>{b.harness_version != null ? `v${b.harness_version}` : "—"}</td>
                      <td>{b.progress.done}/{b.progress.total}</td>
                      <td>{b.triggered_at?.slice(0, 19).replace("T", " ") ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {batchTotal != null && batches.length < batchTotal && (
                <div className="bench-artifacts-more">
                  <button
                    type="button"
                    className="action-link"
                    disabled={batchesLoading}
                    onClick={() => setBatchLimit((n) => n + 20)}
                  >
                    加载更多（{batches.length}/{batchTotal}）
                  </button>
                </div>
              )}
            </>
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
              {byCase.map(({ caseId: cid, caseRows, avg }) => {
                const title = caseTitles[cid];
                return (
                  <article
                    key={cid}
                    className="bench-case-card bench-row-click"
                    onClick={() => setCaseId(cid)}
                  >
                    <header className="bench-case-head">
                      <div className="bench-case-title-wrap">
                        <span className="bench-case-title">{title ?? cid}</span>
                        {title && <span className="bench-case-id-sub mono">{cid}</span>}
                      </div>
                      {avg != null && (
                        <span className="bench-score-overview-value bench-dim-high" title="seed 总分均分">
                          {avg.toFixed(2)}
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
          {/* seed 切换器常驻顶部（FR-004：切换不退回 case 层） */}
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
                    className={`bench-seed-chip status-${r.status}${
                      r.id === runId ? " selected" : ""
                    }`}
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

          {activeRun && (
            <div className="bench-artifacts-run">
              <div className="bench-overall-row">
                <span>
                  总分{" "}
                  <strong>
                    {activeRun.scores?.overall != null ? activeRun.scores.overall.toFixed(2) : "—"}
                  </strong>
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
                <RunSplit
                  run={activeRun}
                  caseRows={byCase.find((c) => c.caseId === caseId)?.caseRows ?? []}
                  caseId={caseId}
                  batchGoldenRevision={
                    batches.find((b) => b.batch_id === batchId)?.golden_revision ?? null
                  }
                />
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/**
 * seed 详情左右分栏（FR-004/DEC-001）：左栏钉住评分（滚产物时保持视野），
 * 右栏滚动产物（需求折叠条 + 三件套）。窄窗口由 CSS 退化为上下堆叠。
 */
function RunSplit({
  run,
  caseRows,
  caseId,
  batchGoldenRevision,
}: {
  run: BenchmarkRunRow;
  caseRows: BenchmarkRunRow[];
  caseId: string;
  batchGoldenRevision: string | null;
}) {
  const [showCompare, setShowCompare] = useState(false);
  const [focusHint, setFocusHint] = useState<string | null>(null);
  const deliveryRef = useRef<HTMLDivElement | null>(null);

  // 维度卡片「查看相关交付」→ 右栏产物分组定位（与批次详情页同机制，DEC-011）
  function jumpToDelivery(hint: string | null) {
    if (hint) {
      setFocusHint(`${hint}:${Date.now()}`);
    } else {
      deliveryRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  const scores = run.scores;
  const hasDetail = !!scores?.scores && Object.keys(scores.scores).length > 0;
  const multiSeed = caseRows.filter((r) => r.status === "done").length >= 2;

  return (
    <div className="bench-run-split">
      <aside className="bench-run-score">
        {run.status !== "done" ? (
          <p className="bench-run-note">
            该行{STATUS_LABEL[run.status] ?? run.status}，暂无评分明细。
          </p>
        ) : !hasDetail ? (
          <p className="bench-run-note">
            明细不可用（评分标准版本过旧：rubric {run.rubric_version ?? "NULL"}）。
          </p>
        ) : (
          <>
            <div className="bench-rule-line">
              交付完整
              {scores!.rule_delivery?.passed == null
                ? "—"
                : scores!.rule_delivery!.passed
                  ? "：过"
                  : "：未过"}
            </div>
            <ScoreOverview scores={scores!.scores!} />
            {Object.entries(scores!.scores!).map(([dim, value]) => (
              <DimensionCard
                key={dim}
                dim={dim}
                value={value}
                reason={scores!.reasons?.[dim]}
                onJump={jumpToDelivery}
              />
            ))}
            {(scores!.rule_delivery?.problems?.length ?? 0) > 0 && (
              <div className="bench-rule-problems">
                <h5>交付完整问题</h5>
                <ul>
                  {(scores!.rule_delivery?.problems ?? []).map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              </div>
            )}
            {multiSeed && (
              <div className="bench-run-compare">
                <button
                  type="button"
                  className="action-link"
                  onClick={() => setShowCompare((v) => !v)}
                >
                  {showCompare ? "收起 seed 对比" : "seed 对比（本 case 全部 seed）"}
                </button>
                {showCompare && <SeedCompareTable rows={caseRows} />}
              </div>
            )}
          </>
        )}
      </aside>
      <div className="bench-run-product" ref={deliveryRef}>
        <DemandStrip caseId={caseId} batchGoldenRevision={batchGoldenRevision} />
        <DeliveriesView traceId={run.trace_id!} focusHint={focusHint} />
      </div>
    </div>
  );
}

/**
 * 需求原文折叠条（FR-005/DEC-005/007）：默认收起一行，展开渲染当前 demand.md；
 * 批次运行时指纹 ≠ 数据集当前指纹时黄色提示「数据集已更新」。
 */
function DemandStrip({
  caseId,
  batchGoldenRevision,
}: {
  caseId: string;
  batchGoldenRevision: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [demandMd, setDemandMd] = useState<string | null>(null);
  const [currentRevision, setCurrentRevision] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || demandMd != null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([getCaseContent(caseId), getGoldenRevision()])
      .then(([content, golden]) => {
        if (cancelled) return;
        setDemandMd(content.demand_md);
        setCurrentRevision(golden.revision);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, caseId, demandMd]);

  // 指纹不一致（DEC-007）：显示的已是当前版本需求，非运行时评分依据
  const mismatch =
    open &&
    demandMd != null &&
    currentRevision != null &&
    batchGoldenRevision != null &&
    currentRevision !== batchGoldenRevision;

  return (
    <div className="bench-demand-strip">
      <button
        type="button"
        className="bench-demand-toggle"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "收起需求" : "需求（demand）"}
      </button>
      {open && (
        <div className="bench-demand-body">
          {mismatch && (
            <div className="bench-demand-warn">
              数据集已更新，以下为当前版本需求（当前 @
              {currentRevision!.slice(0, 7)} ≠ 运行 @
              {batchGoldenRevision!.slice(0, 7)}）
            </div>
          )}
          {loading ? (
            <div className="page-loading">加载需求…</div>
          ) : error ? (
            <div className="bench-delivery-note">读取需求失败：{error}</div>
          ) : demandMd != null ? (
            <div className="prose-doc bench-demand-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{demandMd}</ReactMarkdown>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
