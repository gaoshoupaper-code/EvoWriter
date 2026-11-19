import { useCallback, useEffect, useRef, useState } from "react";
import { apiJson, type BillingOperation, type ConversationMessage } from "@/lib/api";
import { agents, conversation, post, readCreativeStream, updateReply } from "@/lib/creative-agents";
import type { Agent, CreativeTask, Entry } from "@/lib/creative-agents";
import type { ChatMessage, ThreadSummary } from "@/lib/types";
import { streamRequest } from "@/lib/stream";
import { refreshWorkspaceContent } from "@/lib/usePanelPolling";
import { useBillingStore } from "@/stores/billing";
import { ChatPanel } from "./ChatPanel";

export function CreativeWorkspace({ workspaceId }: { workspaceId: string }) {
  const [agent, setAgent] = useState<Agent>("outline");
  return <section className="creative-workspace" aria-label="创作工作区">
    <nav className="creative-agents" aria-label="创作入口">
      {(Object.keys(agents) as Agent[]).map(id =>
        <button key={id} type="button" aria-pressed={agent === id} onClick={() => setAgent(id)}>{agents[id]}</button>)}
    </nav>
    <AgentChat key={workspaceId + "/" + agent} workspaceId={workspaceId} agent={agent} />
  </section>;
}

function AgentChat({ workspaceId, agent }: { workspaceId: string; agent: Agent }) {
  const base = `/api/workspaces/${encodeURIComponent(workspaceId)}/agents/${agent}`;
  const [volume, setVolume] = useState(1);
  const [chapter, setChapter] = useState(1);
  const [entry, setEntry] = useState<Entry | null>(null);
  const [thread, setThread] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [switching, setSwitching] = useState(true);
  const request = useRef(0);
  const mounted = useRef(false);
  const sending = useRef(false);
  const restoreTarget = useRef(true);
  const requestedThread = useRef("");
  const reader = useRef<Awaited<ReturnType<typeof streamRequest>> | null>(null);
  const task = entry?.task;
  const active = busy || task?.status === "running";

  const load = useCallback(async (selected?: string, quiet = false) => {
    const version = ++request.current;
    if (!quiet) { setSwitching(true); setMessages([]); setPrompt(""); setError(""); }
    try {
      const params = new URLSearchParams({ volume: String(volume), chapter: String(chapter) });
      if (selected) params.set("thread_id", selected);
      const state = await apiJson<Entry>(base + "/entry?" + params);
      if (!mounted.current || version !== request.current) return;
      const id = selected || state.last_thread_id || "";
      requestedThread.current = id;
      if (restoreTarget.current) {
        restoreTarget.current = false;
        if (state.task && (state.task.volume !== volume || state.task.chapter !== chapter)) {
          setVolume(state.task.volume); setChapter(state.task.chapter);
          return;
        }
      }
      const rows = id && (!state.missing.length || state.task)
        ? await apiJson<ConversationMessage[]>(base + "/threads/" + encodeURIComponent(id) + "/conversation") : [];
      if (!mounted.current || version !== request.current) return;
      setEntry(state); setThread(id); setMessages(conversation(rows, state.task));
    } catch (err) {
      if (mounted.current && version === request.current) {
        setError(String(err));
        if (!quiet) { setThread(""); setEntry(null); }
      }
    } finally {
      if (mounted.current && version === request.current) setSwitching(false);
    }
  }, [base, volume, chapter]);
  useEffect(() => {
    mounted.current = true;
    void load(requestedThread.current);
    return () => { mounted.current = false; ++request.current; void reader.current?.cancel(); };
  }, [load]);
  useEffect(() => {
    if (busy || task?.status !== "running" || !thread) return;
    const timer = window.setInterval(() => {
      void load(thread, true);
      void refreshWorkspaceContent(workspaceId);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [busy, task?.status, thread, load, workspaceId]);

  async function createThread() {
    const result = await post<ThreadSummary>(base + "/threads", { volume, chapter });
    return result.thread_id;
  }
  async function newConversation() {
    setSwitching(true);
    try { const id = await createThread(); if (mounted.current) await load(id); }
    catch (err) { if (mounted.current) { setError(String(err)); setSwitching(false); } }
  }

  async function send(text: string, continuing = false) {
    if (sending.current || switching || !entry || (!continuing && !text.trim()) || active) return;
    sending.current = true; setBusy(true); setError("");
    let id = thread;
    try {
      const budget = continuing ? undefined : await useBillingStore.getState().submissionBudget(agent);
      if (!mounted.current) return;
      if (!id) id = await createThread();
      if (!mounted.current) return;
      setThread(id);
      const awaiting = task?.status === "awaiting_input";
      const endpoint = continuing && task ? base + "/tasks/" + task.task_id + "/continue"
        : base + "/threads/" + encodeURIComponent(id) + "/generate/stream";
      const body = continuing ? {} : { thread_id: id, volume, chapter, message_id: crypto.randomUUID(),
        billing_protocol: 2, billing_budget: budget,
        ...(awaiting ? { resume: text.trim(), trace_id: task.trace_id } : { prompt: text.trim() }) };
      const stream = await streamRequest(endpoint, { method: "POST", body });
      if (!mounted.current) { await stream.cancel(); return; }
      reader.current = stream;
      let reply: ChatMessage = { role: "assistant", content: "", status: "streaming", contentFormat: "markdown" };
      setMessages(previous => [
        ...previous.map(m => m.awaitingInput ? { ...m, awaitingInput: undefined } : m),
        ...(continuing ? [] : [{ role: "user" as const, content: text.trim() }]), reply,
      ]);
      setPrompt("");
      await readCreativeStream(stream, (type, data) => {
        if (!mounted.current) return;
        if (type === "creative_task") {
          const started = data as CreativeTask;
          setEntry(e => e ? { ...e, task: started } : null);
        } else if (type === "billing" && typeof data.operation_id === "string") {
          useBillingStore.getState().receiveOperation(data as BillingOperation);
        } else if (type === "billing_reconcile") {
          void useBillingStore.getState().refresh();
        } else if (["error", "billing_error", "credit_exhausted"].includes(type)) {
          setError(String(data.message || data.error || "创作中断，请查看任务状态。"));
        } else {
          const next = updateReply(reply, type, data);
          if (next !== reply) {
            reply = next;
            setMessages(previous => [...previous.slice(0, -1), next]);
          }
        }
      });
    } catch (err) {
      if (mounted.current) { setError(String(err)); if (!continuing) setPrompt(text); }
    } finally {
      reader.current = null;
      if (mounted.current) { await load(id, true); setBusy(false); }
      void refreshWorkspaceContent(workspaceId);
      sending.current = false;
    }
  }
  async function stop() {
    if (!task) return;
    try {
      await post(base + "/tasks/" + task.task_id + "/stop", {});
      await reader.current?.cancel();
      if (mounted.current) await load(thread, true);
    } catch (err) { if (mounted.current) setError(String(err)); }
  }
  async function resolve(id: number, choice: "current" | "proposed") {
    try {
      await post(base + "/conflicts/" + id + "/resolve", { choice });
      if (mounted.current) await load(thread, true);
      void refreshWorkspaceContent(workspaceId);
    } catch (err) { if (mounted.current) setError(String(err)); }
  }

  const missing = entry?.missing ?? [];
  const resumable = task?.status === "interrupted" || task?.status === "awaiting_input" || task?.status === "running";
  return <>
    <div className="creative-controls">
      {agent !== "outline" && <label>卷号 <input aria-label="卷号" type="number" min={1} value={volume} disabled={active}
        onChange={e => setVolume(Math.max(1, Math.trunc(Number(e.target.value) || 1)))} /></label>}
      {(agent === "chapter" || agent === "text") && <label>章号 <input aria-label="章号" type="number" min={1} value={chapter} disabled={active}
        onChange={e => setChapter(Math.max(1, Math.trunc(Number(e.target.value) || 1)))} /></label>}
      {entry && entry.threads.length > 0 && <select aria-label="当前会话" value={thread} disabled={switching || busy}
        onChange={e => { restoreTarget.current = true; void load(e.target.value); }}>
        {entry.threads.map(t => <option key={t.thread_id} value={t.thread_id}>{t.session_name}</option>)}
      </select>}
      <button type="button" disabled={!entry || !!missing.length || switching || busy}
        onClick={() => void newConversation()}>新会话</button>
    </div>
    {error && <p role="alert">{error} <button type="button" onClick={() => void load(thread)}>重新加载</button></p>}
    {switching ? <p role="status">正在读取{agents[agent]}会话…</p> :
      entry && <>
        {missing.length > 0 && <p role="status">缺少{missing.join("、")}。<button type="button" onClick={() => void load(thread)}>重新检查</button></p>}
        {task?.status === "interrupted" && <section aria-label="任务中断">
          <p>第 {task.volume} 卷 · 第 {task.chapter} 章的任务已中断。</p>
          <p>{task.progress.length ? "已保存：" + task.progress.join("、") : "尚无已保存的稿件"}</p>
          <button type="button" disabled={busy} onClick={() => void send("", true)}>继续原任务</button>
        </section>}
        {entry.conflicts.map(conflict => <section key={conflict.id} aria-label="人物卡冲突">
          <p>{conflict.name}的「{conflict.field}」有两项改动，请选用一项。</p>
          <button type="button" onClick={() => void resolve(conflict.id, "current")}>保留当前值 · {conflict.current}</button>
          <button type="button" onClick={() => void resolve(conflict.id, "proposed")}>采用本次值 · {conflict.proposed}</button>
        </section>)}
        {(!missing.length || resumable) && <ChatPanel creativeAgent={{ id: agent, label: agents[agent] }} messages={messages}
          prompt={prompt} loading={active} activeThreadId={thread} hasActiveWorkspace
          inputLocked={active || task?.status === "interrupted" || (!!missing.length && !resumable)}
          onPromptChange={setPrompt} onSubmit={e => { e.preventDefault(); void send(prompt); }}
          onResumeSubmit={value => send(value)} onStop={() => void stop()} />}
      </>}
  </>;
}
