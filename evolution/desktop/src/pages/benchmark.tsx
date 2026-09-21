import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { CaseRunsSection } from "@/components/benchmark/CaseRunsSection";
import {
  runBenchmark,
  listBenchmarkBatches,
  getBenchmarkReport,
  compareBatches,
  getBenchmarkRubric,
  listJudgeCandidates,
  rerunGolden,
  createGoldenCase,
  getDatasetCases,
  getCaseContent,
  getGoldenRevision,
  getBenchmarkVersions,
  stopBenchmark,
  type BenchmarkBatchSummary,
  type BenchmarkReport,
  type BenchmarkCompare,
  type BenchmarkRubric,
  type JudgeCandidate,
  type JudgeDefault,
  type DatasetCase,
  type GoldenRevision,
  type BenchmarkVersionItem,
} from "@/lib/api";

/**
 * 评测页（REQ-20260919-172934 / FR-008 增量；REQ-20260920-104714 增强）。
 *
 * 工作流：
 * 1. 触发评测批次（golden 全 case × N seed × 指定 harness 版本，
 *    并发度可选 + judge 可选，FR-001/FR-003）
 * 2. 批次列表轮询进度（running 时 5s 刷新）
 * 3. 点选批次看弱点报告（维度均分 + 缺陷标签 + 低分 case）
 * 4. 选两个批次做 CI 三态对比（win / tie / lose）
 * 5. 评分标准只读展示（FR-002，锚点草稿状态明示）
 * 6. golden 集查看 + 受控新增 + 一键重跑（FR-004）
 */
export default function BenchmarkPage() {
  const [batches, setBatches] = useState<BenchmarkBatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [stoppingId, setStoppingId] = useState<string | null>(null);

  // 触发表单
  const [versionInput, setVersionInput] = useState("");
  const [seeds, setSeeds] = useState(3);
  const [concurrency, setConcurrency] = useState<1 | 3 | 5>(3);

  // harness 版本下拉（Platform 账本流水，registry.json 已冻结退役；
  // 加载失败退化为仅「跟随」）
  const [versions, setVersions] = useState<BenchmarkVersionItem[]>([]);
  const [productionVersion, setProductionVersion] = useState<number | null>(null);

  // judge 候选（FR-003）
  const [judges, setJudges] = useState<JudgeCandidate[]>([]);
  const [judgeDefault, setJudgeDefault] = useState<JudgeDefault | null>(null);
  const [judgeConfigId, setJudgeConfigId] = useState<number | "">("");

  // 选中批次的报告
  const [selectedBatch, setSelectedBatch] = useState("");
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  // case 明细区随报告刷新同步（FR-001：loadReport 时递增触发重拉）
  const [reportRefreshKey, setReportRefreshKey] = useState(0);

  // 对比
  const [cmpA, setCmpA] = useState("");
  const [cmpB, setCmpB] = useState("");
  const [comparing, setComparing] = useState(false);
  const [compare, setCompare] = useState<BenchmarkCompare | null>(null);

  useEffect(() => {
    listJudgeCandidates()
      .then((resp) => {
        setJudges(resp.judges);
        setJudgeDefault(resp.default);
      })
      .catch(() => {
        // 候选加载失败不阻塞页面：judge 下拉退化为「默认」一项
      });
    getBenchmarkVersions()
      .then((resp) => {
        setVersions(resp.items);
        setProductionVersion(resp.production_version);
      })
      .catch(() => {
        // 账本加载失败不阻塞触发：下拉退化为仅「跟随 production」（不带版本号）
      });
  }, []);

  const selectedJudge = judges.find((j) => j.config_id === judgeConfigId) ?? null;
  const judgeSameFamily = selectedJudge?.same_family_as_executor === true;

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
      const payload: {
        seeds: number;
        concurrency: 1 | 3 | 5;
        judge_config_id?: number;
        version?: number;
      } = { seeds, concurrency };
      const v = versionInput.trim();
      if (v) payload.version = Number(v);
      if (judgeConfigId !== "") payload.judge_config_id = judgeConfigId;
      const resp = await runBenchmark(payload);
      toast.success(`评测批次已触发：${resp.batch_id.slice(0, 8)}（${resp.progress.total} 次生成+评分，并发 ${concurrency}）`);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "触发评测失败");
    } finally {
      setStarting(false);
    }
  }

  async function handleStop(batchId: string) {
    if (!window.confirm("确定停止该评测批次？生成中的任务将被叫停，未开始的行转已取消。")) return;
    setStoppingId(batchId);
    try {
      const resp = await stopBenchmark(batchId);
      toast.success(`批次 ${batchId.slice(0, 8)} 已停止（取消 ${resp.progress.cancelled ?? 0} 行）`);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "停止失败");
    } finally {
      setStoppingId(null);
    }
  }

  async function loadReport(batchId: string) {
    setSelectedBatch(batchId);
    setReport(null);
    setReportLoading(true);
    setReportRefreshKey((k) => k + 1);
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
            <span>Harness 版本</span>
            <select
              className="evolve-select"
              value={versionInput}
              onChange={(e) => setVersionInput(e.target.value)}
              disabled={starting}
            >
              <option value="">
                跟随当前 production{productionVersion != null ? `（v${productionVersion}）` : ""}
              </option>
              {versions.map((v) => (
                <option key={v.version} value={String(v.version)}>
                  v{v.version} · {v.status === "production" ? "★ production" : v.status}
                  {v.change_summary ? ` · ${v.change_summary.slice(0, 30)}` : ""}
                </option>
              ))}
            </select>
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
          <label className="test-field">
            <span>并发度（同时跑几行）</span>
            <select
              className="evolve-select"
              value={concurrency}
              onChange={(e) => setConcurrency(Number(e.target.value) as 1 | 3 | 5)}
              disabled={starting}
            >
              <option value={1}>1（串行，限流时降级用）</option>
              <option value={3}>3（推荐）</option>
              <option value={5}>5（激进）</option>
            </select>
          </label>
          <label className="test-field">
            <span>评测器 judge（空 = 默认解析）</span>
            <select
              className="evolve-select"
              value={judgeConfigId}
              onChange={(e) => setJudgeConfigId(e.target.value === "" ? "" : Number(e.target.value))}
              disabled={starting}
            >
              <option value="">
                {judgeDefault
                  ? `默认（${judgeDefault.model || "未配置"} · ${judgeDefault.degraded ? "降级 evolution" : judgeDefault.scope}）`
                  : "默认"}
              </option>
              {judges.map((j) => (
                <option key={j.config_id} value={j.config_id} disabled={!j.has_key}>
                  {j.name}（{j.model} · {j.scope}）{!j.has_key ? " — 缺 key 不可用" : ""}
                </option>
              ))}
            </select>
          </label>
          <button className="config-button primary" onClick={handleRun} disabled={starting}>
            {starting ? "触发中…" : "触发全量评测"}
          </button>
        </div>
        {judgeSameFamily && (
          <div className="bench-judge-warn">
            判评分离告警：所选 judge（{selectedJudge?.model}）与被测写作模型同家族，
            评测存在自我偏好风险（分数可能系统性偏高）。建议换不同家族的模型。
          </div>
        )}
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
                <th>并发</th>
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
                      <span className="bench-fail-count">
                        （失败 {b.progress.failed}
                        {b.stop_reason === "auto_fail" ? " · 连续 3 次失败自动止损" : ""}）
                      </span>
                    )}
                    {(b.progress.cancelled ?? 0) > 0 && (
                      <span className="bench-fail-count">（取消 {b.progress.cancelled}）</span>
                    )}
                  </td>
                  <td>{b.harness_version != null ? `v${b.harness_version}` : "—"}</td>
                  <td title={b.golden_revision ?? ""}>{b.rubric_version ?? "—"}</td>
                  <td>{b.concurrency != null ? `×${b.concurrency}` : "—"}</td>
                  <td>{b.triggered_at?.slice(0, 19).replace("T", " ") ?? "—"}</td>
                  <td>
                    {b.status === "running" ? (
                      <button
                        className="action-link bench-stop-link"
                        onClick={() => handleStop(b.batch_id)}
                        disabled={stoppingId === b.batch_id}
                      >
                        {stoppingId === b.batch_id ? "停止中…" : "停止"}
                      </button>
                    ) : (
                      <button className="action-link" onClick={() => loadReport(b.batch_id)}>
                        报告
                      </button>
                    )}
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

          {/* case 明细区（REQ-20260921-114943 FR-001/FR-004：聚合报告下方，
              全行状态可见；无聚合数据的批次也可下钻失败行） */}
          <CaseRunsSection batchId={selectedBatch} refreshKey={reportRefreshKey} />
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
      {/* 评分标准（只读，FR-002） */}
      <RubricSection />

      {/* golden 集管理（只读视图 + 受控新增 + 一键重跑，FR-004） */}
      <GoldenSection />
    </div>
  );
}

/** 评分标准只读展示（FR-002/DEC-011：评测页内入口；草稿状态明示，DEC-006）。 */
function RubricSection() {
  const [expanded, setExpanded] = useState(false);
  const [rubric, setRubric] = useState<BenchmarkRubric | null>(null);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && !rubric) {
      setLoading(true);
      try {
        setRubric(await getBenchmarkRubric());
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "读取评分标准失败");
        setExpanded(false);
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <section className="bench-rubric-section">
      <h3>
        评分标准（只读）
        <button className="action-link" onClick={toggle}>
          {expanded ? "收起" : "展开"}
        </button>
      </h3>
      {expanded && (
        <>
          {loading ? (
            <div className="page-loading">加载评分标准…</div>
          ) : rubric ? (
            <>
              <div className="bench-calibration-note">
                版本 <strong>{rubric.rubric_version}</strong> · 校准状态{" "}
                <strong>{rubric.calibration_status}</strong> · 锚点 {rubric.anchor_status}
                <span className="bench-draft-badge">草稿 · 未校准（分数只用于相对对比，不作绝对质量结论）</span>
              </div>
              <div className="bench-rubric-grid">
                {rubric.dimensions.map((dim) => (
                  <div key={dim.key} className="bench-rubric-dim">
                    <h4>{dim.key}</h4>
                    <p className="bench-rubric-question">{dim.question}</p>
                    <dl className="bench-rubric-anchors">
                      <dt>5 分</dt><dd>{dim.anchors["5"]}</dd>
                      <dt>3 分</dt><dd>{dim.anchors["3"]}</dd>
                      <dt>1 分</dt><dd>{dim.anchors["1"]}</dd>
                    </dl>
                    <p className="bench-rubric-tags">
                      缺陷标签词表：{dim.defect_tags.join("、")}
                    </p>
                  </div>
                ))}
              </div>
              <div className="bench-rubric-rule">
                规则项「{rubric.rule_delivery.key}」（代码判定，不走 LLM）：
                {rubric.rule_delivery.description}。分数 ≤{rubric.low_score_threshold} 的维度必须挂词表内缺陷标签。
              </div>
            </>
          ) : (
            <div className="monitor-empty">暂无评分标准数据</div>
          )}
        </>
      )}
    </section>
  );
}

/** golden 集管理：只读视图 + 受控新增 + 一键重跑（FR-004/DEC-008/009）。 */
function GoldenSection() {
  const [cases, setCases] = useState<DatasetCase[]>([]);
  const [rev, setRev] = useState<GoldenRevision | null>(null);
  const [loading, setLoading] = useState(true);
  const [demandInput, setDemandInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [addedResult, setAddedResult] = useState<{ case_id: string; golden_revision: string; push_warning: string | null } | null>(null);
  const [viewingCase, setViewingCase] = useState<string>("");
  const [caseContent, setCaseContent] = useState<string>("");
  const [rerunning, setRerunning] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [casesResp, revResp] = await Promise.all([
        getDatasetCases("golden"),
        getGoldenRevision(),
      ]);
      setCases(casesResp.cases);
      setRev(revResp);
    } catch {
      if (loading) toast.error("读取 golden 集失败");
    } finally {
      setLoading(false);
    }
  }, [loading]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleAdd() {
    setAdding(true);
    try {
      const result = await createGoldenCase(demandInput);
      setDemandInput("");
      setAddedResult({
        case_id: result.case_id,
        golden_revision: result.golden_revision,
        push_warning: result.git_push_warning,
      });
      toast.success(`golden case ${result.case_id} 已新增（revision → ${result.golden_revision}）`);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "新增失败");
    } finally {
      setAdding(false);
    }
  }

  async function handleView(caseId: string) {
    if (viewingCase === caseId) {
      setViewingCase("");
      setCaseContent("");
      return;
    }
    setViewingCase(caseId);
    setCaseContent("");
    try {
      const detail = await getCaseContent(caseId, "golden");
      setCaseContent(detail.demand_md);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "读取 case 内容失败");
    }
  }

  async function handleRerun() {
    setRerunning(true);
    try {
      const resp = await rerunGolden(3);
      toast.success(`重跑已触发：${resp.batch_id.slice(0, 8)}（最近 3 版本 × 新 golden 全 case）`);
      setAddedResult(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "触发重跑失败");
    } finally {
      setRerunning(false);
    }
  }

  if (loading) return null;

  return (
    <section className="bench-golden-section">
      <h3>golden 集（{cases.length} case · revision {rev?.revision ?? "—"}）</h3>
      <div className="bench-golden-grid">
        <div className="bench-golden-list">
          {cases.length === 0 ? (
            <div className="monitor-empty">golden 集为空</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr><th>Case</th><th>标题</th><th>来源</th><th>操作</th></tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.case_id} className={c.case_id === viewingCase ? "bench-row-selected" : ""}>
                    <td>{c.case_id}</td>
                    <td>{c.title}</td>
                    <td>{c.created_by}</td>
                    <td>
                      <button className="action-link" onClick={() => handleView(c.case_id)}>
                        {viewingCase === c.case_id ? "收起" : "查看"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {viewingCase && (
            <pre className="bench-demand-view">{caseContent || "加载中…"}</pre>
          )}
        </div>
        <div className="bench-golden-add">
          <h4>受控新增（写入经 git 提交 + 重锁 revision）</h4>
          <textarea
            className="config-input bench-demand-input"
            value={demandInput}
            onChange={(e) => setDemandInput(e.target.value)}
            placeholder="粘贴新 case 的 demand.md 全文（创作需求）…"
            disabled={adding}
          />
          <button
            className="config-button primary"
            onClick={handleAdd}
            disabled={adding || !demandInput.trim()}
          >
            {adding ? "提交中…" : "新增 golden case"}
          </button>
          {addedResult && (
            <div className="bench-golden-added">
              <p>
                <strong>{addedResult.case_id}</strong> 已入库，revision → {addedResult.golden_revision}。
                golden 已变，历史批次对比将不可比——重跑最近 3 个版本建立新基线？
              </p>
              {addedResult.push_warning && (
                <div className="bench-rule-warn">{addedResult.push_warning}</div>
              )}
              <button className="config-button" onClick={handleRerun} disabled={rerunning}>
                {rerunning ? "触发中…" : "重跑最近 3 个版本"}
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function batchStatusLabel(s: string): string {
  const map: Record<string, string> = {
    running: "运行中", done: "完成", partial: "部分完成", failed: "失败",
    cancelled: "已停止",
  };
  return map[s] ?? s;
}

function batchStatusClass(s: string): string {
  // 复用 session-status 的语义色（done/failed 现成；running/partial/cancelled 就近映射）
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
