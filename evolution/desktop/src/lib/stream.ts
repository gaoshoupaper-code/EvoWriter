/**
 * SSE 流式请求封装（移植自写作端 desktop/src/lib/stream.ts，设计文档 S11/T12）。
 *
 * 桌面端 SSE 走 Rust 中继（stream_request command + sse_chunk/sse_end event）。
 * reader-like 接口模拟 fetch 的 response.body.getReader()，下游按 \n\n 解析 SSE。
 *
 * 工作流：
 * 1. 生成 stream_id
 * 2. listen("sse_chunk") + listen("sse_end") 注册监听
 * 3. invoke("stream_request") 启动 Rust 流式拉取（后台逐 chunk emit）
 * 4. read() 从内部队列取 chunk（阻塞式 Promise）
 * 5. cancel() 主动停止（断线降级 / 组件卸载）
 *
 * 观测大盘实时化（REQ-20260920-193428 FR-005）：本文件是通用中继封装，
 * 事件语义（run_started/run_finished）由 hooks/useActiveRunsStream 处理。
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

/// Rust emit 的 chunk event payload（对应 src-tauri/src/http.rs SseChunk）。
interface SseChunkPayload {
  stream_id: string;
  chunk: string;
}

/// Rust emit 的结束 event payload（对应 src-tauri/src/http.rs SseEnd）。
interface SseEndPayload {
  stream_id: string;
  ok: boolean;
  error?: string;
}

/// reader.read() 返回值（模拟 ReadableStreamReadResult）。
export interface StreamReadResult {
  done: boolean;
  value: Uint8Array | undefined;
}

/// streamRequest 参数（与原 fetch 的 RequestInit 子集对齐）。
export interface StreamRequestInit {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
}

/**
 * 发起流式请求，返回一个 reader-like 对象。
 *
 * body 直接传对象（不是 JSON.stringify）——Rust 端 reqwest 接 serde_json::Value。
 */
export async function streamRequest(path: string, init: StreamRequestInit = {}): Promise<{
  read: () => Promise<StreamReadResult>;
  cancel: () => Promise<void>;
}> {
  const streamId = crypto.randomUUID();

  // 内部状态
  const queue: Uint8Array[] = [];
  let done = false;
  let error: string | null = null;
  let waiter: ((r: StreamReadResult) => void) | null = null;

  // 解析 body：兼容旧代码传字符串的情况。
  let bodyValue: unknown = init.body;
  if (typeof bodyValue === "string") {
    try {
      bodyValue = JSON.parse(bodyValue);
    } catch {
      // 纯文本 body，保留原样
    }
  }

  // 注册监听
  const unlistenChunk = await listen<SseChunkPayload>("sse_chunk", (event) => {
    if (event.payload.stream_id !== streamId) return;
    const bytes = new TextEncoder().encode(event.payload.chunk);
    queue.push(bytes);
    // 唤醒等待中的 read()
    if (waiter) {
      const w = waiter;
      waiter = null;
      w({ done: false, value: queue.shift() });
    }
  });

  const unlistenEnd = await listen<SseEndPayload>("sse_end", (event) => {
    if (event.payload.stream_id !== streamId) return;
    if (!event.payload.ok) {
      error = event.payload.error ?? "stream ended with error";
    }
    done = true;
    // 唤醒等待中的 read()（返回 done:true）
    if (waiter) {
      const w = waiter;
      waiter = null;
      w({ done: true, value: undefined });
    }
  });

  // 启动 Rust 流式拉取（后台运行，chunk 通过 event 推回）
  invoke("stream_request", {
    request: {
      path,
      method: init.method ?? "POST",
      headers: init.headers ?? null,
      body: bodyValue ?? null,
      stream_id: streamId,
    },
  }).catch((e) => {
    // invoke 本身失败（连接失败已在 Rust 端 emit sse_end，这里兜底）
    error = String(e);
    done = true;
    if (waiter) {
      const w = waiter;
      waiter = null;
      w({ done: true, value: undefined });
    }
  });

  // reader.read()：从队列取 chunk，空则阻塞等待 event。
  const read = (): Promise<StreamReadResult> => {
    if (queue.length > 0) {
      return Promise.resolve({ done: false, value: queue.shift() });
    }
    if (done) {
      if (error) throw new Error(error);
      return Promise.resolve({ done: true, value: undefined });
    }
    // 队列空 + 未结束：阻塞，等 chunk/end event 唤醒
    return new Promise<StreamReadResult>((resolve) => {
      waiter = resolve;
    });
  };

  // cancel()：断线降级 / 组件卸载。取消监听，标记 done，
  // 并通知 Rust 中止后台流（review R4：防弃掉的连接靠服务端心跳存活）。
  const cancel = async (): Promise<void> => {
    unlistenChunk();
    unlistenEnd();
    done = true;
    // fire-and-forget：流可能已自然结束（Rust 侧表项已摘），失败忽略。
    invoke("stream_cancel", { streamId }).catch(() => undefined);
    if (waiter) {
      const w = waiter;
      waiter = null;
      w({ done: true, value: undefined });
    }
  };

  return { read, cancel };
}

// 保留 UnlistenFn 引用避免未使用告警（listen 返回类型显式化）。
export type { UnlistenFn };
