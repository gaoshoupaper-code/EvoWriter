import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  createGoldenCase,
  getCaseContent,
  getDatasetCases,
  getGoldenRevision,
  rerunGolden,
  type DatasetCase,
  type GoldenRevision,
} from "@/lib/api";

/**
 * 评测数据集页（REQ-20260921-135543 FR-008，DEC-019 基线适配修订）。
 *
 * golden 标准集单主体：revision 状态条 + case 列表 + 正文 Markdown 查看 +
 * 受控新增 + rerun-golden 入口（DEC-016：改数据后原地重跑验证）。
 * Growing/Review/采集线不实现（promote 已于 82e257b 裁撤）。
 */
export default function BenchDataset() {
  const [cases, setCases] = useState<DatasetCase[]>([]);
  const [revision, setRevision] = useState<GoldenRevision | null>(null);
  const [revError, setRevError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [viewingCase, setViewingCase] = useState<string>("");
  const [caseContent, setCaseContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);

  const [demandInput, setDemandInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [addedResult, setAddedResult] = useState<{
    case_id: string;
    golden_revision: string;
    push_warning: string | null;
  } | null>(null);
  const [rerunning, setRerunning] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRevError(false);
    const [csResult, revResult] = await Promise.allSettled([
      getDatasetCases("golden"),
      getGoldenRevision(),
    ]);
    if (csResult.status === "fulfilled") {
      setCases(csResult.value.cases);
    } else {
      setError(csResult.reason instanceof Error ? csResult.reason.message : "读取 golden 列表失败");
    }
    if (revResult.status === "fulfilled") {
      setRevision(revResult.value);
    } else {
      setRevError(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleView(caseId: string) {
    if (viewingCase === caseId) {
      setViewingCase("");
      setCaseContent("");
      return;
    }
    setViewingCase(caseId);
    setCaseContent("");
    setContentLoading(true);
    try {
      const detail = await getCaseContent(caseId, "golden");
      setCaseContent(detail.demand_md);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "读取 case 内容失败");
    } finally {
      setContentLoading(false);
    }
  }

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

  return (
    <div className="bench-page bench-dataset-page">
      <header className="page-header">
        <div className="page-header-row">
          <div>
            <h1>评测数据集</h1>
            <p className="page-desc">
              golden 标准集（冻结基准，评测用）· 共 {cases.length} case
            </p>
          </div>
          <button
            className="config-button"
            onClick={handleRerun}
            disabled={rerunning}
            title="golden 变更后重跑最近 3 个版本建立新基线"
          >
            {rerunning ? "重跑触发中…" : "重跑最近 3 个版本"}
          </button>
        </div>
      </header>

      {loading ? (
        <div className="page-loading">加载 golden 集…</div>
      ) : error ? (
        <div className="error-card">
          <span className="error-icon">⚠</span>
          <div className="error-body">
            <div className="error-title">加载失败</div>
            <div className="error-desc">{error}</div>
          </div>
          <button className="action-link" onClick={refresh}>重试</button>
        </div>
      ) : (
        <>
          {/* revision 状态条 */}
          {(revision || revError) && (
            <section className="golden-revision-bar">
              {revError ? (
                <div className="rev-item">
                  <span className="rev-label">Revision</span>
                  <span className="rev-badge danger">⚠ 加载失败</span>
                </div>
              ) : revision && (
                <>
                  <div className="rev-item">
                    <span className="rev-label">Revision</span>
                    <span className="rev-value mono">{revision.revision?.slice(0, 12) || "—"}</span>
                  </div>
                  <div className="rev-item">
                    <span className="rev-label">锁定</span>
                    <span className={`rev-badge ${revision.locked ? "ok" : "warn"}`}>
                      {revision.locked ? "已锁定" : "未锁定"}
                    </span>
                  </div>
                  <div className="rev-item">
                    <span className="rev-label">完整性</span>
                    <span className={`rev-badge ${revision.intact ? "ok" : "danger"}`}>
                      {revision.intact ? "完好" : "已篡改"}
                    </span>
                  </div>
                  <div className="rev-item">
                    <span className="rev-label">Case 数</span>
                    <span className="rev-value">{revision.case_count}</span>
                  </div>
                </>
              )}
            </section>
          )}

          {/* case 列表 */}
          {cases.length === 0 ? (
            <div className="monitor-empty">golden 集为空——用下方受控新增添加第一批 case</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Case ID</th>
                  <th>标题</th>
                  <th>Revision</th>
                  <th>来源 Trace</th>
                  <th>参考终稿</th>
                  <th>升级时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.case_id} className={c.case_id === viewingCase ? "bench-row-selected" : ""}>
                    <td className="mono">{c.case_id}</td>
                    <td>{c.title}</td>
                    <td className="mono">{c.demand_revision?.slice(0, 12) || "—"}</td>
                    <td className="mono">{c.source_trace_id?.slice(0, 16) || "—"}</td>
                    <td>{c.has_reference ? "✓" : "—"}</td>
                    <td>{c.promoted_at?.slice(0, 19).replace("T", " ") || "—"}</td>
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

          {/* 正文 Markdown 查看（FR-008：替换旧 <pre> 裸文本） */}
          {viewingCase && (
            <div className="bench-demand-view prose-doc">
              {contentLoading ? (
                <div className="page-loading">加载正文…</div>
              ) : (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{caseContent}</ReactMarkdown>
              )}
            </div>
          )}

          {/* 受控新增 */}
          <section className="bench-golden-add">
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
          </section>
        </>
      )}
    </div>
  );
}
