// useActiveRunsStream —— 观测大盘 SSE 订阅 + 断线降级回归测试
// （REQ-20260920-193428 FR-005/FR-006 → AC-001/003/004/006 前端部分）。
//
// 守住的核心契约：
//   - run_started 事件插入活跃行（带 session_name，不降级为 trace_id 代称）。
//   - run_finished 移除活跃行并回调 onRunFinished（近期区即时翻转）。
//   - SSE 断开（sse_end ok=false）→ 降级 5s 轮询；重连成功 → 快照对齐 + 回 SSE。
//   - 跨 chunk 的 SSE 帧能正确拼装。
//   - seq 跳变（慢消费者丢帧）→ 主动拉快照对齐。
//
// Tauri 层 mock：@tauri-apps/api/core（invoke）与 event（listen）；
// stream.ts 的中继逻辑（stream_id 过滤、队列、阻塞读）走真实实现。

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { UnlistenFn } from "@tauri-apps/api/event";

type TauriEvent = { event: string; payload: unknown };
type Listener = (e: TauriEvent) => void;

const listeners = new Map<string, Listener[]>();

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (name: string, cb: Listener): Promise<UnlistenFn> => {
    if (!listeners.has(name)) listeners.set(name, []);
    listeners.get(name)!.push(cb);
    return () => {
      listeners.set(name, (listeners.get(name) || []).filter((f) => f !== cb));
    };
  }),
}));

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

vi.mock("@/lib/api", () => ({
  getActiveRuns: vi.fn(),
  evoStreamPath: (path: string) => path,
}));

import { getActiveRuns } from "@/lib/api";
import { useActiveRunsStream } from "@/hooks/useActiveRunsStream";

const mockedGetActiveRuns = vi.mocked(getActiveRuns);

function emitTauri(name: string, payload: unknown) {
  for (const cb of listeners.get(name) || []) cb({ event: name, payload });
}

/** 发一段 SSE 文本 chunk（走真实 stream.ts 中继链路）。 */
function emitChunk(text: string) {
  emitTauri("sse_chunk", { stream_id: currentStreamId(), chunk: text });
}

let streamIdSeq = 0;
const streamIds: string[] = [];
function currentStreamId(): string {
  return streamIds[streamIds.length - 1];
}

function makeEnvelope(seq: number, type: string, data: unknown) {
  return JSON.stringify({ type, seq, emitted_at: "2026-09-20T12:00:00Z", data });
}

function runStartedFrame(seq: number, traceId: string, sessionName: string) {
  return (
    `event: run_started\nid: ${seq}\ndata: ${makeEnvelope(seq, "run_started", {
      trace_id: traceId,
      source: "executor",
      run: {
        trace_id: traceId,
        session_name: sessionName,
        workload: "creation",
        status: "running",
        started_at: "2026-09-20T12:00:00Z",
      },
    })}\n\n`
  );
}

function runFinishedFrame(seq: number, traceId: string, status = "completed") {
  return (
    `event: run_finished\nid: ${seq}\ndata: ${makeEnvelope(seq, "run_finished", {
      trace_id: traceId,
      source: "executor",
      run: {
        trace_id: traceId,
        session_name: "某任务",
        workload: "creation",
        status,
        started_at: "2026-09-20T12:00:00Z",
        ended_at: "2026-09-20T12:01:00Z",
        duration_ms: 60000,
        event_count: 42,
      },
    })}\n\n`
  );
}

/** 快照接口（GET /api/active-runs）返回的行形状。 */
function makeSnapshotRun(traceId: string, sessionName = "快照任务") {
  return {
    trace_id: traceId,
    workspace_id: "ws",
    thread_id: null,
    endpoint: null,
    status: "running",
    started_at: null,
    duration_ms: 1,
    event_count: 1,
    session_name: sessionName,
    ingested: false,
    run_purpose: null,
    workload: "creation",
    integrity_status: "pending",
    coverage: {},
    skill_activation_count: 0,
    middleware_intervention_count: 0,
    hitl_count: 0,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  listeners.clear();
  invokeMock.mockReset();
  streamIds.length = 0;
  // crypto.randomUUID mock：stream.ts 每次连接生成新 id，测试侧记录最新一个。
  let seq = 0;
  vi.spyOn(crypto, "randomUUID").mockImplementation(() => {
    seq += 1;
    const id = `sid-${seq}`;
    streamIds.push(id);
    return id as `${string}-${string}-${string}-${string}-${string}`;
  });
  mockedGetActiveRuns.mockReset().mockResolvedValue([]);
  invokeMock.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function mount() {
  const utils = renderHook(() => useActiveRunsStream());
  // 初始快照 + streamRequest 的 listen await 链落定。
  await act(async () => {});
  return utils;
}

/** 只统计 stream_request 调用（stream_cancel 也是 invoke，须排除，review R4）。 */
function streamRequestCalls(): unknown[][] {
  return invokeMock.mock.calls.filter((c) => c[0] === "stream_request");
}

describe("useActiveRunsStream", () => {
  it("初始拉快照并进入 SSE 订阅", async () => {
    await mount();
    expect(mockedGetActiveRuns).toHaveBeenCalledTimes(1);
    expect(streamRequestCalls()).toHaveLength(1);
    const arg = streamRequestCalls()[0][1] as { request: { path: string } };
    expect(arg.request.path).toContain("/api/events/stream");
  });

  it("run_started 事件插入活跃行，带任务名（AC-001/004 前端）", async () => {
    const { result } = await mount();
    expect(result.current.transport).toBe("connecting");

    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-a", "写一部赛博修仙小说"));
    });

    expect(result.current.transport).toBe("sse");
    expect(result.current.activeRuns).toHaveLength(1);
    expect(result.current.activeRuns[0].trace_id).toBe("trace-a");
    expect(result.current.activeRuns[0].session_name).toBe("写一部赛博修仙小说");
    expect(result.current.activeRuns[0].workload).toBe("creation");
  });

  it("跨 chunk 的帧能正确拼装", async () => {
    const { result } = await mount();
    const frame = runStartedFrame(1, "trace-split", "跨包任务");
    const mid = Math.floor(frame.length / 2);

    await act(async () => {
      emitChunk(frame.slice(0, mid));
    });
    expect(result.current.activeRuns).toHaveLength(0); // 半帧不生效

    await act(async () => {
      emitChunk(frame.slice(mid));
    });
    expect(result.current.activeRuns).toHaveLength(1);
    expect(result.current.activeRuns[0].session_name).toBe("跨包任务");
  });

  it("run_finished 移除活跃行并回调 onRunFinished（AC-003 前端）", async () => {
    const onRunFinished = vi.fn();
    const utils = renderHook(() => useActiveRunsStream(onRunFinished));
    await act(async () => {});
    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-b", "任务B"));
    });
    expect(utils.result.current.activeRuns).toHaveLength(1);

    await act(async () => {
      emitChunk(runFinishedFrame(2, "trace-b", "failed"));
    });
    expect(utils.result.current.activeRuns).toHaveLength(0);
    expect(onRunFinished).toHaveBeenCalledTimes(1);
    const [run, traceId] = onRunFinished.mock.calls[0];
    expect(traceId).toBe("trace-b");
    expect(run.status).toBe("failed");
    expect(run.duration_ms).toBe(60000);
  });

  it("SSE 断开 → 降级 5s 轮询，不再阻塞更新（AC-006 前端）", async () => {
    const { result } = await mount();
    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-c", "任务C"));
    });

    // 断流：Rust 层 emit sse_end ok=false
    await act(async () => {
      emitTauri("sse_end", { stream_id: currentStreamId(), ok: false, error: "conn reset" });
    });
    expect(result.current.transport).toBe("polling");

    // 快照返回新数据：轮询驱动大盘继续更新
    mockedGetActiveRuns.mockResolvedValue([
      {
        trace_id: "trace-c",
        workspace_id: "ws",
        thread_id: null,
        endpoint: null,
        status: "running",
        started_at: null,
        duration_ms: 1,
        event_count: 1,
        session_name: "任务C",
        ingested: false,
        run_purpose: null,
        workload: "creation",
        integrity_status: "pending",
        coverage: {},
        skill_activation_count: 0,
        middleware_intervention_count: 0,
        hitl_count: 0,
      },
    ] as never);
    await act(async () => {
      vi.advanceTimersByTime(5100);
    });
    expect(result.current.activeRuns[0].trace_id).toBe("trace-c");
    expect(mockedGetActiveRuns.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("降级后周期重连，重连成功 → 快照对齐 + 回 SSE 模式（AC-006 前端）", async () => {
    const { result } = await mount();
    await act(async () => {
      emitTauri("sse_end", { stream_id: currentStreamId(), ok: false });
    });
    expect(result.current.transport).toBe("polling");

    // 推进重连周期 → 第二次 invoke stream_request
    await act(async () => {
      vi.advanceTimersByTime(15100);
    });
    expect(streamRequestCalls()).toHaveLength(2);

    // 新连接的首个 chunk（心跳即可）→ 回 sse + 快照对齐
    const snapshotCallsBefore = mockedGetActiveRuns.mock.calls.length;
    await act(async () => {
      emitChunk(": ping\n\n");
    });
    expect(result.current.transport).toBe("sse");
    expect(mockedGetActiveRuns.mock.calls.length).toBeGreaterThan(snapshotCallsBefore);

    // 重连订阅带 since=最后 seq（此处无事件，since=0）
    const arg = streamRequestCalls()[1][1] as { request: { path: string } };
    expect(arg.request.path).toContain("since=");
  });

  it("seq 跳变（丢帧）→ 主动拉快照对齐", async () => {
    const { result } = await mount();
    await act(async () => {
      emitChunk(runStartedFrame(5, "trace-d", "任务D"));
    });
    const callsBefore = mockedGetActiveRuns.mock.calls.length;

    // 下一条事件 seq=9（5→9 跳变）→ 触发快照
    await act(async () => {
      emitChunk(runFinishedFrame(9, "trace-d"));
    });
    expect(mockedGetActiveRuns.mock.calls.length).toBeGreaterThan(callsBefore);
    expect(result.current.activeRuns).toHaveLength(0);
  });

  it("30s 无任何 chunk（含心跳）→ watchdog 降级轮询", async () => {
    const { result } = await mount();
    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-e", "任务E"));
    });
    expect(result.current.transport).toBe("sse");

    await act(async () => {
      vi.advanceTimersByTime(36000);
    });
    expect(result.current.transport).toBe("polling");
  });

  it("卸载时清理订阅与定时器", async () => {
    const { result, unmount } = await mount();
    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-f", "任务F"));
    });
    unmount();

    // 卸载后事件不再引起状态更新（React act 外静默，不断言报错即可）
    emitChunk(runStartedFrame(2, "trace-g", "任务G"));
    expect(result.current.activeRuns).toHaveLength(1);
  });

  it("重复 seq 帧（回放/在线重叠）不重复应用（review R3）", async () => {
    const onRunFinished = vi.fn();
    const utils = renderHook(() => useActiveRunsStream(onRunFinished));
    await act(async () => {});
    await act(async () => {
      emitChunk(runStartedFrame(5, "trace-dup", "任务dup"));
    });
    await act(async () => {
      emitChunk(runStartedFrame(5, "trace-dup", "任务dup")); // 同 seq 重复帧
    });
    expect(utils.result.current.activeRuns).toHaveLength(1);

    await act(async () => {
      emitChunk(runFinishedFrame(6, "trace-dup"));
      emitChunk(runFinishedFrame(6, "trace-dup")); // 同 seq 重复 finished
    });
    expect(utils.result.current.activeRuns).toHaveLength(0);
    expect(onRunFinished).toHaveBeenCalledTimes(1);
  });

  it("run_finished 墓碑阻止滞后快照复活该行（review R3）", async () => {
    const { result } = await mount();
    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-tomb", "任务T"));
    });
    await act(async () => {
      emitChunk(runFinishedFrame(2, "trace-tomb"));
    });
    expect(result.current.activeRuns).toHaveLength(0);

    // 滞后快照：1s 轮询缓存里 trace-tomb 还在——不得复活
    mockedGetActiveRuns.mockResolvedValue([
      { ...makeSnapshotRun("trace-tomb"), session_name: "任务T" },
    ] as never);
    await act(async () => {
      emitChunk(": ping\n\n"); // 心跳不触发快照；用 seq 跳变触发
      emitChunk(runStartedFrame(10, "trace-other", "别的任务"));
    });
    expect(result.current.activeRuns.some((r) => r.trace_id === "trace-tomb")).toBe(false);
    expect(result.current.activeRuns.some((r) => r.trace_id === "trace-other")).toBe(true);
  });

  it("事件行连续 3 次快照缺席后被淘汰（review R3 幽灵行）", async () => {
    mockedGetActiveRuns.mockResolvedValue([] as never);
    const { result } = await mount();
    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-ghost", "幽灵任务"));
    });
    expect(result.current.activeRuns).toHaveLength(1);

    // 降级轮询：每 5s 一次快照（空），3 次缺席后淘汰
    await act(async () => {
      emitTauri("sse_end", { stream_id: currentStreamId(), ok: false });
    });
    await act(async () => {
      vi.advanceTimersByTime(16000); // 3 轮快照
    });
    expect(result.current.activeRuns.some((r) => r.trace_id === "trace-ghost")).toBe(false);
  });

  it("断开后 Tauri 事件监听器被注销（review R3 监听器泄漏）", async () => {
    const utils = renderHook(() => useActiveRunsStream());
    await act(async () => {});
    await act(async () => {
      emitChunk(runStartedFrame(1, "trace-l", "任务L"));
    });
    expect((listeners.get("sse_chunk") || []).length).toBe(1);

    await act(async () => {
      emitTauri("sse_end", { stream_id: currentStreamId(), ok: false, error: "reset" });
    });
    expect(utils.result.current.transport).toBe("polling");
    // pump 退出自清理：本连接的监听器已注销
    expect((listeners.get("sse_chunk") || []).length).toBe(0);
    expect((listeners.get("sse_end") || []).length).toBe(0);
  });

  it("重连后从未收到 chunk 即断：监听器仍被清理（polling 早退路径）", async () => {
    await mount();
    await act(async () => {
      emitTauri("sse_end", { stream_id: currentStreamId(), ok: false });
    });
    await act(async () => {
      vi.advanceTimersByTime(15100); // 触发重连（新 streamRequest + 新监听器）
    });
    expect((listeners.get("sse_chunk") || []).length).toBe(1);

    // 首个 chunk 前就断（mode 仍 polling → degradeToListing 早退）
    await act(async () => {
      emitTauri("sse_end", { stream_id: currentStreamId(), ok: false });
    });
    expect((listeners.get("sse_chunk") || []).length).toBe(0);
  });
});
