import { useCallback, useEffect, useRef, useState } from "react";
import { getTraceModelIO, type TraceModelIOPage } from "@/lib/api";
import "./trace-model-io.css";

const ROLES = new Map(Object.entries({
  system: "系统提示词", human: "用户消息", user: "用户消息",
  ai: "助手消息", assistant: "助手消息", tool: "工具消息",
}));

export function TraceModelIO({ traceId, eventCount }: { traceId: string; eventCount: number }) {
  const [page, setPage] = useState<TraceModelIOPage | null>(null);
  const [syncedEvents, setSyncedEvents] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    setFailed(false);
    try {
      const next = await getTraceModelIO(traceId, page?.outputs.at(-1)?.sequence ?? 0, !page?.initial_input);
      setPage({ initial_input: page?.initial_input ?? next.initial_input,
        outputs: [...(page?.outputs ?? []), ...next.outputs], has_more: next.has_more });
      setSyncedEvents(eventCount);
    } catch {
      setFailed(true);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [traceId, page, eventCount]);

  useEffect(() => {
    if (!loading && !failed && !page?.has_more && syncedEvents !== eventCount) void load();
  }, [load, loading, failed, page?.has_more, syncedEvents, eventCount]);

  const initial = page?.initial_input;
  return <div className="trace-model-io">
    <section className="trace-model-panel" aria-label="初始模型输入">
      <h2>初始模型输入</h2>
      <div className="trace-model-scroll" tabIndex={0} role="group" aria-label="首轮请求内容">
        {initial ? <>
          {initial.model_name && <p className="trace-model-meta">{initial.model_name}</p>}
          <ModelPayload value={initial.input} showRoles empty="首轮调用未保存可读取的输入正文。" />
        </> : !page && !failed ? <p role="status">正在读取模型输入…</p>
          : !failed && <p>暂无模型输入。</p>}
      </div>
    </section>
    <section className="trace-model-panel" aria-label="模型输出">
      <h2>模型输出</h2>
      <div className="trace-model-scroll" tabIndex={0} role="group" aria-label="按记录顺序排列的输出">
        {page?.outputs.length ? <ol className="trace-model-responses">
          {page.outputs.map((event, index) => <li key={event.event_id}>
            <h3>{event.node_name || `第 ${index + 1} 次调用`}</h3>
            {event.model_name && <p className="trace-model-meta">{event.model_name}</p>}
            {event.type === "llm_error" && <p className="trace-model-error">{event.error || "模型调用失败。"}</p>}
            <ModelPayload value={event.output} toolCalls={event.tool_calls}
              empty={event.type === "llm_error" ? undefined : "本次调用未保存可读取的输出正文。"} />
          </li>)}
        </ol> : page && !loading && !failed && <p>暂无模型输出。</p>}
        {loading && !page?.outputs.length && <p role="status">正在读取模型输出…</p>}
      </div>
      {page?.has_more && !failed && <div className="trace-model-actions">
        <button type="button" className="action-link" disabled={loading} onClick={() => void load()}>
          {loading ? "正在加载…" : "加载更多输出"}
        </button>
      </div>}
    </section>
    {failed && <p role="alert" className="trace-model-error">模型输入和输出读取失败。{" "}
      <button type="button" className="action-link" onClick={() => void load()}>重试</button>
    </p>}
  </div>;
}

function ModelPayload({ value, toolCalls, showRoles, empty }: { value: unknown; toolCalls?: unknown; showRoles?: boolean; empty?: string }) {
  const payload = record(value);
  const structured = textOf(payload?.structured_response);
  const messages = payload && "messages" in payload ? payload.messages : structured ? null : value;
  const items = (Array.isArray(messages) ? messages : [messages]).flatMap(raw => {
    const message = record(raw);
    const role = message?.role ?? message?.type;
    const content = message && "content" in message ? message.content : raw;
    const text = Array.isArray(content) ? content.map(block => textOf(record(block)?.text ?? block)).join("\n") : textOf(content);
    const tools = toolNames(message?.tool_calls);
    return text.trim() || tools.length ? [{ label: typeof role === "string" ? ROLES.get(role) : undefined, text, tools }] : [];
  });
  const messageTools = new Set(items.flatMap(item => item.tools));
  const otherTools = toolNames(toolCalls).filter(name => !messageTools.has(name));
  if (!items.length && !structured && !otherTools.length) return empty ? <p>{empty}</p> : null;
  return <div className="trace-model-payload">
    {items.map(({ label, text, tools }, index) => <div key={index}>
      {showRoles && label && <p className="trace-model-role">{label}</p>}
      {text && <PreviewText text={text} />}
      {!!tools.length && <p className="trace-model-meta">请求调用工具 · {tools.join("、")}</p>}
    </div>)}
    {structured && <div><p className="trace-model-role">结构化输出</p><PreviewText text={structured} /></div>}
    {!!otherTools.length && <p className="trace-model-meta">请求调用工具 · {otherTools.join("、")}</p>}
  </div>;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function textOf(value: unknown): string {
  return value == null ? "" : typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "");
}

function toolNames(value: unknown): string[] {
  return Array.isArray(value) ? value.flatMap(call => {
    const name = record(call)?.name ?? record(record(call)?.function)?.name;
    return typeof name === "string" ? [name] : [];
  }) : [];
}

function PreviewText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const preview = text.split("\n", 8).join("\n").slice(0, 650);
  const truncated = preview.length < text.length;
  return <>
    <pre>{truncated && !expanded ? `${preview}…` : text}</pre>
    {truncated && <button type="button" className="trace-model-expand" aria-expanded={expanded}
      onClick={() => setExpanded(current => !current)}>{expanded ? "收起全文" : "展开全文"}</button>}
  </>;
}
