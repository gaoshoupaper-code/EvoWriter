import { useEffect, useState } from "react";
import { evoStreamPath } from "@/lib/api";
import { streamRequest } from "@/lib/stream";

type ModelStreamCall = {
  run_id: string;
  reasoning: string;
  text: string;
  finished: boolean;
};

type ModelStreamEvent =
  | { type: "snapshot"; calls: ModelStreamCall[] }
  | { type: "delta"; run_id: string; channel: "reasoning" | "text"; text: string }
  | { type: "end"; run_id: string }
  | { type: "done" };

const MAX_RECENT_CALLS = 16;
const MAX_CHANNEL_CHARS = 24_000;

function parseFrame(frame: string): ModelStreamEvent | null {
  const lines = frame.split("\n");
  const type = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
  if (!type) return null;
  const body = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5)).join("\n");
  try {
    const data = JSON.parse(body);
    if (type === "snapshot" && Array.isArray(data?.calls)) return { type, calls: data.calls };
    if (type === "done") return { type };
    if (typeof data?.run_id !== "string" || !data.run_id) return null;
    if (type === "end") return { type, run_id: data.run_id };
    if (type === "delta" && (data.channel === "reasoning" || data.channel === "text") && typeof data.text === "string") {
      return { type, run_id: data.run_id, channel: data.channel, text: data.text };
    }
  } catch {
    // A malformed frame should not interrupt the remaining output.
  }
  return null;
}

function applyFrame(calls: ModelStreamCall[], event: ModelStreamEvent): ModelStreamCall[] {
  if (event.type === "snapshot") return event.calls.slice(-MAX_RECENT_CALLS).map(call => ({
    ...call, reasoning: call.reasoning.slice(-MAX_CHANNEL_CHARS), text: call.text.slice(-MAX_CHANNEL_CHARS),
  }));
  if (event.type === "done") return [];
  if (event.type === "end") return calls.map(call => call.run_id === event.run_id ? { ...call, finished: true } : call);
  const call = calls.find(item => item.run_id === event.run_id);
  if (!call) return [...calls, { run_id: event.run_id, reasoning: "", text: "", finished: false,
    [event.channel]: event.text.slice(-MAX_CHANNEL_CHARS) }].slice(-MAX_RECENT_CALLS);
  return calls.map(item => item === call ? { ...item,
    [event.channel]: (item[event.channel] + event.text).slice(-MAX_CHANNEL_CHARS) } : item);
}

type StreamStatus = "connecting" | "live" | "retrying" | "closed";

export function useTraceModelStream(traceId: string, enabled: boolean) {
  const [calls, setCalls] = useState<ModelStreamCall[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");

  useEffect(() => {
    setCalls([]);
    if (!enabled) {
      setStatus("closed");
      return;
    }
    let disposed = false;
    let terminal = false;
    let reader: Awaited<ReturnType<typeof streamRequest>> | undefined;
    let watchdog: ReturnType<typeof setInterval> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    setStatus("connecting");

    async function connect() {
      try {
        const connection = await streamRequest(evoStreamPath(`/api/traces/${encodeURIComponent(traceId)}/model-io/stream`), {
          method: "GET",
        });
        reader = connection;
        if (disposed) return;
        const decoder = new TextDecoder();
        let buffer = "";
        let recentChunk = Date.now();
        watchdog = setInterval(() => {
          if (Date.now() - recentChunk > 45_000) void connection.cancel();
        }, 5_000);
        while (!disposed && !terminal) {
          const { done, value } = await connection.read();
          if (disposed || done) break;
          recentChunk = Date.now();
          buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const event = parseFrame(frame);
            if (!event) continue;
            setCalls(current => applyFrame(current, event));
            if (event.type === "done") {
              terminal = true;
              setStatus("closed");
              break;
            }
            if (event.type === "snapshot") setStatus("live");
          }
        }
      } catch (error) {
        if (!disposed && error instanceof Error && /\bHTTP (401|403)\b/.test(error.message)) {
          terminal = true;
          setCalls([]);
          setStatus("closed");
        }
      } finally {
        clearInterval(watchdog);
        if (reader) void reader.cancel();
        reader = undefined;
      }
      if (!disposed && !terminal) {
        setStatus("retrying");
        retry = setTimeout(() => void connect(), 2_000);
      }
    }

    void connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      clearInterval(watchdog);
      if (reader) void reader.cancel();
    };
  }, [traceId, enabled]);

  return { calls: enabled ? calls : [], status: enabled ? status : "closed" };
}
