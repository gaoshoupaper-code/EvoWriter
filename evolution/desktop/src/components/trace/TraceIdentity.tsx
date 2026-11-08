import { useState } from "react";
import { getTraceDisplay, type TraceDisplayContext, type TraceIdentityRun } from "@/lib/trace-display";
import "./trace-identity.css";

export function TraceIdentity({ run, context, compact = false }: {
  run: TraceIdentityRun; context?: TraceDisplayContext; compact?: boolean;
}) {
  const display = getTraceDisplay(run, context);
  return <span className={"trace-identity" + (compact ? " trace-identity-compact" : "")}>
    <span className="trace-identity-title" title={display.title}>{display.title}</span>
    <span className="trace-identity-subtitle" title={display.subtitle}>{display.subtitle}</span>
  </span>;
}

export function TraceIdentifier({ traceId }: { traceId: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(traceId);
      setCopied(true);
      setError("");
    } catch {
      setError("复制失败，请选中编号手动复制。");
    }
  }
  return <details className="trace-identifier">
    <summary>运行标识</summary>
    <div><code>{traceId}</code>
      <button type="button" className="action-link" aria-label="复制运行编号" onClick={() => void copy()}>{copied ? "已复制" : "复制编号"}</button>
    </div>
    {error && <span role="status">{error}</span>}
  </details>;
}
