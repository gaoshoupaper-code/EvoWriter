/**
 * useActiveRunsStream —— 观测大盘活跃运行实时流（REQ-20260920-193428 FR-005/FR-006）。
 *
 * 数据面：初始快照（GET /api/active-runs）→ SSE 订阅（/api/events/stream）事件驱动：
 *   - run_started  → 活跃行插入/更新（事件自带运行中元数据，不降级，FR-003）
 *   - run_finished → 活跃行移除 + onRunFinished 回调（近期区即时翻转，FR-002）
 *
 * 传输面（DEC-008）：
 *   - SSE 断开（sse_end ok=false / 读错误 / 30s 无 chunk）→ 自动降级 5s 轮询；
 *   - 每 15s 重连；重连成功的首个 chunk 触发一次全量快照对齐（不丢行不重复行）；
 *   - seq 跳变（慢消费者丢帧）→ 主动拉快照对齐。
 *
 * 帧解析：SSE 文本可能跨 chunk 拆分，内部按 "\n\n" 缓冲切帧。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { evoStreamPath, getActiveRuns } from "@/lib/api";
import { streamRequest } from "@/lib/stream";
import type { ActiveRun } from "@/lib/types";

const POLL_INTERVAL_MS = 5000;
const RECONNECT_INTERVAL_MS = 15000;
const WATCHDOG_CHECK_MS = 5000;
const WATCHDOG_TIMEOUT_MS = 30000;
/** run_finished 墓碑 TTL：期间快照里的同名行视为滞后数据，不复活（review R3）。 */
const TOMBSTONE_TTL_MS = 60000;
/** 事件行连续缺席快照 N 次后淘汰（防 run_finished 丢失的幽灵行，review R3）。 */
const EVENT_ROW_MISS_LIMIT = 3;

export type TransportMode = "connecting" | "sse" | "polling";

/** run_finished 事件携带的终态摘要（近期区即时渲染用）。 */
export interface FinishedRunInfo {
  trace_id: string;
  session_name: string | null;
  workload: string | null;
  status: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  event_count: number | null;
  error: string | null;
}

/** SSE 事件 envelope（服务端 app/view/events.py publish 的格式）。 */
interface EventEnvelope {
  type: string;
  seq: number;
  emitted_at: string;
  data: {
    trace_id: string;
    source: "executor" | "evolution";
    run: Record<string, unknown> | null;
  };
}

/** 事件 run 字段 → 活跃行（缺省字段按"运行中"降级补齐）。 */
function eventRunToActiveRun(run: Record<string, unknown> | null, fallbackTraceId: string): ActiveRun | null {
  const traceId = (run?.["trace_id"] as string) || fallbackTraceId;
  if (!traceId) return null;
  return {
    trace_id: traceId,
    workspace_id: (run?.["workspace_id"] as string) || "",
    thread_id: (run?.["thread_id"] as string) ?? null,
    endpoint: (run?.["endpoint"] as string) ?? null,
    status: ((run?.["status"] as string) || "running") as ActiveRun["status"],
    started_at: (run?.["started_at"] as string) ?? null,
    duration_ms: (run?.["duration_ms"] as number) ?? null,
    event_count: (run?.["event_count"] as number) ?? 0,
    session_name: (run?.["session_name"] as string) || null,
    ingested: false,
    run_purpose: (run?.["run_purpose"] as string) || null,
    service: (run?.["service"] as string) || null,
    workload: ((run?.["workload"] as string) || null) as ActiveRun["workload"],
    integrity_status: "pending",
    coverage: {},
    skill_activation_count: 0,
    middleware_intervention_count: 0,
    hitl_count: 0,
  };
}

export function useActiveRunsStream(onRunFinished?: (run: FinishedRunInfo | null, traceId: string) => void) {
  const [activeRuns, setActiveRuns] = useState<ActiveRun[]>([]);
  const [transport, setTransport] = useState<TransportMode>("connecting");

  const modeRef = useRef<TransportMode>("connecting");
  const lastSeqRef = useRef<number | null>(null);
  const frameBufferRef = useRef("");
  const readerRef = useRef<{ cancel: () => Promise<void> } | null>(null);
  const cancelledRef = useRef(false);
  const lastChunkAtRef = useRef(0);
  const pollTimerRef = useRef<number | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const watchdogTimerRef = useRef<number | null>(null);
  // run_finished 墓碑：trace_id → 过期时刻。快照合并时过滤（防滞后快照复活终态行）。
  const tombstonesRef = useRef<Map<string, number>>(new Map());
  // 事件行缺席计数：trace_id → 连续未出现在快照中的次数。
  const missCountRef = useRef<Map<string, number>>(new Map());
  // 回调用 ref 保存：重连逻辑不因回调身份变化而重建。
  const finishedCbRef = useRef(onRunFinished);
  finishedCbRef.current = onRunFinished;

  const setMode = useCallback((mode: TransportMode) => {
    modeRef.current = mode;
    setTransport(mode);
  }, []);

  const refreshSnapshot = useCallback(async () => {
    try {
      const runs = await getActiveRuns();
      if (cancelledRef.current) return;
      setActiveRuns((prev) => {
        // 墓碑：run_finished 刚到的行，1s 轮询缓存快照里可能还在——过滤防复活。
        const now = Date.now();
        const tombstones = tombstonesRef.current;
        for (const [tid, expiresAt] of tombstones) {
          if (expiresAt <= now) tombstones.delete(tid);
        }
        const fresh = runs.filter((r) => !tombstones.has(r.trace_id));
        // 合并语义：快照为准，但保留「事件先到、快照尚未见」的活跃行——
        // evolution 对 executor 的轮询缓存有 ~1s 滞后，直连快照会闪掉新运行。
        // 淘汰：缺席超过 EVENT_ROW_MISS_LIMIT 次的事件行视为幽灵行（run_finished
        // 丢失场景），随快照丢弃，不悬挂。
        const snapshotIds = new Set(fresh.map((r) => r.trace_id));
        const missCount = missCountRef.current;
        const eventOnly = prev.filter((r) => {
          if (r.ingested || snapshotIds.has(r.trace_id)) {
            missCount.delete(r.trace_id);
            return false;
          }
          const count = (missCount.get(r.trace_id) ?? 0) + 1;
          missCount.set(r.trace_id, count);
          return count <= EVENT_ROW_MISS_LIMIT;
        });
        return eventOnly.length > 0 ? [...fresh, ...eventOnly] : fresh;
      });
    } catch {
      // 快照失败保留旧数据；SSE 事件 / 下轮轮询会继续推进。
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current != null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const stopReconnect = useCallback(() => {
    if (reconnectTimerRef.current != null) {
      window.clearInterval(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const clearWatchdog = useCallback(() => {
    if (watchdogTimerRef.current != null) {
      window.clearInterval(watchdogTimerRef.current);
      watchdogTimerRef.current = null;
    }
  }, []);

  /** 处理一个完整 SSE 帧（已按 \n\n 切好）。 */
  const handleFrame = useCallback((block: string) => {
    if (block.startsWith(":")) return; // 心跳注释帧：保活信号，无事件语义
    let eventName = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    let envelope: EventEnvelope;
    try {
      envelope = JSON.parse(data) as EventEnvelope;
    } catch {
      return; // 半截/异常 JSON：丢弃，等快照对齐兜底
    }

    // seq 单调对齐：重复帧（回放/在线窗口重叠）跳过；跳变说明丢帧
    //（慢消费者/断线窗口）→ 快照兜底。
    if (typeof envelope.seq === "number") {
      if (lastSeqRef.current != null) {
        if (envelope.seq <= lastSeqRef.current) return;
        if (envelope.seq > lastSeqRef.current + 1) {
          void refreshSnapshot();
        }
      }
      lastSeqRef.current = envelope.seq;
    }

    const type = envelope.type || eventName;
    if (type === "run_started") {
      const row = eventRunToActiveRun(envelope.data?.run, envelope.data?.trace_id);
      if (row) {
        setActiveRuns((prev) => {
          const index = prev.findIndex((r) => r.trace_id === row.trace_id);
          if (index >= 0) {
            const next = [...prev];
            next[index] = row;
            return next;
          }
          return [row, ...prev];
        });
      }
    } else if (type === "run_finished") {
      const traceId = envelope.data?.trace_id;
      if (traceId) {
        setActiveRuns((prev) => prev.filter((r) => r.trace_id !== traceId));
        // 墓碑：滞后快照（1s 轮询缓存）里该行可能还在，TTL 内不复活。
        tombstonesRef.current.set(traceId, Date.now() + TOMBSTONE_TTL_MS);
        missCountRef.current.delete(traceId);
        const raw = envelope.data?.run ?? null;
        finishedCbRef.current?.(
          raw
            ? {
                trace_id: (raw["trace_id"] as string) || traceId,
                session_name: (raw["session_name"] as string) ?? null,
                workload: (raw["workload"] as string) ?? null,
                status: (raw["status"] as string) ?? null,
                started_at: (raw["started_at"] as string) ?? null,
                ended_at: (raw["ended_at"] as string) ?? null,
                duration_ms: (raw["duration_ms"] as number) ?? null,
                event_count: (raw["event_count"] as number) ?? null,
                error: (raw["error"] as string) ?? null,
              }
            : null,
          traceId,
        );
      }
    }
  }, [refreshSnapshot]);

  /** 断线降级：轮询兜底 + 周期重连（DEC-008）。幂等：已降级则只保持。 */
  const degradeToListing = useCallback(() => {
    if (cancelledRef.current || modeRef.current === "polling") return;
    setMode("polling");
    // 丢弃半截帧缓冲，重连后从快照重建。
    frameBufferRef.current = "";
    clearWatchdog();
    void readerRef.current?.cancel().catch(() => undefined);
    readerRef.current = null;
    if (pollTimerRef.current == null) {
      pollTimerRef.current = window.setInterval(() => {
        void refreshSnapshot();
      }, POLL_INTERVAL_MS);
    }
    if (reconnectTimerRef.current == null) {
      reconnectTimerRef.current = window.setInterval(() => {
        void connectSseRef.current();
      }, RECONNECT_INTERVAL_MS);
    }
  }, [clearWatchdog, refreshSnapshot, setMode]);

  // connectSse 与 degradeToListing 互相引用：ref 打破环。
  const connectSseRef = useRef<() => Promise<void>>(async () => {});

  const connectSse = useCallback(async () => {
    if (cancelledRef.current || modeRef.current === "sse") return;
    frameBufferRef.current = "";
    // watchdog 基准重置：从发起连接起算（覆盖「TCP 已连但响应头迟迟不来」
    // 的悬挂形态，review R3）。
    lastChunkAtRef.current = Date.now();
    const since = lastSeqRef.current ?? 0;
    try {
      const reader = await streamRequest(evoStreamPath(`/api/events/stream?since=${since}`), {
        method: "GET",
      });
      if (cancelledRef.current) {
        await reader.cancel().catch(() => undefined);
        return;
      }
      readerRef.current = reader;
      const decoder = new TextDecoder();

      const pump = async () => {
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            lastChunkAtRef.current = Date.now();
            // 重连成功的首个 chunk（含心跳）：回 SSE 模式 + 快照对齐（DEC-008）。
            if (modeRef.current !== "sse") {
              setMode("sse");
              stopPolling();
              stopReconnect();
              void refreshSnapshot();
            }
            const text = decoder.decode(value, { stream: true });
            frameBufferRef.current += text;
            const blocks = frameBufferRef.current.split("\n\n");
            frameBufferRef.current = blocks.pop() ?? "";
            for (const block of blocks) {
              if (block.trim()) handleFrame(block);
            }
          }
        } catch {
          // 读错误（sse_end ok=false 会 throw）→ 降级
        }
        // 资源自释放（幂等）：degradeToListing 在 polling 态会早退跳过 cancel，
        // 这里保证本连接的 Tauri 监听器必被注销（review R3 监听器泄漏）。
        await reader.cancel().catch(() => undefined);
        if (readerRef.current === reader) readerRef.current = null;
        degradeToListing();
      };
      void pump();

      // watchdog：连接/推送阶段 30s 无任何 chunk（含心跳）视为僵死流。
      // 覆盖 connecting（头悬挂）与 sse（流僵死）两态（review R3）。
      clearWatchdog();
      watchdogTimerRef.current = window.setInterval(() => {
        if (modeRef.current !== "polling" && Date.now() - lastChunkAtRef.current > WATCHDOG_TIMEOUT_MS) {
          degradeToListing();
        }
      }, WATCHDOG_CHECK_MS);
    } catch {
      degradeToListing();
    }
  }, [clearWatchdog, degradeToListing, handleFrame, refreshSnapshot, setMode, stopPolling, stopReconnect]);

  useEffect(() => {
    connectSseRef.current = connectSse;
  }, [connectSse]);

  useEffect(() => {
    cancelledRef.current = false;
    void refreshSnapshot();
    void connectSse();
    return () => {
      cancelledRef.current = true;
      stopPolling();
      stopReconnect();
      clearWatchdog();
      void readerRef.current?.cancel().catch(() => undefined);
      readerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在挂载时建连；回调经 ref 透传。
  }, []);

  return { activeRuns, transport };
}
