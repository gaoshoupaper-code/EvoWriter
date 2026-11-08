import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { PHASE_LABELS, type ExperimentSample } from "@/lib/experiment-types";
import { downloadTextFile } from "@/lib/download";

export function ExperimentArtifacts({ samples, canReadContent }: { samples: ExperimentSample[]; canReadContent: boolean }) {
  const [packageId, setPackageId] = useState("");
  const [sampleId, setSampleId] = useState("");
  const packages = [...new Map(samples.map((sample) => [sample.package_id, sample.package_title])).entries()];
  const selectedPackage = packages.some(([id]) => id === packageId) ? packageId : packages[0]?.[0];
  const versions = samples.filter((sample) => sample.package_id === selectedPackage).sort((a, b) =>
    a.round - b.round || a.phase.localeCompare(b.phase) || a.repeat - b.repeat || a.attempt - b.attempt || a.id.localeCompare(b.id));
  const sample = versions.find((item) => item.id === sampleId) ?? versions[0];
  return <section className="experiment-artifacts" aria-label="按样本阅读产物">
    <div className="experiment-artifact-selectors"><label>故事样本<select className="config-input" value={selectedPackage ?? ""} onChange={(event) => { setPackageId(event.target.value); setSampleId(""); }}>
      {packages.map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select></label>
      <label>运行版本<select className="config-input" value={sample?.id ?? ""} onChange={(event) => setSampleId(event.target.value)}>
        {versions.map((item) => <option key={item.id} value={item.id}>{item.arm === "baseline" ? "原版" : "候选第 " + item.round + " 轮"}
          {" · " + (PHASE_LABELS[item.phase] ?? item.phase) + " · 重复 " + item.repeat + " / 尝试 " + item.attempt}</option>)}</select></label></div>
    {!canReadContent ? <p className="experiment-note">当前账号没有正文读取权限。</p> : !sample ? <p className="experiment-empty">尚未生成样本产物。</p> : <>
      <p className="experiment-artifact-source">{sample.package_title} · {PHASE_LABELS[sample.phase] ?? sample.phase} ·
        {sample.arm === "baseline" ? " 原版" : " 候选第 " + sample.round + " 轮"} · 重复 {sample.repeat} / 尝试 {sample.attempt}
        <span className="experiment-cell-note">运行版本 {sample.source_commit} · 输入版本 {sample.input_revision}</span></p>
      {!sample.completed && <p className="experiment-end-reason">本次运行未完成，以下保留实际已生成的文件。{sample.error}</p>}
      <ArtifactReader key={sample.id} files={sample.artifacts} />
    </>}
  </section>;
}

function ArtifactReader({ files }: { files: Record<string, string> }) {
  const paths = Object.keys(files).sort((a, b) => a.localeCompare(b, "zh-CN", { numeric: true }));
  const [selectedPath, setSelectedPath] = useState("");
  const path = paths.includes(selectedPath) ? selectedPath : paths[0];
  if (!path) return <p className="experiment-empty">本次运行尚未生成文件。</p>;
  return <div className="experiment-file-reader"><nav aria-label="产物文件列表" className="experiment-file-nav">
    {paths.map((name) => <button key={name} type="button" aria-current={name === path ? "true" : undefined} onClick={() => setSelectedPath(name)}>{name}</button>)}
  </nav><article className="experiment-file-document"><header><h3>{path}</h3>
    <button className="action-link" type="button" onClick={() => downloadTextFile(path.replace(/[\\/]/g, "_"), files[path])}>下载文稿</button></header>
    <div className="prose-doc"><ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{files[path]}</ReactMarkdown></div>
  </article></div>;
}
