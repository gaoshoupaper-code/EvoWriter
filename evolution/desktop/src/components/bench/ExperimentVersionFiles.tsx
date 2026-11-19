import { useEffect, useState } from "react";
import { getExperimentVersionSource } from "@/lib/api";

export function ExperimentVersionFiles({ id, round, commit }: { id: string; round: number; commit: string }) {
  const [paths, setPaths] = useState<string[] | null>(null);
  const [path, setPath] = useState("");
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setError(""); setContent(null);
    void getExperimentVersionSource(id, round, path || undefined).then((result) => {
      if (!current) return;
      if (result.commit !== commit) throw new Error("版本标识与本轮记录不符。");
      if (!path && "paths" in result) setPaths(result.paths);
      else if ("path" in result && result.path === path) setContent(result.content);
      else throw new Error("返回的文件与当前选择不符。");
    }).catch((cause) => {
      if (current) setError(cause instanceof Error ? cause.message : "读取版本文件失败。");
    });
    return () => { current = false; };
  }, [id, round, commit, path, retry]);
  return <section aria-label="版本文件">
    {paths && (paths.length ? <label>选择版本文件 <select value={path} className="config-input"
      onChange={(event) => { setContent(null); setPath(event.target.value); }}>
      <option value="">选择文件</option>{paths.map((name) => <option key={name}>{name}</option>)}
    </select></label> : <p>当前版本没有可查看的文件。</p>)}
    {error ? <p role="alert" className="experiment-error">{error} <button type="button" className="action-link"
      onClick={() => setRetry((n) => n + 1)}>重试</button></p> : !paths || (path && content === null) ? <p role="status">正在读取…</p> : null}
    {path && content !== null && <pre className="experiment-diff">{content || "（空文件）"}</pre>}
  </section>;
}
