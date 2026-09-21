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
import { DeliveriesView, groupsForDimension } from "@/components/bench/DeliveriesView";

/**
 * 评测批次 case 明细面板（REQ-20260921-135543 FR-005，DEC-009/010/011）。
 *
 * case 聚合卡片 → 行明细抽屉：
 * - 五维概览（条形）+ 维度卡片（分值/两段理由默认展开分色/关联交付跳转）
 * - seed 并排对比（同维极差 ≥2 高亮 = 输出不稳定）
 * - 三件套 Markdown 渲染（DeliveriesView，FR-006）
 * - 低分下钻目标区（focusCase：报告低分行点击直达）
 */

const STATUS_LABEL: Record<string, string> = {
  done: "完成",
  failed: "失败",
  pending: "未跑",
  running: "运行中",
  evaluating: "评分中",
  cancelled: "已取消",
};

/** seed 间同维极差达到该值视为不稳定（1-5 分制下 2 分 = 跨档波动）。 */
const UNSTABLE_SPREAD = 2;

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

/** seed 并排对比（DEC-010：行=维度，列=seed；同维极差 ≥2 高亮）。 */
function SeedCompareTable({ rows }: { rows: BenchmarkRunRow[] }) {
  const dims = useMemo(() => {
    const seen: string[] = [];
    for (const r of rows) {
      for (const dim of Object.keys(r.scores?.scores ?? {})) {
        if (!seen.includes(dim)) seen.push(dim);
      }
    }
    return seen;
  }, [rows]);

  if (dims.length === 0) return null;

  return (
    <div className="bench-seed-compare">
      <h5>seed 对比（红格 = 同维极差 ≥{UNSTABLE_SPREAD}，输出不稳定信号）</h5>
      <table className="data-table">
        <thead>
          <tr>
            <th>维度</th>
            {rows.map((r) => (
              <th key={r.id}>
                #{r.seed ?? "-"}
                <span className="bench-seed-col-status"> {STATUS_LABEL[r.status] ?? r.status}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dims.map((dim) => {
            const nums = rows
              .map((r) => r.scores?.scores?.[dim])
              .filter((v): v is number => typeof v === "number");
            const spread =
              nums.length >= 2 ? Math.max(...nums) - Math.min(...nums) : null;
            const unstable = spread != null && spread >= UNSTABLE_SPREAD;
            return (
              <tr key={dim}>
                <td>{dim}</td>
                {rows.map((r) => {
                  const v = r.scores?.scores?.[dim];
                  return (
                    <td
                      key={r.id}
                      className={unstable && typeof v === "number" ? "bench-seed-unstable" : ""}
                      title={unstable ? `极差 ${spread?.toFixed(1)} 分` : undefined}
                    >
                      {typeof v === "number" ? v.toFixed(1) : "—"}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          <tr>
            <td>总分</td>
            {rows.map((r) => (
              <td key={r.id}>
                {r.scores?.overall != null ? r.scores.overall.toFixed(2) : "—"}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
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

function ScoreOverview({ scores }: { scores: Record<string, number> }) {
  return (
    <div className="bench-score-overview">
      {Object.entries(scores).map(([dim, v]) => (
        <div key={dim} className="bench-score-overview-row">
          <span className="bench-score-overview-dim">{dim}</span>
          <div className="bench-score-bar">
            <div className="bench-score-fill" style={{ width: `${(v / 5) * 100}%` }} />
          </div>
          <span className={`bench-score-overview-value ${scoreClass(v)}`}>
            {v.toFixed(1)}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * 维度卡片：分值 + 两段理由（达标/不足）默认展开分色（DEC-011 of REQ-20260921-210038）。
 * 旧批次理由为字符串（v3 及更早）→ 原样展示并标「旧版规则」；无理由 → 占位说明。
 */
function DimensionCard({
  dim,
  value,
  reason,
  onJump,
}: {
  dim: string;
  value: number;
  reason?: string | { 达标: string[]; 不足: string[] };
  onJump: (hint: string | null) => void;
}) {
  const hint = groupsForDimension(dim);
  return (
    <div className="bench-dim-card">
      <div className="bench-dim-head">
        <span className="bench-dim-name">{dim}</span>
        <span className={`bench-dim-score ${scoreClass(value)}`}>{value}/5</span>
      </div>
      {reason == null ? (
        <p className="bench-reason-absent">旧版规则评分，未生成理由</p>
      ) : typeof reason === "string" ? (
        <div className="bench-dim-reasons">
          <p className="bench-dim-reason bench-dim-reason-legacy">
            <span className="bench-reason-legacy-tag">旧版理由</span>
            {reason}
          </p>
        </div>
      ) : (
        <div className="bench-dim-reasons">
          <div className="bench-dim-reason-list bench-dim-reason-met">
            <span className="bench-dim-reason-label">✓ 达标</span>
            <ul>
              {reason.达标.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>
          <div className="bench-dim-reason-list bench-dim-reason-flaw">
            <span className="bench-dim-reason-label">⚠ 不足</span>
            <ul>
              {reason.不足.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      <button
        type="button"
        className="action-link"
        onClick={() => onJump(hint ? hint[0] : null)}
      >
        查看相关交付{hint ? `（${hint[0]}）` : "（全部三件套）"}
      </button>
    </div>
  );
}

function scoreClass(value: number): string {
  if (value <= 2) return "bench-dim-low";
  if (value <= 3) return "bench-dim-mid";
  return "bench-dim-high";
}
