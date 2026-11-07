import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Upload } from "lucide-react";
import {
  getExperimentPackage, getExperimentPackageTemplates, importExperimentPackage, listExperimentPackages,
  type ExperimentInput, type ExperimentPackage, type ExperimentPackageInput,
} from "@/lib/api";
import { BUILTIN_SUITE, PURPOSE_LABELS, TASK_LABELS, TASK_TYPES, experimentTargetLabel } from "@/lib/experiment-types";
import { downloadTextFile } from "@/lib/download";
import { ArtifactFiles, EvidenceValues } from "./ExperimentEvidence";
import "./experiments.css";

const DATASETS = [...TASK_TYPES, "regression"] as const;
type DatasetKey = typeof DATASETS[number];

export function StagePackages() {
  const [packages, setPackages] = useState<ExperimentPackage[]>([]);
  const [canRead, setCanRead] = useState(false);
  const [dataset, setDataset] = useState<DatasetKey>("volume_outlines");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<(ExperimentPackage & { input: ExperimentInput }) | null>(null);
  const viewRequest = useRef(0);

  const refresh = useCallback(async () => {
    const result = await listExperimentPackages();
    setPackages(result.items); setCanRead(result.can_read_content);
  }, []);
  useEffect(() => {
    void refresh().catch((cause) => setError(cause instanceof Error ? cause.message : "读取资料包失败")).finally(() => setLoading(false));
    return () => { viewRequest.current += 1; };
  }, [refresh]);

  async function upload(file: File) {
    setBusy(true); setError(""); setNotice("");
    try {
      let payload: ExperimentPackageInput;
      try { payload = JSON.parse(await file.text()) as ExperimentPackageInput; }
      catch { throw new Error("资料包不是有效 JSON，请参照示例修改。"); }
      const result = await importExperimentPackage(payload);
      setNotice("已导入「" + result.title + "」，可在自备资料包中查看。");
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "资料包导入失败"); }
    finally { setBusy(false); }
  }
  async function downloadTemplates() {
    setBusy(true); setError("");
    try {
      const { items } = await getExperimentPackageTemplates();
      for (const item of items) downloadTextFile(item.target.task_type + "-example.json", JSON.stringify(item, null, 2), "application/json;charset=utf-8");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "读取示例失败"); }
    finally { setBusy(false); }
  }
  async function view(id: string) {
    const request = ++viewRequest.current;
    setError(""); setSelected(null);
    try {
      const result = await getExperimentPackage(id);
      if (request === viewRequest.current) setSelected(result);
    } catch (cause) { if (request === viewRequest.current) setError(cause instanceof Error ? cause.message : "读取输入失败"); }
  }
  function changeDataset(key: DatasetKey) {
    setDataset(key); viewRequest.current += 1; setSelected(null);
  }
  const builtins = packages.filter((item) => item.suite === BUILTIN_SUITE);
  const custom = packages.filter((item) => item.suite !== BUILTIN_SUITE);
  const itemsFor = (key: DatasetKey) => builtins.filter((item) => key === "regression"
    ? item.purpose === "regression" : item.task_type === key && item.purpose !== "regression");
  const visible = itemsFor(dataset);
  function table(items: ExperimentPackage[]) {
    return <div className="experiment-table-scroll"><table className="data-table experiment-dataset-table"><thead><tr>
      <th>样本</th><th>阶段</th><th>用途</th><th>目标产物</th><th>输入版本</th><th></th>
    </tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.title}</strong></td>
      <td>{item.task_type === "chapter_outlines" && item.target.chapter == null ? "4 卷章纲" : TASK_LABELS[item.task_type]}</td>
      <td><span className={"experiment-purpose " + item.purpose}>{PURPOSE_LABELS[item.purpose]}</span></td>
      <td>{experimentTargetLabel(item.target)}</td><td className="mono" title={item.input_revision}>{item.input_revision.slice(0, 12)}</td>
      <td><button className="action-link" type="button" disabled={!canRead} title={canRead ? "查看完整固定输入" : "当前账号没有正文读取权限"} onClick={() => void view(item.id)}>查看输入</button></td>
    </tr>)}</tbody></table></div>;
  }
  return <section className="experiment-section" aria-labelledby="stage-packages-title">
    <div className="experiment-heading"><div><h2 id="stage-packages-title">试验数据集 <span className="experiment-count">{builtins.length} 条</span></h2>
      <p className="experiment-note">三阶段各 2 条优化、1 条独立终验。回归集另含每阶段 1 条，运行时使用独立副本。</p></div>
      <div className="experiment-actions"><button className="config-button" type="button" onClick={() => void downloadTemplates()} disabled={busy}><Download size={15} />下载完整示例</button>
        <label className={"config-button experiment-upload" + (busy ? " disabled" : "")}><Upload size={15} />{busy ? "处理中…" : "导入资料包"}
          <input aria-label="选择资料包" type="file" accept="application/json,.json" disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file);
          }} /></label></div>
    </div>
    {error && <p className="experiment-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {loading ? <p role="status">正在加载数据集…</p> : <div className="experiment-dataset-layout">
      <div className="experiment-dataset-nav" role="tablist" aria-label="试验数据集" aria-orientation="vertical">
        {DATASETS.map((key, index) => <button type="button" role="tab" key={key} id={"dataset-tab-" + key}
          aria-selected={dataset === key} aria-controls="dataset-content" tabIndex={dataset === key ? 0 : -1}
          onClick={() => changeDataset(key)} onKeyDown={(event) => {
            let next = index;
            if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % DATASETS.length;
            else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + DATASETS.length - 1) % DATASETS.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = DATASETS.length - 1;
            else return;
            event.preventDefault(); changeDataset(DATASETS[next]);
            document.getElementById("dataset-tab-" + DATASETS[next])?.focus();
          }}>
          <span><strong>{key === "regression" ? "回归集" : TASK_LABELS[key]}</strong>
            <small>{key === "regression" ? "检查另外两类能力" : key === "volume_outlines" ? "3 卷 · 每卷规划 20 章" : "每条只生成指定一章"}</small></span>
          <b>{itemsFor(key).length}</b>
        </button>)}
      </div>
      <div id="dataset-content" role="tabpanel" aria-labelledby={"dataset-tab-" + dataset} className="experiment-dataset-content">
        <div className="experiment-heading"><div><h3>{dataset === "regression" ? "独立回归集" : TASK_LABELS[dataset] + "试验集"}</h3>
          <p className="experiment-note">{dataset === "regression" ? "每次主攻一类时，自动运行另外两类回归题。" : "优化题用于找问题和改版，独立终验题只在最后把关。"}</p></div></div>
        {visible.length ? table(visible) : <p className="experiment-empty">本组数据尚未就绪，请刷新配置后核对初始化状态。</p>}
      </div>
    </div>}
    {!!custom.length && <details className="experiment-history"><summary>自备与历史资料包 · {custom.length} 条</summary>
      <p className="experiment-note">保留原始目标与输入版本，旧四卷章纲资料不会改成单章含义。</p>{table(custom)}
    </details>}
    {selected && <section className="experiment-input-preview"><div className="experiment-heading"><h3>{selected.title} · 固定输入</h3>
      <div className="experiment-actions"><button type="button" className="action-link" onClick={() => {
        const payload: ExperimentPackageInput = { title: selected.title, purpose: selected.purpose, target: selected.target, input: selected.input };
        downloadTextFile(selected.id + ".json", JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
      }}>下载资料包</button><button type="button" className="action-link" onClick={() => { viewRequest.current += 1; setSelected(null); }}>收起</button></div></div>
      <p className="mono">输入版本：{selected.input_revision}</p>
      <h4>完整需求</h4><p className="experiment-prewrap">{selected.input.demand_md || "使用下方固定上游文稿"}</p>
      <h4>预设确认规则</h4><p className="experiment-prewrap">{selected.input.confirmation_rules}</p>
      <ArtifactFiles files={selected.input.files} />
      {!!selected.input.skills.length && <details><summary>固定技能</summary><EvidenceValues value={selected.input.skills} /></details>}
    </section>}
  </section>;
}
