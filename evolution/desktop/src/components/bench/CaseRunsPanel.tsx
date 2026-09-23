import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetClose,
} from "@/components/ui/sheet";
import { listBenchmarkRuns, type BenchmarkRunRow } from "@/lib/api";
import { DeliveriesView } from "@/components/bench/DeliveriesView";
import {
  DimensionCard,
  ScoreOverview,
  SeedCompareTable,
  STATUS_LABEL,
} from "@/components/bench/ScoreDetail";

/**
 * 评测批次 case 明细面板（REQ-20260921-135543 FR-005，DEC-009/010/011）。
 *
 * case 聚合卡片 → 行明细抽屉：
 * - 五维概览（条形）+ 维度卡片（分值/两段理由默认展开分色/关联交付跳转）
 * - seed 并排对比（同维极差 ≥2 高亮 = 输出不稳定）
 * - 三件套 Markdown 渲染（DeliveriesView，FR-006）
 * - 低分下钻目标区（focusCase：报告低分行点击直达）
 *
 * 评分展示组件已抽至 ScoreDetail（REQ-20260923-131103 FR-004：产物页复用）。
 */

export type CaseFocus = {
  caseId: string;
  seed?: number;
  /** 每次下钻递增，保证同目标重复点击也能触发定位 */
  nonce: number;
};

export function CaseRunsPanel({
  batchId,
  refreshKey,
  focusCase,
}: {
  batchId: string;
  refreshKey: number;
  focusCase: CaseFocus | null;
}) {
  const [rows, setRows] = useState<BenchmarkRunRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  // 维度卡片 → 交付分组定位提示（DEC-011；null=滚动到交付区整体）
  const [focusHint, setFocusHint] = useState<string | null>(null);
  const deliveryRef = useRef<HTMLDivElement | null>(null);

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

  // 进行中批次随刷新同步更新（有活跃行时 5s 轮询）
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

  // 低分下钻定位（DEC-014）：打开目标 case 抽屉并选中对应 seed 行。
  // nonce 消费守卫：同一下钻只应用一次——否则批次轮询期间 byCase 换引用
  // 反复触发本 effect，重开用户已关闭的抽屉/抢回 seed 选择。
  const appliedNonceRef = useRef<number>(-1);
  useEffect(() => {
    if (!focusCase) return;
    if (appliedNonceRef.current === focusCase.nonce) return;
    const target = byCase.find((c) => c.caseId === focusCase.caseId);
    if (!target) return; // runs 尚未到达，等下轮（nonce 保持未消费）
    appliedNonceRef.current = focusCase.nonce;
    const row =
      target.caseRows.find((r) => r.seed === focusCase.seed) ??
      target.caseRows.find((r) => r.status === "done") ??
      target.caseRows[0];
    setOpenCase(focusCase.caseId);
    setActiveRunId(row.id);
  }, [focusCase, byCase]);

  function jumpToDelivery(hint: string | null) {
    if (hint) {
      setFocusHint(`${hint}:${Date.now()}`); // 拼时间戳让同分组重复点击也能再次定位
    } else {
      setFocusHint(null);
      deliveryRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

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
              className={`bench-case-card${focusCase?.caseId === caseId ? " bench-case-focused" : ""}`}
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
              {openRows.length >= 2 && <SeedCompareTable rows={openRows} />}
              {activeRun && (
                <RunDetail
                  run={activeRun}
                  focusHint={focusHint}
                  deliveryRef={deliveryRef}
                  onJumpDelivery={jumpToDelivery}
                />
              )}
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

/** 行明细主体：评分区（概览+维度卡片）/ 失败下钻 / 状态说明。 */
function RunDetail({
  run,
  focusHint,
  deliveryRef,
  onJumpDelivery,
}: {
  run: BenchmarkRunRow;
  focusHint: string | null;
  deliveryRef: React.RefObject<HTMLDivElement | null>;
  onJumpDelivery: (hint: string | null) => void;
}) {
  if (run.status === "failed") {
    return (
      <div className="bench-run-detail">
        <div className="bench-error-box">
          <p className="bench-error-title">失败（重试 {run.retries} 次）</p>
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

      {/* 五维概览（DEC-009：条形，扫一眼） */}
      <ScoreOverview scores={scores.scores} />

      {/* 维度卡片（DEC-011 of REQ-20260921-210038：两段理由默认展开分色 + 关联交付跳转） */}
      {Object.entries(scores.scores).map(([dim, value]) => (
        <DimensionCard
          key={dim}
          dim={dim}
          value={value}
          reason={scores.reasons?.[dim]}
          onJump={onJumpDelivery}
        />
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

      {run.trace_id && (
        <div ref={deliveryRef}>
          <DeliveriesView traceId={run.trace_id} focusHint={focusHint} />
        </div>
      )}
    </div>
  );
}
