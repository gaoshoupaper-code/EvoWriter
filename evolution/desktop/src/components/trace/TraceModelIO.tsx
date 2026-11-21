import { useCallback, useEffect, useRef, useState } from "react";
import { useTraceModelStream } from "@/hooks/useTraceModelStream";
import { getTraceModelIO, type TraceModelIOPage } from "@/lib/api";
import "./trace-model-io.css";

const ROLES = new Map(Object.entries({
  system: "系统提示词", developer: "开发者消息", human: "用户消息", user: "用户消息",
  ai: "助手消息", assistant: "助手消息", tool: "工具消息", function: "工具消息",
}));

export function TraceModelIO({ traceId, eventCount, modelRationale, live, archiveReady }: {
  traceId: string; eventCount: number; modelRationale?: string; live: boolean; archiveReady: boolean;
}) {
  const [page, setPage] = useState<TraceModelIOPage | null>(null);
  const [syncedEvents, setSyncedEvents] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const inFlight = useRef(false);
  const stream = useTraceModelStream(traceId, live);

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
    if (archiveReady && !loading && !failed && !page?.has_more && syncedEvents !== eventCount) void load();
  }, [archiveReady, load, loading, failed, page?.has_more, syncedEvents, eventCount]);

  const initial = page?.initial_input;
  const archived = new Set(page?.outputs.map(event => event.run_id));
  const pending = stream.calls.filter(call => !archived.has(call.run_id));
  const generating = pending.filter(call => !call.finished);
  const currentCalls = generating.length ? generating : pending.slice(-1);
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
        {stream.status !== "closed" && <section className="trace-model-stream" aria-label="实时模型输出">
          <p className="trace-model-stream-status" role="status">
            {stream.status === "connecting" ? "正在连接实时输出…" : stream.status === "retrying" ? "实时输出重新连接中…"
              : generating.length ? "模型正在输出…" : currentCalls.length ? "本次模型输出已结束" : "等待模型输出…"}
          </p>
          {currentCalls.map(call => <div className="trace-model-payload" key={call.run_id}>
            {call.reasoning && <StreamingText label="思考" text={call.reasoning} />}
            {call.text && <StreamingText label="回复" text={call.text} />}
          </div>)}
        </section>}
        {modelRationale && <div className="trace-model-payload trace-model-summary">
          <h3 className="trace-model-role">本轮改进依据</h3><PreviewText text={modelRationale} />
        </div>}
        {page?.outputs.length ? <ol className="trace-model-responses">
          {page.outputs.map((event, index) => <li key={event.event_id}>
            <h3>{event.node_name || `第 ${index + 1} 次调用`}</h3>
            {event.model_name && <p className="trace-model-meta">{event.model_name}</p>}
            {event.type === "llm_error" && <p className="trace-model-error">{event.error || "模型调用失败。"}</p>}
            <ModelPayload value={event.output} toolCalls={event.tool_calls} showSummary
              empty={event.type === "llm_error" ? undefined : "本次调用未保存可读取的输出正文。"} />
          </li>)}
        </ol> : stream.status === "closed" && page && !loading && !failed && <p>暂无模型输出。</p>}
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

function StreamingText({ label, text }: { label: string; text: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const follow = useRef(true);

  useEffect(() => {
    const element = ref.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [text]);

  return <div>
    <p className="trace-model-role">{label}</p>
    <pre ref={ref} tabIndex={0} aria-label={label} onScroll={({ currentTarget }) => {
      follow.current = currentTarget.scrollHeight - currentTarget.clientHeight - currentTarget.scrollTop <= 8;
    }}>{text}</pre>
  </div>;
}

function ModelPayload({ value, toolCalls, showRoles, showSummary, empty }: {
  value: unknown; toolCalls?: unknown; showRoles?: boolean; showSummary?: boolean; empty?: string;
}) {
  const payload = record(value);
  const structured = textOf(payload?.structured_response);
  const messages = payload && "messages" in payload ? payload.messages : structured ? null : value;
  const items = (Array.isArray(messages) ? messages : [messages]).map(raw => {
    const message = record(raw);
    const role = message?.role ?? message?.type;
    const label = typeof role === "string" ? ROLES.get(role) : undefined;
    const content = message && "content" in message && (label || role == null) ? message.content : raw;
    const { text, summary, hasThinking } = modelContent(content);
    const tools = toolNames(message?.tool_calls);
    return { label, text, summary, hasThinking, tools };
  });
  const summary = showSummary ? items.flatMap(item => item.summary) : [];
  const tools = [...new Set([...items.flatMap(item => item.tools), ...toolNames(toolCalls)])];
  const text = items.filter(item => item.text.trim());
  const unavailableSummary = showSummary && !summary.length && items.some(item => item.hasThinking);
  if (!text.length && !summary.length && !structured && !tools.length && !unavailableSummary) {
    return empty ? <p>{empty}</p> : null;
  }
  return <div className="trace-model-payload">
    {!!summary.length && <div className="trace-model-summary">
      <p className="trace-model-role">思考摘要</p><PreviewText text={summary.join("\n\n")} />
    </div>}
    {unavailableSummary && <p className="trace-model-meta">本次调用没有可读的思考摘要。</p>}
    {text.map(({ label, text }, index) => <div key={index}>
      {showRoles && label && <p className="trace-model-role">{label}</p>}
      <PreviewText text={text} />
    </div>)}
    {structured && <div><p className="trace-model-role">结构化输出</p><PreviewText text={structured} /></div>}
    {!!tools.length && <details className="trace-model-tools">
      <summary>工具调用（{tools.length}）</summary><p>{tools.join("、")}</p>
    </details>}
  </div>;
}

function modelContent(content: unknown) {
  const text: string[] = [];
  const summary: string[] = [];
  let hasThinking = false;
  for (const value of Array.isArray(content) ? content : [content]) {
    const block = record(value);
    if (block?.type === "reasoning") {
      hasThinking = true;
      if (Array.isArray(block.summary)) {
        for (const part of block.summary) {
          const item = record(part);
          if (item?.type === "summary_text" && typeof item.text === "string" && item.text.trim()) summary.push(item.text);
        }
      }
    } else if (typeof value === "string") {
      text.push(value);
    } else if (typeof block?.text === "string"
      && (block.type == null || block.type === "text" || block.type === "input_text" || block.type === "output_text")) {
      text.push(block.text);
    } else if (!Array.isArray(content) && block?.type == null) {
      text.push(textOf(value));
    }
  }
  return { text: text.join("\n"), summary, hasThinking };
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
