import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import {
  runBenchmark,
  listBenchmarkBatches,
  getBenchmarkReport,
  compareBatches,
  type BenchmarkBatchSummary,
  type BenchmarkReport,
  type BenchmarkCompare,
} from "@/lib/api";

/**
 * 评测页（REQ-20260919-172934 / FR-008 增量）。
 *
 * 工作流：
 * 1. 触发评测批次（golden 全 case × N seed × 指定 harness 版本）
 * 2. 批次列表轮询进度（running 时 5s 刷新）
 * 3. 点选批次看弱点报告（维度均分 + 缺陷标签 + 低分 case）
 * 4. 选两个批次做 CI 三态对比（win / tie / lose）
 */
export default function BenchmarkPage() {
  const [batches, setBatches] = useState<BenchmarkBatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);

  // 触发表单
  const [versionInput, setVersionInput] = useState("");
  const [seeds, setSeeds] = useState(3);

  // 选中批次的报告
  const [selectedBatch, setSelectedBatch] = useState("");
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);

  // 对比
  const [cmpA, setCmpA] = useState("");
  const [cmpB, setCmpB] = useState("");
  const [comparing, setComparing] = useState(false);
  const [compare, setCompare] = useState<BenchmarkCompare | null>(null);

  const refresh = useCallback(async () => {
    try {
      const resp = await listBenchmarkBatches(20);
      setBatches(resp.batches);
    } catch {
      // 轮询失败静默（下一轮重试）；首次加载失败要提示
      if (loading) toast.error("读取评测批次失败");
    } finally {
      setLoading(false);
    }
  }, [loading]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function handleRun() {
    setStarting(true);
    try {
      const payload: { version?: number; seeds: number } = { seeds };
      const v = versionInput.trim();
      if (v) payload.version = Number(v);
      const resp = await runBenchmark(payload);
      toast.success(`评测批次已触发：${resp.batch_id.slice(0, 8)}（${resp.progress.total} 次生成+评分）`);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "触发评测失败");
    } finally {
      setStarting(false);
    }
  }

  async function loadReport(batchId: string) {
    setSelectedBatch(batchId);
    setReport(null);
    setReportLoading(true);
    try {
      const r = await getBenchmarkReport(batchId);
      setReport(r);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "读取报告失败");
    } finally {
      setReportLoading(false);
    }
  }

  async function handleCompare() {
    if (!cmpA || !cmpB) {
      toast.error("请选择两个批次（A=候选，B=基线）");
      return;
    }
    if (cmpA === cmpB) {
      toast.error("两个批次不能相同");
      return;
    }
    setComparing(true);
    setCompare(null);
    try {
      const result = await compareBatches(cmpA, cmpB);
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

  const hasActive = batches.some((b) => b.status === "running");

  if (loading) return <div className="page-loading">加载评测批次…</div>;

  return (
    <div className="benchmark-page">
      <header className="page-header">
        <h1>评测</h1>
        <p className="page-desc">
          golden 数据集 × harness 版本 × N seed → LLM 评分 → 弱点报告 / 版本对比
          {hasActive && "（有批次运行中，5 秒自动刷新）"}
        </p>
      </header>

      {/* 触发评测 */}
      <section className="test-start">
        <h3>触发评测</h3>
        <div className="test-form">
          <label className="test-field">
            <span>Harness 版本（空 = 当前 production）</span>
            <input
              className="config-input"
              value={versionInput}
              onChange={(e) => setVersionInput(e.target.value)}
              placeholder="如 12"
              disabled={starting}
            />
          </label>
          <label className="test-field">
            <span>每 case 重复（seed）</span>
            <select
              className="evolve-select"
              value={seeds}
              onChange={(e) => setSeeds(Number(e.target.value))}
              disabled={starting}
            >
              <option value={1}>1</option>
              <option value={3}>3（推荐）</option>
              <option value={5}>5</option>
            </select>
          </label>
          <button className="config-button primary" onClick={handleRun} disabled={starting}>
            {starting ? "触发中…" : "触发全量评测"}
          </button>
        </div>
      </section>

      {/* 批次列表 */}
      <section className="bench-list-section">
        <h3>批次</h3>
        {batches.length === 0 ? (
          <div className="monitor-empty">暂无评测批次——先触发一次</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>批次</th>
                <th>状态 / 进度</th>
                <th>harness</th>
                <th>rubric</th>
                <th>触发时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => (
                <tr
                  key={b.batch_id}
                  className={b.batch_id === selectedBatch ? "bench-row-selected" : ""}
                >
                  <td title={b.batch_id}>{b.batch_id.slice(0, 8)}</td>
                  <td>
                    <span className={`session-status ${batchStatusClass(b.status)}`}>
                      {batchStatusLabel(b.status)}
                    </span>{" "}
                    {b.progress.done}/{b.progress.total}
                    {b.progress.failed > 0 && (
                      <span className="bench-fail-count">（失败 {b.progress.failed}）</span>
                    )}
                  </td>
                  <td>{b.harness_version != null ? `v${b.harness_version}` : "—"}</td>
                  <td title={b.golden_revision ?? ""}>{b.rubric_version ?? "—"}</td>
                  <td>{b.triggered_at?.slice(0, 19).replace("T", " ") ?? "—"}</td>
                  <td>
                    <button className="action-link" onClick={() => loadReport(b.batch_id)}>
                      报告
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* 弱点报告 */}
      {selectedBatch && (
        <section className="bench-report-section">
          <h3>弱点报告 · {selectedBatch.slice(0, 8)}</h3>
          {reportLoading ? (
            <div className="page-loading">加载报告…</div>
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
                </div>

                {/* 标签命中 */}
                <div className="bench-report-block">
                  <h4>高频缺陷标签</h4>
                  {(report.tag_hits ?? []).length === 0 ? (
                    <div className="monitor-empty">无低分标签命中</div>
                  ) : (
                    <ul className="bench-tag-list">
                      {(report.tag_hits ?? []).map((t) => (
                        <li key={t.tag}>
                          <span className="bench-tag-name">{t.tag}</span>
                          <span className="bench-tag-hits">×{t.hits}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {report.rule_delivery_failed ? (
                    <div className="bench-rule-warn">
                      交付完整规则项未过：{report.rule_delivery_failed} 条
                    </div>
                  ) : null}
                </div>
              </div>

              {/* 低分 case + 失败行 */}
              <div className="bench-report-block">
                <h4>低分条目</h4>
                {(report.low_cases ?? []).length === 0 ? (
                  <div className="monitor-empty">无</div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr><th>Case</th><th>Seed</th><th>总分</th><th>缺陷标签</th><th>交付完整</th></tr>
                    </thead>
                    <tbody>
                      {(report.low_cases ?? []).map((c, i) => (
                        <tr key={`${c.case_id}-${c.seed}-${i}`}>
                          <td>{c.case_id}</td>
                          <td>#{c.seed}</td>
                          <td>{c.overall?.toFixed(2) ?? "—"}</td>
                          <td>
                            {Object.entries(c.tags ?? {}).flatMap(([dim, tags]) =>
                              tags.map((t) => `${dim}:${t}`),
                            ).join("、") || "—"}
                          </td>
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
      )}

      {/* 版本对比 */}
      <section className="bench-compare-section">
        <h3>版本对比（CI 三态）</h3>
        <div className="test-form">
          <label className="test-field">
            <span>候选（A）</span>
            <select className="evolve-select" value={cmpA} onChange={(e) => setCmpA(e.target.value)}>
              <option value="">选择批次…</option>
              {batches.filter((b) => b.status !== "running").map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.batch_id.slice(0, 8)}（v{b.harness_version ?? "?"} · {b.progress.done}分）
                </option>
              ))}
            </select>
          </label>
          <label className="test-field">
            <span>基线（B，通常为 production）</span>
            <select className="evolve-select" value={cmpB} onChange={(e) => setCmpB(e.target.value)}>
              <option value="">选择批次…</option>
              {batches.filter((b) => b.status !== "running").map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.batch_id.slice(0, 8)}（v{b.harness_version ?? "?"} · {b.progress.done}分）
                </option>
              ))}
            </select>
          </label>
          <button className="config-button primary" onClick={handleCompare} disabled={comparing}>
            {comparing ? "对比中…" : "对比"}
          </button>
        </div>

        {compare && compare.comparable && compare.total && (
          <div className="bench-compare-result">
            <div className={`bench-verdict ${compare.total.verdict}`}>
              {verdictLabel(compare.total.verdict)}
            </div>
            {!compare.total.sufficient_power && (
              <div className="bench-rule-warn">统计力不足（有效评分 &lt; 10/组），结论仅参考</div>
            )}
            <table className="data-table">
              <tbody>
                <tr><td>候选均分（A）</td><td>{compare.total.mean_candidate.toFixed(3)}（n={compare.total.n_candidate}）</td></tr>
                <tr><td>基线均分（B）</td><td>{compare.total.mean_production.toFixed(3)}（n={compare.total.n_production}）</td></tr>
                <tr><td>候选 95% CI</td><td>[{compare.total.ci_95_low.toFixed(3)}, {compare.total.ci_95_high.toFixed(3)}]</td></tr>
                <tr><td>均值差（A−B）</td><td>{compare.total.delta_mean.toFixed(3)}</td></tr>
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
            <p className="bench-compare-note">
              tie = 测不出差异（≠ 没有差异）；维度差值只报告不判胜负。
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function batchStatusLabel(s: string): string {
  const map: Record<string, string> = {
    running: "运行中", done: "完成", partial: "部分完成", failed: "失败",
  };
  return map[s] ?? s;
}

function batchStatusClass(s: string): string {
  // 复用 session-status 的语义色（done/failed 现成；running/partial 就近映射）
  if (s === "done") return "done";
  if (s === "failed") return "failed";
  if (s === "running") return "running";
  return "pending";
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
