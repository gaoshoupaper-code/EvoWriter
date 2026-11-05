import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Upload } from "lucide-react";
import {
  getExperimentPackage, getExperimentPackageTemplates, importExperimentPackage, listExperimentPackages,
  type ExperimentInput, type ExperimentPackage, type ExperimentPackageInput,
} from "@/lib/api";
import { PURPOSE_LABELS, TASK_LABELS, TASK_TYPES } from "@/lib/experiment-types";
import { downloadTextFile } from "@/lib/download";
import { ArtifactFiles, EvidenceValues } from "./ExperimentEvidence";
import "./experiments.css";

export function StagePackages() {
  const [packages, setPackages] = useState<ExperimentPackage[]>([]);
  const [canRead, setCanRead] = useState(false);
  const [filter, setFilter] = useState("");
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
      setNotice(`已导入「${result.title}」`);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "资料包导入失败"); }
    finally { setBusy(false); }
  }
  async function downloadTemplates() {
    setBusy(true); setError("");
    try {
      const { items } = await getExperimentPackageTemplates();
      for (const item of items) downloadTextFile(`${item.target.task_type}-example.json`, JSON.stringify(item, null, 2), "application/json;charset=utf-8");
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
  const visible = packages.filter((item) => !filter || item.task_type === filter);
  return <section className="experiment-section" aria-labelledby="stage-packages-title">
    <div className="experiment-heading"><div><h2 id="stage-packages-title">三段任务资料包</h2><p className="experiment-note">固定需求、上游文稿、技能及确认规则。运行时使用独立副本。</p></div>
      <div className="experiment-actions"><button className="config-button" type="button" onClick={() => void downloadTemplates()} disabled={busy}><Download size={15} />下载示例</button>
        <label className={`config-button primary experiment-upload${busy ? " disabled" : ""}`}><Upload size={15} />{busy ? "处理中…" : "导入资料包"}
          <input aria-label="选择资料包" type="file" accept="application/json,.json" disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file);
          }} /></label></div>
    </div>
    {error && <p className="experiment-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <label className="experiment-filter">任务类型<select className="config-input" value={filter} onChange={(event) => setFilter(event.target.value)}>
      <option value="">全部类型</option>{TASK_TYPES.map((kind) => <option key={kind} value={kind}>{TASK_LABELS[kind]}</option>)}
    </select></label>
    {loading ? <p role="status">正在加载资料包…</p> : !visible.length ? <p className="experiment-note">尚无资料包。下载示例并填写后导入。</p> :
      <div className="experiment-table-scroll"><table className="data-table"><thead><tr><th>样本</th><th>任务</th><th>用途</th><th>目标</th><th>输入版本</th><th>操作</th></tr></thead><tbody>
        {visible.map((item) => <tr key={item.id}><td>{item.title}</td><td>{TASK_LABELS[item.task_type]}</td><td>{PURPOSE_LABELS[item.purpose]}</td>
          <td>{item.task_type === "chapter_text" ? `第 ${item.target.chapter} 章` : `第 ${item.target.volume_ids.join("、")} 卷`}</td>
          <td className="mono" title={item.input_revision}>{item.input_revision.slice(0, 12)}</td>
          <td><button className="action-link" type="button" disabled={!canRead} title={canRead ? "查看固定输入" : "当前账号没有正文读取权限"} onClick={() => void view(item.id)}>查看输入</button></td></tr>)}
      </tbody></table></div>}
    {selected && <section className="experiment-input-preview"><div className="experiment-heading"><h3>{selected.title} · 固定输入</h3><button type="button" className="action-link" onClick={() => { viewRequest.current += 1; setSelected(null); }}>收起</button></div>
      <p className="mono">输入版本：{selected.input_revision}</p>
      <h4>完整需求</h4><p className="experiment-prewrap">{selected.input.demand_md || "使用资料包中的上游文稿"}</p>
      <h4>预设确认规则</h4><p className="experiment-prewrap">{selected.input.confirmation_rules}</p>
      <ArtifactFiles files={selected.input.files} />
      {!!selected.input.skills.length && <details><summary>固定技能</summary><EvidenceValues value={selected.input.skills} /></details>}
    </section>}
  </section>;
}
