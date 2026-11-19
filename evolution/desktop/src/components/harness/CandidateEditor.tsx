import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  getSharedVersion, getSharedVersions, listCandidateAssets, readCandidateAsset,
  saveAssetCandidate, shareMiddleware,
} from "@/lib/api";

export function CandidateEditor() {
  const [base, setBase] = useState("");
  const [paths, setPaths] = useState<string[]>([]);
  const [newPaths, setNewPaths] = useState<string[]>([]);
  const [newName, setNewName] = useState("");
  const [path, setPath] = useState("");
  const [original, setOriginal] = useState<string | null>(null);
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [shared, setShared] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<{commit: string; diff: string} | null>(null);
  const module = path.match(/^middleware\/(\w+)\.py$/)?.[1];

  async function open() {
    setError(""); setBusy(true);
    try {
      const files = await listCandidateAssets();
      setBase(files.base_commit); setPaths(files.paths);
      setPath(files.paths.find((name) => name.startsWith("prompts/")) ?? files.paths[0] ?? "");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    if (!path) return;
    let active = true;
    setOriginal(null); setShared(null); setError("");
    if (newPaths.includes(path)) {
      setOriginal("");
      return;
    }
    readCandidateAsset(path).then((asset) => {
      if (!active) return;
      if (asset.base_commit !== base) setError("线上版本已变化，请重新创建候选版。");
      else setOriginal(asset.content);
    }).catch((cause) => { if (active) setError(String(cause)); });
    if (module) getSharedVersions(module).then((result) => { if (active) setShared(result.versions); })
      .catch(() => { /* 专属规则没有公共版本清单。 */ });
    return () => { active = false; };
  }, [path, base, module, newPaths]);

  function edit(value: string) {
    setSaved(null);
    setChanges((previous) => {
      const next = {...previous};
      if (value === original) delete next[path]; else next[path] = value;
      return next;
    });
  }
  async function save() {
    setBusy(true); setError("");
    try { setSaved(await saveAssetCandidate(base, changes)); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section className="experiment-card">
    {!base ? <button type="button" className="config-button" disabled={busy} onClick={open}>从当前版创建候选</button> : <>
      <h3>候选版 · 基于 {base.slice(0, 10)}</h3>
      <label>本套资产 <select value={path} disabled={busy} onChange={(event) => setPath(event.target.value)}>
        {[...paths, ...newPaths].map((name) => <option key={name}>{name}</option>)}
      </select></label>
      <label>新增资产路径 <input value={newName} disabled={busy} placeholder="skills/人物塑造/SKILL.md"
        onChange={(event) => setNewName(event.target.value)} /></label>
      <button type="button" disabled={!newName.trim() || busy} onClick={() => {
        const name = newName.trim();
        if (!paths.includes(name) && !newPaths.includes(name)) setNewPaths([...newPaths, name]);
        setPath(name); setNewName("");
      }}>打开资产</button>
      {module && shared && <div className="experiment-actions">
        <button type="button" disabled={busy} onClick={async () => {
          setBusy(true);
          try { const result = await shareMiddleware(module, base); setShared([...new Set([...shared, result.version])]); }
          catch (cause) { setError(String(cause)); } finally { setBusy(false); }
        }}>共享当前线上规则版本</button>
        <select aria-label="采用公共规则版本" value="" disabled={busy || original === null} onChange={async (event) => {
          setBusy(true);
          try { edit((await getSharedVersion(module, event.target.value)).content); }
          catch (cause) { setError(String(cause)); } finally { setBusy(false); }
        }}><option value="">采用公共规则版本</option>{shared.map((id) => <option key={id} value={id}>{id.slice(0, 12)}</option>)}</select>
      </div>}
      <textarea aria-label="候选资产内容" rows={13} style={{width: "100%"}} disabled={original === null || busy}
        value={changes[path] ?? original ?? ""} onChange={(event) => edit(event.target.value)} />
      <button type="button" disabled={busy || !Object.keys(changes).length} onClick={save}>保存候选并查看差异</button>
      {saved && <><pre className="experiment-prewrap">{saved.diff}</pre>
        <Link to={`/bench?candidate=${saved.commit}&base=${base}`}>评测此候选版</Link></>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
