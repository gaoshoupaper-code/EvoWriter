import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { batchStatusLabel, batchStatusClass } from "@/components/bench/batchStatus";
import {
  runBenchmark,
  listBenchmarkBatches,
  stopBenchmark,
  getBenchmarkVersions,
  listJudgeCandidates,
  getDatasetCases,
  getLeaderboard,
  type BenchmarkBatchSummary,
  type BenchmarkVersionItem,
  type DatasetCase,
  type JudgeCandidate,
  type JudgeDefault,
  type LeaderboardResponse,
} from "@/lib/api";

/**
 * 评测工作台（REQ-20260921-135543 FR-002/FR-003）。
 *
 * 发起评测（版本/seed/并发/judge + case 子集勾选，DEC-008）+
 * 批次列表监控（5s 轮询、停止、跳批次详情）+ 版本趋势 tab（DEC-017）。
 * 弱点报告与 case 明细已迁批次详情页（DEC-006）。
 */
export default function BenchWorkbench() {
  const navigate = useNavigate();

  // ── 发起表单状态 ──
  const [versionInput, setVersionInput] = useState("");
  const [seeds, setSeeds] = useState(3);
  const [concurrency, setConcurrency] = useState<1 | 3 | 5>(3);
  const [starting, setStarting] = useState(false);

  const [versions, setVersions] = useState<BenchmarkVersionItem[]>([]);
  const [productionVersion, setProductionVersion] = useState<number | null>(null);

  const [judges, setJudges] = useState<JudgeCandidate[]>([]);
  const [judgeDefault, setJudgeDefault] = useState<JudgeDefault | null>(null);
  const [judgeConfigId, setJudgeConfigId] = useState<number | "">("");

  // case 子集勾选（DEC-008：空选 = 全量 golden）
  const [goldenCases, setGoldenCases] = useState<DatasetCase[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedCases, setSelectedCases] = useState<Set<string>>(new Set());

  // ── 批次列表 ──
  const [batches, setBatches] = useState<BenchmarkBatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [stoppingId, setStoppingId] = useState<string | null>(null);

  const selectedJudge = judges.find((j) => j.config_id === judgeConfigId) ?? null;
  const judgeSameFamily = selectedJudge?.same_family_as_executor === true;

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
        // 账本加载失败不阻塞触发：下拉退化为仅「跟随 production」
      });
    getDatasetCases("golden")
      .then((resp) => setGoldenCases(resp.cases))
      .catch(() => {
        // golden 列表加载失败：勾选器显示为空，等价全量
      });
  }, []);

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

  function toggleCase(caseId: string) {
    setSelectedCases((prev) => {
      const next = new Set(prev);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });
  }

  function selectAllCases() {
    setSelectedCases(new Set(goldenCases.map((c) => c.case_id)));
  }

  async function handleRun() {
    setStarting(true);
    try {
      const payload: {
        seeds: number;
        concurrency: 1 | 3 | 5;
        judge_config_id?: number;
        version?: number;
        case_ids?: string[];
      } = { seeds, concurrency };
      const v = versionInput.trim();
      if (v) payload.version = Number(v);
      if (judgeConfigId !== "") payload.judge_config_id = judgeConfigId;
      if (selectedCases.size > 0) payload.case_ids = [...selectedCases];
      const resp = await runBenchmark(payload);
      const scope = selectedCases.size > 0 ? `${selectedCases.size} 个 case` : "全量 golden";
      toast.success(
        `评测批次已触发：${resp.batch_id.slice(0, 8)}（${scope} × ${resp.progress.total} 行，并发 ${concurrency}）`,
      );
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

  const hasActive = batches.some((b) => b.status === "running");

  return (
    <div className="bench-page bench-workbench">
      <header className="page-header">
        <h1>评测工作台</h1>
        <p className="page-desc">
          发起评测批次（golden × harness 版本 × N seed）· 监控进度
          {hasActive && "（有批次运行中，5 秒自动刷新）"}
        </p>
      </header>

      {/* 发起评测（FR-002：case 子集勾选，DEC-008） */}
      <section className="test-start">
        <h3>发起评测</h3>
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
            {starting ? "触发中…" : selectedCases.size > 0 ? `触发评测（${selectedCases.size} case）` : "触发全量评测"}
          </button>
        </div>

        {/* case 子集勾选器 */}
        <div className="bench-case-picker">
          <button
            type="button"
            className="action-link"
            onClick={() => setPickerOpen((o) => !o)}
          >
            {pickerOpen ? "收起 case 选择" : "选择 case 子集"}
            （{selectedCases.size > 0 ? `已选 ${selectedCases.size}/${goldenCases.length}` : `不选 = 全量 ${goldenCases.length} 个` }）
          </button>
          {pickerOpen && (
            <div className="bench-case-picker-body">
              <div className="bench-case-picker-actions">
                <button type="button" className="action-link" onClick={selectAllCases}>全选</button>
                <button type="button" className="action-link" onClick={() => setSelectedCases(new Set())}>清空（全量）</button>
              </div>
              {goldenCases.length === 0 ? (
                <div className="monitor-empty">golden 列表加载失败或为空——不勾选等价全量</div>
              ) : (
                goldenCases.map((c) => (
                  <label key={c.case_id} className="bench-case-picker-item">
                    <input
                      type="checkbox"
                      checked={selectedCases.has(c.case_id)}
                      onChange={() => toggleCase(c.case_id)}
                    />
                    <span className="mono">{c.case_id}</span>
                    <span className="bench-case-picker-title">{c.title || "—"}</span>
                  </label>
                ))
              )}
            </div>
          )}
        </div>

        {judgeSameFamily && (
          <div className="bench-judge-warn">
            判评分离告警：所选 judge（{selectedJudge?.model}）与被测写作模型同家族，
            评测存在自我偏好风险（分数可能系统性偏高）。建议换不同家族的模型。
          </div>
        )}
      </section>

      {/* 批次列表 + 版本趋势（FR-003，DEC-017） */}
      <Tabs defaultValue="batches">
        <TabsList>
          <TabsTrigger value="batches">批次列表</TabsTrigger>
          <TabsTrigger value="trend">版本趋势</TabsTrigger>
        </TabsList>
        <TabsContent value="batches">
          <section className="bench-list-section">
            {loading ? (
              <div className="page-loading">加载评测批次…</div>
            ) : batches.length === 0 ? (
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
                    <tr key={b.batch_id} className="bench-row-click" onClick={() => navigate(`/bench/batches/${b.batch_id}`)}>
                      <td className="mono" title={b.batch_id}>{b.batch_id.slice(0, 8)}</td>
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
                            onClick={(e) => {
                              e.stopPropagation();
                              handleStop(b.batch_id);
                            }}
                            disabled={stoppingId === b.batch_id}
                          >
                            {stoppingId === b.batch_id ? "停止中…" : "停止"}
                          </button>
                        ) : (
                          <Link
                            className="action-link"
                            to={`/bench/batches/${b.batch_id}`}
                            onClick={(e) => e.stopPropagation()}
                          >
                            详情
                          </Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </TabsContent>
        <TabsContent value="trend">
          <LeaderboardTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** 版本趋势 tab（FR-003：各版本总分 + 五维均分，按当前锁定 golden revision）。 */
function LeaderboardTab() {
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLeaderboard()
      .then((resp) => {
        if (!cancelled) setData(resp);
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
  }, []);

  // 五维列取所有版本出现过的维度并集，保持首个出现顺序（列稳定）
  const dims = useMemo(() => {
    const seen: string[] = [];
    for (const v of data?.versions ?? []) {
      for (const dim of Object.keys(v.dimension_means ?? {})) {
        if (!seen.includes(dim)) seen.push(dim);
      }
    }
    return seen;
  }, [data]);

  if (loading) return <div className="page-loading">加载版本趋势…</div>;
  if (error) {
    return (
      <div className="error-card">
        <span className="error-icon">⚠</span>
        <div className="error-body">
          <div className="error-title">版本趋势加载失败</div>
          <div className="error-desc">{error}</div>
        </div>
      </div>
    );
  }
  if (!data || data.versions.length === 0) {
    return (
      <div className="monitor-empty">
        当前 golden revision（{data?.revision.slice(0, 12) || "—"}）下暂无完成行——先跑一个批次
      </div>
    );
  }

  return (
    <section className="bench-trend-section">
      <p className="bench-trend-note">
        golden revision <span className="mono">{data.revision.slice(0, 12)}</span> ·
        分数只用于同指纹前缀的相对对比，绝对值不作质量结论。
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th>版本</th>
            <th>总分均分</th>
            <th>case 数</th>
            {dims.map((dim) => (
              <th key={dim}>{dim}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.versions.map((v) => (
            <tr key={v.version}>
              <td>v{v.version}</td>
              <td>
                <span className="bench-trend-total">{v.avg_score?.toFixed(3) ?? "—"}</span>
              </td>
              <td>{v.case_count}</td>
              {dims.map((dim) => {
                const mean = v.dimension_means?.[dim];
                return (
                  <td key={dim}>
                    {mean == null ? (
                      "—"
                    ) : (
                      <div className="bench-trend-dim">
                        <div className="bench-score-bar">
                          <div
                            className="bench-score-fill"
                            style={{ width: `${(mean / 5) * 100}%` }}
                          />
                        </div>
                        <span className="bench-trend-dim-value">{mean.toFixed(2)}</span>
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
