import { useEffect, useState } from "react";
import { getMiddlewareActions, type MiddlewareAction } from "@/lib/api";

export function RunMiddleware({ traceId, eventCount }: {traceId: string; eventCount?: number}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<MiddlewareAction[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let current = true;
    setItems(null); setError("");
    getMiddlewareActions(traceId).then((response) => {
      if (current) { setItems(response.items); setError(""); }
    }).catch((cause) => { if (current) setError(String(cause)); });
    return () => { current = false; };
  }, [traceId, open, eventCount]);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>本次实际触发的中间件</summary>
    {error ? <p role="alert">{error}</p> : items === null ? <p role="status">读取触发记录…</p> : items.length ?
      <ol>{items.map((item, index) => <li key={index}>{item.rule || "未记录规则名"} · {item.hook} · {item.action}</li>)}</ol> :
      <p>尚无实际干预记录。</p>}
  </details>;
}
