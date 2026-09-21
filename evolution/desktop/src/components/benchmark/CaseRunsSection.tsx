import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FileText, LoaderCircle, X } from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetClose,
} from "@/components/ui/sheet";
import {
  getArtifactRevisionContent,
  getBenchmarkDeliveries,
  listBenchmarkRuns,
  type BenchmarkDeliveriesResponse,
  type BenchmarkRunRow,
} from "@/lib/api";

/**
 * 评测批次 case 明细区（REQ-20260921-114943 FR-001/FR-004，DEC-007）。
 *
 * 报告视图内下探：case 聚合卡片（全 seed 状态徽标 + 分数，DEC-008）→
 * 点卡片开行明细抽屉（五维分/理由/标签动态渲染 DEC-006 + 三件套正文 FR-002
 * + 失败行下钻 FR-004）。pending/cancelled 只显示不计入聚合（聚合在报告层，
 * 本区只如实呈现行状态）。
 */

const STATUS_LABEL: Record<string, string> = {
  done: "完成",
  failed: "失败",
  pending: "未跑",
  running: "运行中",
  evaluating: "评分中",
  cancelled: "已取消",
};

export function CaseRunsSection({
  batchId,
  refreshKey,
}: {
  batchId: string;
  refreshKey: number;
}) {
  const [rows, setRows] = useState<BenchmarkRunRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const resp = await listBenchmarkRuns(batchId);
      setRows(resp.items);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [batchId]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh, refreshKey]);

  // 进行中批次随刷新同步更新（FR-001：有活跃行时 5s 轮询）
  const hasActive = rows.some((r) =>
    ["pending", "running", "evaluating"].includes(r.status),
  );
  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [hasActive, refresh]);

  const byCase = useMemo(() => {
    const grouped = new Map<string, BenchmarkRunRow[]>();
    for (const row of rows) {
      const list = grouped.get(row.case_id) ?? [];
      list.push(row);
      grouped.set(row.case_id, list);
    }
    return [...grouped.entries()].map(([caseId, caseRows]) => ({
      caseId,
      caseRows: [...caseRows].sort((a, b) => (a.seed ?? 0) - (b.seed ?? 0)),
    }));
  }, [rows]);

  const openRows = byCase.find((c) => c.caseId === openCase)?.caseRows ?? [];
  const activeRun = openRows.find((r) => r.id === activeRunId) ?? null;

  return (
    <div className="bench-case-wrap">
      <h4>case 明细</h4>
      {loading ? (
        <div className="page-loading">加载行明细…</div>
      ) : error ? (
        <div className="monitor-empty">读取行明细失败：{error}</div>
      ) : byCase.length === 0 ? (
        <div className="monitor-empty">该批次无行数据</div>
      ) : (
        <div className="bench-case-grid">
          {byCase.map(({ caseId, caseRows }) => (
            <article
              key={caseId}
              className="bench-case-card"
              onClick={() => {
                const first =
                  caseRows.find((r) => r.status === "done") ?? caseRows[0];
                setOpenCase(caseId);
                setActiveRunId(first.id);
              }}
            >
              <header className="bench-case-head">
                <span className="bench-case-id">{caseId}</span>
                <CaseSummary caseRows={caseRows} />
              </header>
              <div className="bench-seed-row">
                {caseRows.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    className={`bench-seed-chip status-${r.status}`}
                    title={STATUS_LABEL[r.status] ?? r.status}
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpenCase(caseId);
                      setActiveRunId(r.id);
                    }}
                  >
                    #{r.seed ?? "-"}
                    <span className="bench-seed-status">
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                    {r.scores?.overall != null && (
                      <span className="bench-seed-score">
                        {r.scores.overall.toFixed(2)}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}

      <Sheet
        open={openCase !== null}
        onOpenChange={(o) => {
          if (!o) {
            setOpenCase(null);
            setActiveRunId(null);
          }
        }}
      >
        <SheetContent side="right" className="bench-case-sheet">
          <SheetHeader>
            <SheetTitle>行明细 · {openCase ?? ""}</SheetTitle>
            <SheetClose asChild>
              <button className="evidence-sheet-close" aria-label="关闭抽屉">
                <X size={16} aria-hidden />
              </button>
            </SheetClose>
          </SheetHeader>
          {openCase && (
            <div className="bench-case-sheet-body">
              <div className="bench-seed-row bench-seed-row-drawer">
                {openRows.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    className={`bench-seed-chip status-${r.status}${
                      r.id === activeRunId ? " selected" : ""
                    }`}
                    onClick={() => setActiveRunId(r.id)}
                  >
                    #{r.seed ?? "-"}
                    <span className="bench-seed-status">
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                    {r.scores?.overall != null && (
                      <span className="bench-seed-score">
                        {r.scores.overall.toFixed(2)}
                      </span>
                    )}
                  </button>
                ))}
              </div>
              {activeRun && <RunDetail run={activeRun} />}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function CaseSummary({ caseRows }: { caseRows: BenchmarkRunRow[] }) {
  const done = caseRows.filter((r) => r.status === "done").length;
  const failed = caseRows.filter((r) => r.status === "failed").length;
  const parts = [`完成 ${done}/${caseRows.length}`];
  if (failed > 0) parts.push(`失败 ${failed}`);
  const cancelled = caseRows.filter((r) => r.status === "cancelled").length;
  if (cancelled > 0) parts.push(`取消 ${cancelled}`);
  return <span className="bench-case-stats">{parts.join(" · ")}</span>;
}

/** 行明细主体：评分明细 / 失败下钻 / 状态说明（同一抽屉，FR-001/FR-004）。 */
function RunDetail({ run }: { run: BenchmarkRunRow }) {
  if (run.status === "failed") {
    return (
      <div className="bench-run-detail">
        <div className="bench-error-box">
          <p className="bench-error-title">
            失败（重试 {run.retries} 次）
          </p>
          <p className="bench-error-msg">{run.error || "未知错误"}</p>
        </div>
        {run.trace_id ? (
          <p className="bench-run-note">
            该行已跑出 trace（生成完成、评分阶段失败）：
            <Link className="action-link" to={`/traces/${run.trace_id}?tab=artifacts`}>
              在运行观测中查看已产出产物
            </Link>
          </p>
        ) : (
          <p className="bench-run-note">生成阶段失败，无产物可查看。</p>
        )}
      </div>
    );
  }

  if (run.status !== "done") {
    return (
      <div className="bench-run-detail">
        <p className="bench-run-note">
          该行{STATUS_LABEL[run.status] ?? run.status}，暂无评分明细。
        </p>
        {run.trace_id && (
          <p className="bench-run-note">
            <Link className="action-link" to={`/traces/${run.trace_id}?tab=artifacts`}>
              在运行观测中查看该 trace
            </Link>
          </p>
        )}
      </div>
    );
  }

  const scores = run.scores;
  if (!scores?.scores || Object.keys(scores.scores).length === 0) {
    // DEC-006：旧批次无结构化评分数据 → 降级提示，聚合照常在报告层呈现
    return (
      <div className="bench-run-detail">
        <p className="bench-run-note">
          明细不可用（评分标准版本过旧：rubric {run.rubric_version ?? "NULL"}）。
        </p>
      </div>
    );
  }

  const rulePassed = scores.rule_delivery?.passed;
  return (
    <div className="bench-run-detail">
      <div className="bench-overall-row">
        <span>总分 <strong>{scores.overall?.toFixed(2) ?? "—"}</strong></span>
        <span>
          交付完整
          {rulePassed == null ? "—" : rulePassed ? "：过" : "：未过"}
        </span>
        {run.trace_id && (
          <Link className="action-link" to={`/traces/${run.trace_id}?tab=artifacts`}>
            运行观测产物
          </Link>
        )}
      </div>

      {Object.entries(scores.scores).map(([dim, value]) => (
        <div key={dim} className="bench-dim-block">
          <div className="bench-dim-head">
            <span className="bench-dim-name">{dim}</span>
            <span className={`bench-dim-score ${scoreClass(value)}`}>
              {value}/5
            </span>
          </div>
          {(scores.tags?.[dim]?.length ?? 0) > 0 && (
            <div className="bench-tag-row">
              {scores.tags?.[dim].map((tag) => (
                <span key={tag} className="bench-tag-chip">{tag}</span>
              ))}
            </div>
          )}
          {scores.reasons?.[dim] && (
            <p className="bench-dim-reason">{scores.reasons[dim]}</p>
          )}
        </div>
      ))}

      {(scores.rule_delivery?.problems?.length ?? 0) > 0 && (
        <div className="bench-rule-problems">
          <h5>交付完整问题</h5>
          <ul>
            {(scores.rule_delivery?.problems ?? []).map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      {run.trace_id && <DeliveriesBlock traceId={run.trace_id} />}
    </div>
  );
}

function scoreClass(value: number): string {
  if (value <= 2) return "bench-dim-low";
  if (value <= 3) return "bench-dim-mid";
  return "bench-dim-high";
}

/** 三件套正文块（FR-002：懒加载 + 无权/过期/缺失降级占位）。 */
function DeliveriesBlock({ traceId }: { traceId: string }) {
  const [data, setData] = useState<BenchmarkDeliveriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [contents, setContents] = useState<
    Record<string, { status: "loading" } | { status: "open"; content: unknown } | { status: "error"; message: string }>
  >({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setContents({});
    setExpanded(new Set());
    getBenchmarkDeliveries(traceId)
      .then((resp) => {
        if (!cancelled) setData(resp);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [traceId]);

  function toggleFile(revisionId: string) {
    const next = new Set(expanded);
    if (next.has(revisionId)) {
      next.delete(revisionId);
      setExpanded(next);
      return;
    }
    next.add(revisionId);
    setExpanded(next);
    if (contents[revisionId]) return; // 已加载过，直接展开
    setContents((prev) => ({ ...prev, [revisionId]: { status: "loading" } }));
    getArtifactRevisionContent(revisionId)
      .then((resp) => {
        setContents((prev) => ({
          ...prev,
          [revisionId]: { status: "open", content: resp.content },
        }));
      })
      .catch((e) => {
        setContents((prev) => ({
          ...prev,
          [revisionId]: {
            status: "error",
            message: e instanceof Error ? e.message : String(e),
          },
        }));
      });
  }

  if (loading) {
    return (
      <div className="bench-delivery">
        <h5>大纲三件套（评分依据）</h5>
        <div className="page-loading">加载三件套…</div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="bench-delivery">
        <h5>大纲三件套（评分依据）</h5>
        <div className="monitor-empty">读取三件套失败：{error}</div>
      </div>
    );
  }

  return (
    <div className="bench-delivery">
      <h5>大纲三件套（评分依据）</h5>
      {!data?.can_read_content && (
        <div className="bench-delivery-note">无权查看正文（仅超级管理员可按需读取）。</div>
      )}
      {data?.groups.map((group) => (
        <div key={group.display} className="bench-delivery-group">
          <div className="bench-delivery-head">
            <FileText size={13} aria-hidden />
            <span>{group.display}</span>
          </div>
          {group.files.length === 0 ? (
            <div className="bench-delivery-note">缺失</div>
          ) : (
            group.files.map((file) => {
              const revisionId = file.artifact_revision_id ?? "";
              const isOpen = expanded.has(revisionId);
              const state = contents[revisionId];
              const canOpen =
                data.can_read_content && file.available && revisionId !== "";
              return (
                <div key={`${file.logical_key}:${revisionId}`} className="bench-delivery-file">
                  <button
                    type="button"
                    className="bench-delivery-toggle"
                    disabled={!canOpen}
                    onClick={() => toggleFile(revisionId)}
                    title={
                      !data.can_read_content
                        ? "无权查看正文"
                        : !file.available
                          ? "正文已过期（保留 90 天）"
                          : isOpen
                            ? "收起正文"
                            : "查看正文"
                    }
                  >
                    <span className="bench-delivery-path" title={file.logical_key}>
                      {file.logical_key}
                    </span>
                    {state?.status === "loading" ? (
                      <LoaderCircle className="artifact-content-spinner" size={13} />
                    ) : !file.available ? (
                      <span className="bench-delivery-note">正文已过期（保留 90 天）</span>
                    ) : !data.can_read_content ? null : (
                      <span className="bench-delivery-note">{isOpen ? "收起" : "正文"}</span>
                    )}
                  </button>
                  {state?.status === "error" && (
                    <div className="bench-delivery-note">读取正文失败：{state.message}</div>
                  )}
                  {isOpen && state?.status === "open" && (
                    <DeliveryContent content={state.content} />
                  )}
                </div>
              );
            })
          )}
        </div>
      ))}
    </div>
  );
}

function DeliveryContent({ content }: { content: unknown }) {
  if (typeof content === "string") {
    return <pre className="bench-delivery-content">{content}</pre>;
  }
  return (
    <pre className="bench-delivery-content">{JSON.stringify(content, null, 2)}</pre>
  );
}
