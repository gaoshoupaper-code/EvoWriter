import { currentTask } from "@/lib/writing-agent";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Download, Upload } from "lucide-react";
import {
  deleteExperimentPackage, getExperimentPackage, getExperimentPackageTemplates, importExperimentPackage,
  listExperimentPackages, replaceExperimentPackage,
  type ExperimentInput, type ExperimentPackage, type ExperimentPackageInput,
} from "@/lib/api";
import { PURPOSE_LABELS, TASK_LABELS, experimentTargetLabel } from "@/lib/experiment-types";
import { downloadTextFile } from "@/lib/download";
import { ArtifactFiles, EvidenceValues } from "./ExperimentEvidence";
import "./experiments.css";
import "./evaluation-readability.css";

const DATASETS = ["optimization", "regression"] as const;
type DatasetKey = typeof DATASETS[number];

async function readSkills(files: File[]) {
  const skills = await Promise.all(files.map(async (file) => {
    const content = (await file.text()).trim();
    const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(content)?.[1];
    const name = frontmatter?.match(/^name:[ \t]*(.+)$/m)?.[1].trim().replace(/^(['"])(.*)\1$/, "$2")
      || file.name.replace(/\.md$/i, "");
    if (!name.trim() || !content) throw new Error("技能名称和内容不能为空。");
    return { stage: "text", name, content };
  }));
  if (new Set(skills.map((skill) => skill.name)).size !== skills.length) throw new Error("技能名称不能重复。");
  return skills;
}

function editedText(form: FormData, name: string, original: string) {
  const value = form.get(name);
  if (typeof value !== "string") return original;
  // Textareas normalize line endings even when the content was left alone.
  return value.replace(/\r\n?/g, "\n") === original.replace(/\r\n?/g, "\n") ? original : value;
}

export function StagePackages() {
  const task = currentTask();
  const [packages, setPackages] = useState<ExperimentPackage[]>([]);
  const [canRead, setCanRead] = useState(false);
  const [dataset, setDataset] = useState<DatasetKey>("optimization");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<(ExperimentPackage & { input: ExperimentInput }) | null>(null);
  const [editing, setEditing] = useState(false);
  const [showOutlineImport, setShowOutlineImport] = useState(false);
  const mounted = useRef(false);
  const listRequest = useRef(0);
  const viewRequest = useRef(0);
  const isCurrentAgent = useCallback(() => mounted.current && currentTask() === task, [task]);

  const refresh = useCallback(async () => {
    const request = ++listRequest.current;
    const result = await listExperimentPackages();
    if (isCurrentAgent() && request === listRequest.current) {
      setPackages(result.items); setCanRead(result.can_read_content); setLoadError("");
    }
  }, [isCurrentAgent]);
  useEffect(() => {
    mounted.current = true;
    let current = true;
    setLoading(true); setLoadError("");
    void refresh().catch((cause) => {
      if (current) setLoadError(cause instanceof Error ? cause.message : "读取资料包失败");
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; mounted.current = false; viewRequest.current += 1; };
  }, [refresh]);

  function closeDetail() {
    viewRequest.current += 1;
    setSelected(null); setEditing(false);
  }

  async function savePackage(build: () => Promise<ExperimentPackageInput>, replaceId?: string) {
    if (busy || !isCurrentAgent()) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const payload = await build();
      if (!isCurrentAgent()) return;
      const result = replaceId ? await replaceExperimentPackage(replaceId, payload) : await importExperimentPackage(payload);
      if (!isCurrentAgent()) return;
      if (replaceId) closeDetail();
      if (result.purpose !== "validation" && result.purpose !== dataset) changeDataset(result.purpose);
      setShowOutlineImport(false);
      setPackages((items) => [result, ...items.filter((item) => item.id !== replaceId && item.id !== result.id)]);
      setNotice(replaceId ? `已保存「${result.title}」的新版本。` : `已导入「${result.title}」。`);
      await refresh();
    } catch (cause) {
      if (isCurrentAgent()) setError(cause instanceof Error ? cause.message : "资料包保存失败");
    } finally { if (isCurrentAgent()) setBusy(false); }
  }
  function upload(file: File, replaceId?: string) {
    return savePackage(async () => {
      try {
        const payload = JSON.parse(await file.text()) as ExperimentPackageInput;
        if (replaceId && payload?.input && typeof payload.input === "object") delete payload.input.input_revision;
        return payload;
      }
      catch { throw new Error("资料包不是有效 JSON，请参照示例修改。"); }
    }, replaceId);
  }
  function replacementUpload(id: string) {
    return <label className="config-button readable-button experiment-upload">替换资料包
      <input aria-label="替换资料包" type="file" accept=".json,application/json" disabled={busy}
        onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file, id); }} />
    </label>;
  }
  function saveEdits(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    const files = Object.fromEntries(Object.entries(selected.input.files)
      .map(([path, content]) => [path, editedText(form, `file:${path}`, content)]));
    void savePackage(async () => ({
      title: String(form.get("title") ?? "").trim(),
      purpose: selected.purpose,
      target: selected.target,
      input: { ...selected.input, demand_md: editedText(form, "demand", selected.input.demand_md),
        confirmation_rules: editedText(form, "rules", selected.input.confirmation_rules), files, input_revision: undefined },
    }), selected.id);
  }
  async function removePackage(item: ExperimentPackage) {
    if (busy || !isCurrentAgent() || !window.confirm(`删除「${item.title}」？今后评测将跳过这道题。`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await deleteExperimentPackage(item.id);
      if (!isCurrentAgent()) return;
      if (selected?.id === item.id) closeDetail();
      else viewRequest.current += 1;
      setPackages((items) => items.filter((entry) => entry.id !== item.id));
      setNotice(`已删除「${item.title}」。`);
      await refresh();
    } catch (cause) {
      if (isCurrentAgent()) setError(cause instanceof Error ? cause.message : "删除题目失败");
    } finally { if (isCurrentAgent()) setBusy(false); }
  }
  async function downloadTemplates() {
    if (busy || !isCurrentAgent()) return;
    setBusy(true); setError("");
    try {
      const { items } = await getExperimentPackageTemplates();
      if (!isCurrentAgent()) return;
      for (const item of items) downloadTextFile(item.target.task_type + "-example.json", JSON.stringify(item, null, 2), "application/json;charset=utf-8");
    } catch (cause) {
      if (isCurrentAgent()) setError(cause instanceof Error ? cause.message : "读取示例失败");
    } finally { if (isCurrentAgent()) setBusy(false); }
  }
  async function view(id: string, edit = false) {
    const request = ++viewRequest.current;
    setError(""); setSelected(null); setEditing(false);
    try {
      const result = await getExperimentPackage(id);
      if (request === viewRequest.current && isCurrentAgent()) {
        setSelected(result); setEditing(edit);
      }
    } catch (cause) {
      if (request === viewRequest.current && isCurrentAgent()) setError(cause instanceof Error ? cause.message : "读取输入失败");
    }
  }
  function changeDataset(key: DatasetKey) {
    setDataset(key); closeDetail();
  }
  const itemsFor = (key: DatasetKey) => packages.filter((item) => item.task_type === task && item.purpose === key);
  const visible = itemsFor(dataset);
  const editedFiles = selected ? Object.entries(selected.input.files).filter(([path]) =>
    selected.task_type !== "chapter_text" || path === `章纲/第${String(selected.target.chapter).padStart(3, "0")}章.md`,
  ) : [];
  function table(items: ExperimentPackage[]) {
    return <div className="experiment-table-scroll" tabIndex={0} role="region" aria-label="题目列表"><table className="data-table experiment-dataset-table"><thead><tr>
      <th>样本</th><th>阶段</th><th>用途</th><th>目标产物</th><th>输入版本</th><th>操作</th>
    </tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.title}</strong></td>
      <td>{TASK_LABELS[item.task_type]}</td>
      <td><span className={"experiment-purpose " + item.purpose}>{PURPOSE_LABELS[item.purpose]}</span></td>
      <td>{experimentTargetLabel(item.target)}</td><td className="mono" title={item.input_revision}>{item.input_revision.slice(0, 12)}</td>
      <td><div className="experiment-actions">
        <button className="action-link readable-button" type="button" disabled={!canRead || busy} title={canRead ? "查看完整固定输入" : "当前账号没有正文读取权限"} onClick={() => void view(item.id)}>查看输入</button>
        {canRead ? <button className="action-link readable-button" type="button" disabled={busy} onClick={() => void view(item.id, true)}>编辑</button>
          : replacementUpload(item.id)}
        <button className="action-link readable-button danger" type="button" disabled={busy} onClick={() => void removePackage(item)}>删除</button>
      </div></td>
    </tr>)}</tbody></table></div>;
  }
  return <section className="experiment-section evaluation-content" aria-labelledby="stage-packages-title">
    <div className="experiment-heading"><div><h2 id="stage-packages-title">试验数据集</h2>
      <p className="experiment-note">{task === "chapter_text" ? "评测范围以工作台选题为准。"
        : "工作台默认使用本套全部在用题，也可自选。改进题和回归题各至少需要一道。"}</p></div>
    </div>
    {loading ? <p role="status">正在加载数据集…</p> : loadError ? <p role="alert" className="experiment-error">{loadError}</p> :
      <div className="evaluation-dataset-tabs" role="tablist" aria-label="试验数据集">
        {DATASETS.map((key, index) => {
          const count = itemsFor(key).length;
          return <button type="button" className="readable-button" role="tab" key={key} id={"dataset-tab-" + key} disabled={busy}
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
            <strong>{key === "regression" ? "回归题" : "改进题"}</strong>
            <span className={count ? "evaluation-dataset-ready" : "evaluation-dataset-missing"}>
              {count ? `${count} 道 · 已齐备` : "0 道 · 缺少题目"}
            </span>
          </button>;
        })}
      </div>}
    <div className="experiment-actions evaluation-dataset-tools">{task === "chapter_text" && <button className="config-button readable-button" type="button"
        aria-expanded={showOutlineImport} onClick={() => setShowOutlineImport(!showOutlineImport)} disabled={busy}>导入章纲</button>}
        <button className="config-button readable-button" type="button" onClick={() => void downloadTemplates()} disabled={busy}><Download size={15} />下载完整示例</button>
        <label className={"config-button readable-button experiment-upload" + (busy ? " disabled" : "")}><Upload size={15} />{busy ? "处理中…" : "导入资料包"}
          <input aria-label="选择资料包" type="file" accept="application/json,.json" disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file);
          }} /></label></div>
    {showOutlineImport && <form className="experiment-input-preview" onSubmit={(event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const chapter = Number(form.get("chapter"));
      const outline = form.get("outline") as File;
      const skills = (form.getAll("skills") as File[]).filter((file) => file.name);
      void savePackage(async () => ({
        title: outline.name.replace(/\.md$/i, ""), purpose: dataset,
        target: { task_type: "chapter_text", chapter, volume_ids: [] },
        input: { demand_md: "", confirmation_rules: "按本章章纲直接完成，不等待确认。",
          files: { [`章纲/第${String(chapter).padStart(3, "0")}章.md`]: await outline.text() }, skills: await readSkills(skills) },
      }));
    }}>
      <p className="experiment-note">导入到{dataset === "optimization" ? "改进题" : "回归题"}。只需本章章纲，技能可选。</p>
      <div className="experiment-actions">
        <label>章号 <input name="chapter" type="number" min="1" max={Number.MAX_SAFE_INTEGER} required defaultValue={1} /></label>
        <label>章纲文件 <input name="outline" type="file" accept=".md" required /></label>
        <label>技能文件（可多选） <input name="skills" type="file" accept=".md" multiple /></label>
        <button className="config-button readable-button" type="submit" disabled={busy}>保存题目</button>
      </div>
    </form>}
    {error && !editing && <p className="experiment-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!loading && !loadError && <div id="dataset-content" role="tabpanel" aria-labelledby={"dataset-tab-" + dataset} className="experiment-dataset-content evaluation-panel">
        <div className="experiment-heading"><div><h3>{dataset === "regression" ? "回归题" : "改进题"}</h3>
          <p className="experiment-note">{dataset === "regression" ? "比较候选版与本套基线的组内均分。" : "用改进题定位问题并检验候选版。"}</p></div></div>
        {visible.length ? table(visible) : <p className="experiment-empty">本组暂无题目。{task !== "chapter_text" && "每组至少导入一道题，才能启动评测。"}</p>}
    </div>}
    {selected && (editing ? <form key={selected.id} className="experiment-input-preview" onSubmit={saveEdits}>
      <h3>编辑题目</h3>
      <label className="experiment-field">题目名称<input className="config-input" name="title" required maxLength={200}
        defaultValue={selected.title} disabled={busy} autoFocus /></label>
      {selected.task_type !== "chapter_text" && <label className="experiment-field">完整需求
        <textarea className="config-input" name="demand" rows={6} defaultValue={selected.input.demand_md} disabled={busy} />
      </label>}
      <label className="experiment-field">预设确认规则<textarea className="config-input" name="rules" rows={3} required
        defaultValue={selected.input.confirmation_rules} disabled={busy} /></label>
      <div className="experiment-files">
        {editedFiles.map(([path, content]) => <details key={path} open={editedFiles.length === 1}>
          <summary className="readable-button">{path}</summary>
          <div className="experiment-field"><textarea className="config-input" aria-label={path}
            name={`file:${path}`} rows={8} defaultValue={content} disabled={busy} /></div>
        </details>)}
      </div>
      {error && <p className="experiment-error" role="alert">{error}</p>}
      <div className="experiment-actions">
        <button type="submit" className="config-button primary readable-button" disabled={busy}>保存为新版本</button>
        <button type="button" className="config-button readable-button" disabled={busy} onClick={() => { setEditing(false); setError(""); }}>取消编辑</button>
      </div>
    </form> : <section className="experiment-input-preview"><div className="experiment-heading"><h3>{selected.title} · 固定输入</h3>
      <div className="experiment-actions"><button type="button" className="action-link readable-button" onClick={() => {
        const payload: ExperimentPackageInput = { title: selected.title, purpose: selected.purpose, target: selected.target,
          input: { ...selected.input, input_revision: undefined } };
        downloadTextFile(selected.id + ".json", JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
      }}>下载资料包</button>
        {selected.task_type === "chapter_text" && <label className="config-button readable-button experiment-upload">导入技能并替换
          <input aria-label="导入技能并替换" type="file" accept=".md" multiple disabled={busy} onChange={(event) => {
            const files = Array.from(event.target.files ?? []); event.target.value = "";
            if (files.length) void savePackage(async () => ({ title: selected.title, purpose: selected.purpose, target: selected.target,
              input: { ...selected.input, input_revision: undefined, skills: await readSkills(files) } }), selected.id);
          }} />
        </label>}
        {replacementUpload(selected.id)}
        <button type="button" className="action-link readable-button" onClick={closeDetail}>收起</button></div></div>
      <p className="mono">输入版本：{selected.input_revision}</p>
      <h4>完整需求</h4><p className="experiment-prewrap">{selected.input.demand_md || "使用下方固定上游文稿"}</p>
      <h4>预设确认规则</h4><p className="experiment-prewrap">{selected.input.confirmation_rules}</p>
      <ArtifactFiles files={selected.input.files} />
      {!!selected.input.skills.length && <details><summary className="readable-button">固定技能</summary><EvidenceValues value={selected.input.skills} /></details>}
    </section>)}
  </section>;
}
