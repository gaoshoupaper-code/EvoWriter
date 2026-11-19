import { apiJson, type ConversationMessage } from "./api";
import { applyRowEvent, closeStreamingRows, concatTextRows } from "./rows";
import { streamRequest } from "./stream";
import type { ChatMessage, ThreadSummary } from "./types";

export const agents = {
  outline: "总纲", volume: "卷纲", chapter: "章纲", text: "正文",
} as const;
export type Agent = keyof typeof agents;

export type CreativeTask = {
  task_id: string;
  thread_id: string;
  status: "new" | "running" | "awaiting_input" | "interrupted" | "completed" | "stopped";
  volume: number;
  chapter: number;
  progress: string[];
  harness_commit: string;
  trace_id?: string;
  awaiting_input?: ChatMessage["awaitingInput"];
};
export type Entry = {
  agent: Agent;
  missing: string[];
  threads: ThreadSummary[];
  last_thread_id: string | null;
  task: CreativeTask | null;
  conflicts: { id: number; name: string; field: string; current: string; proposed: string }[];
};

export function post<T>(path: string, body: unknown) {
  return apiJson<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export function conversation(rows: ConversationMessage[], task: CreativeTask | null): ChatMessage[] {
  const messages: ChatMessage[] = rows.map(row => ({
    role: row.role, content: row.content, traceId: row.trace_id ?? undefined,
    contentFormat: row.role === "assistant" ? "markdown" : "text",
  }));
  if (task?.status === "awaiting_input" && task.awaiting_input) {
    const last = messages.at(-1);
    const message: ChatMessage = last?.role === "assistant" && last.traceId === task.trace_id
      ? last : { role: "assistant", content: "", contentFormat: "markdown" };
    message.traceId = task.trace_id;
    message.awaitingInput = task.awaiting_input;
    if (!message.content.includes(task.awaiting_input.question)) {
      message.content = [message.content, task.awaiting_input.question].filter(Boolean).join("\n\n");
    }
    if (message !== last) messages.push(message);
  }
  return messages;
}

type Data = Record<string, unknown>;
export function updateReply(message: ChatMessage, type: string, data: Data): ChatMessage {
  if (type === "row") {
    const rows = applyRowEvent(message.rows, data);
    return { ...message, rows, content: concatTextRows(rows) };
  }
  if (type === "model_stream" && !message.rows?.length) {
    return { ...message, content: message.content + String(data.content ?? "") };
  }
  if (type === "interrupt") {
    const question = String(data.question ?? "等待你的输入");
    return { ...message, status: "completed", rows: closeStreamingRows(message.rows, "interrupted"),
      content: message.rows?.length ? message.content : question, awaitingInput: { ...data, question } };
  }
  if (type === "final") {
    return { ...message, status: "completed", rows: closeStreamingRows(message.rows, "complete"),
      content: String(data.final_text || data.content || message.content) };
  }
  return message;
}

export async function readCreativeStream(reader: Awaited<ReturnType<typeof streamRequest>>,
  onFrame: (type: string, data: Data) => void) {
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      buffer = buffer.replaceAll("\r\n", "\n");
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const lines = frame.split("\n");
        const type = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
        const data = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (type && data) onFrame(type, JSON.parse(data));
      }
      if (done) break;
    }
  } finally {
    await reader.cancel();
  }
}
