import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  getBenchmarkReport,
  listBenchmarkBatches,
  compareBatches,
  type BenchmarkBatchSummary,
  type BenchmarkCompare,
  type BenchmarkReport,
} from "@/lib/api";
import { CaseRunsPanel, type CaseFocus } from "@/components/bench/CaseRunsPanel";

/**
 * 批次详情页（REQ-20260921-135543 FR-004/FR-005，DEC-006）。
 *
 * 弱点报告（低分条目点击直达 case 明细，DEC-014）+
 * case 明细评分区（DEC-009/010/011）+ A/B 对比（DEC-006）。
 * URL 含批次 id，可收藏可回溯。
 */
export default function BenchBatchDetail() {
  const { batchId = "" } = useParams<{ batchId: string }>();

  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportRefreshKey, setReportRefreshKey] = useState(0);

  // 低分下钻目标（DEC-014：低分行点击 → 打开对应 case 明细）
  const [focusCase, setFocusCase] = useState<CaseFocus | null>(null);
  const caseSectionRef = useRef<HTMLDivElement | null>(null);

  // A/B 对比（A 默认当前批次）
  const [batches, setBatches] = useState<BenchmarkBatchSummary[]>([]);
  const [cmpB, setCmpB] = useState("");
  const [comparing, setComparing] = useState(false);
  const [compare, setCompare] = useState<BenchmarkCompare | null>(null);

  const loadReport = useCallback(async () => {
    if (!batchId) return;
    setReportLoading(true);
    setReportError(null);
    try {
      const r = await getBenchmarkReport(batchId);
      setReport(r);
    } catch (err) {
      setReportError(err instanceof Error ? err.message : String(err));
    } finally {
      setReportLoading(false);
    }
  }, [batchId]);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  useEffect(() => {
    listBenchmarkBatches(20)
      .then((resp) => setBatches(resp.batches))
      .catch(() => {
        // 对比下拉加载失败不阻塞报告；下拉退化为空
      });
  }, []);

  function focusLowCase(caseId: string, seed: number | null) {
    setFocusCase({ caseId, seed: seed ?? undefined, nonce: Date.now() });
    setReportRefreshKey((k) => k + 1);
    caseSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function handleCompare() {
    if (!cmpB) {
      toast.error("请选择基线批次（B）");
      return;
    }
    setComparing(true);
    setCompare(null);
    try {
      const result = await compareBatches(batchId, cmpB);
      setCompare(result);
      if (!result.comparable) {
        toast.error(`不可比：${result.problems?.join("；") ?? "指纹不一致"}`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "对比失败");
    } finally {
      setComparing(false);
    }
  }

  return (
    <div className="bench-page bench-batch-detail">
      <header className="page-header">
        <h1>批次详情 · <span className="mono">{batchId.slice(0, 8)}</span></h1>
        <p className="page-desc">弱点报告 · case 明细 · 版本对比</p>
      </header>

      {/* 弱点报告（FR-004） */}
      <section className="bench-report-section">
        <h3>弱点报告</h3>
        {reportLoading ? (
          <div className="page-loading">加载报告…</div>
        ) : reportError ? (
          <div className="error-card">
            <span className="error-icon">⚠</span>
            <div className="error-body">
              <div className="error-title">报告加载失败</div>
              <div className="error-desc">{reportError}</div>
            </div>
            <button className="action-link" onClick={loadReport}>重试</button>
          </div>
        ) : !report || report.status !== "ok" ? (
          <div className="monitor-empty">
            {report?.message ?? "该批次暂无可聚合数据"}
          </div>
        ) : (
          <>
            <div className="bench-calibration-note">
              校准状态 <strong>{report.calibration}</strong> · 锚点 {report.anchor_status}
              （分数只用于同指纹前缀的相对对比，绝对值不作质量结论）
            </div>

            <div className="bench-report-grid">
              {/* 维度均分 */}
              <div className="bench-report-block">
                <h4>维度均分（弱→强）</h4>
                <table className="data-table">
                  <thead>
                    <tr><th>维度</th><th>均分</th><th>样本</th><th>分布</th></tr>
                  </thead>
                  <tbody>
                    {(report.dimensions ?? []).map((d) => (
                      <tr key={d.dimension}>
                        <td>{d.dimension}</td>
                        <td className={dimScoreClass(d.mean)}>{d.mean?.toFixed(2) ?? "—"}</td>
                        <td>{d.n}</td>
                        <td>
                          <div className="bench-score-bar">
                            <div
                              className="bench-score-fill"
                              style={{ width: `${((d.mean ?? 0) / 5) * 100}%` }}
                            />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {report.rule_delivery_failed ? (
                  <div className="bench-rule-warn">
                    交付完整规则项未过：{report.rule_delivery_failed} 条
                  </div>
                ) : null}
              </div>
            </div>

            {/* 低分 case（行点击直达明细，DEC-014） */}
            <div className="bench-report-block">
              <h4>低分条目（点击行查看明细）</h4>
              {(report.low_cases ?? []).length === 0 ? (
                <div className="monitor-empty">无</div>
              ) : (
                <table className="data-table bench-lowcases-table">
                  <thead>
                    <tr><th>Case</th><th>Seed</th><th>总分</th><th>交付完整</th></tr>
                  </thead>
                  <tbody>
                    {(report.low_cases ?? []).map((c, i) => (
                      <tr
                        key={`${c.case_id}-${c.seed}-${i}`}
                        className="bench-row-click"
                        onClick={() => focusLowCase(c.case_id, c.seed)}
                        title="点击查看该行评分明细与产物"
                      >
                        <td className="mono">{c.case_id}</td>
                        <td>#{c.seed}</td>
                        <td>{c.overall?.toFixed(2) ?? "—"}</td>
                        <td>{c.rule_delivery_passed ? "过" : "未过"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {(report.failed_rows ?? []).length > 0 && (
                <div className="bench-rule-warn">
                  失败行 {report.failed_rows!.length} 条（生成或评分失败，不计入均分）
                </div>
              )}
            </div>
          </>
        )}
      </section>

      {/* case 明细（FR-005：评分区重做，低分下钻目标区） */}
      <div ref={caseSectionRef}>
        <CaseRunsPanel
          batchId={batchId}
          refreshKey={reportRefreshKey}
          focusCase={focusCase}
        />
      </div>

      {/* A/B 对比（FR-004：A=当前批次固定，B 选基线） */}
      <section className="bench-compare-section">
        <h3>版本对比（CI 三态）</h3>
        <div className="test-form">
          <label className="test-field">
            <span>候选（A，当前批次）</span>
            <input className="config-input mono" value={batchId.slice(0, 8)} disabled />
          </label>
          <label className="test-field">
            <span>基线（B，通常为 production）</span>
            <select className="evolve-select" value={cmpB} onChange={(e) => setCmpB(e.target.value)}>
              <option value="">选择批次…</option>
              {batches
                .filter((b) => b.batch_id !== batchId && b.status !== "running")
                .map((b) => (
                  <option key={b.batch_id} value={b.batch_id}>
                    {b.batch_id.slice(0, 8)}（v{b.harness_version ?? "?"} · {b.progress.done}分）
                  </option>
                ))}
            </select>
          </label>
          <button className="config-button primary" onClick={handleCompare} disabled={comparing || !cmpB}>
            {comparing ? "对比中…" : "对比"}
          </button>
        </div>

        {compare && compare.comparable && compare.total && (
          <div className="bench-compare-result">
            <div className={`bench-verdict ${compare.total.verdict}`}>
              {verdictLabel(compare.total.verdict)}
            </div>
            {compare.total.verdict === "insufficient" ? (
              // 样本不足：后端只回 verdict/reason/n_*，无统计字段（stats.py 快退路径）
              <div className="bench-rule-warn">
                {compare.total.reason ?? "样本不足"}
                （候选 n={compare.total.n_candidate} · 基线 n={compare.total.n_production}；
                每侧至少 2 条有效评分才能对比——加 seed 或选更大批次）
              </div>
            ) : (
              <>
                {!compare.total.sufficient_power && (
                  <div className="bench-rule-warn">统计力不足（有效评分 &lt; 10/组），结论仅参考</div>
                )}
                <table className="data-table">
                  <tbody>
                    <tr><td>候选均分（A）</td><td>{compare.total.mean_candidate?.toFixed(3) ?? "—"}（n={compare.total.n_candidate}）</td></tr>
                    <tr><td>基线均分（B）</td><td>{compare.total.mean_production?.toFixed(3) ?? "—"}（n={compare.total.n_production}）</td></tr>
                    <tr><td>候选 95% CI</td><td>[{compare.total.ci_95_low?.toFixed(3) ?? "—"}, {compare.total.ci_95_high?.toFixed(3) ?? "—"}]</td></tr>
                    <tr><td>均值差（A−B）</td><td>{compare.total.delta_mean?.toFixed(3) ?? "—"}</td></tr>
                  </tbody>
                </table>
                {compare.dimensions && Object.keys(compare.dimensions).length > 0 && (
                  <table className="data-table">
                    <thead>
                      <tr><th>维度</th><th>候选</th><th>基线</th><th>差值</th></tr>
                    </thead>
                    <tbody>
                      {Object.entries(compare.dimensions).map(([dim, sides]) => {
                        const a = sides.candidate?.mean;
                        const b = sides.production?.mean;
                        const delta = a != null && b != null ? a - b : null;
                        return (
                          <tr key={dim}>
                            <td>{dim}</td>
                            <td>{a?.toFixed(2) ?? "—"}</td>
                            <td>{b?.toFixed(2) ?? "—"}</td>
                            <td className={delta == null ? "" : delta > 0 ? "bench-delta-up" : delta < 0 ? "bench-delta-down" : ""}>
                              {delta != null ? `${delta > 0 ? "+" : ""}${delta.toFixed(2)}` : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </>
            )}
            <p className="bench-compare-note">
              tie = 测不出差异（≠ 没有差异）；维度差值只报告不判胜负。
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function verdictLabel(v: string): string {
  const map: Record<string, string> = {
    win: "WIN — 候选显著更好",
    tie: "TIE — 测不出差异",
    lose: "LOSE — 候选显著更差",
    insufficient: "样本不足",
  };
  return map[v] ?? v;
}

function dimScoreClass(mean: number | null): string {
  if (mean == null) return "";
  if (mean < 3) return "bench-dim-low";
  if (mean < 3.5) return "bench-dim-mid";
  return "bench-dim-high";
}
