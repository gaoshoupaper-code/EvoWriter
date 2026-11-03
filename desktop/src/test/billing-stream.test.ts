import { beforeEach, describe, expect, it, vi } from "vitest";

const relay = vi.hoisted(() => ({ listeners: new Map<string, (event: { payload: unknown }) => void>() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async (name: string, callback: (event: { payload: unknown }) => void) => {
  relay.listeners.set(name, callback);
  return () => relay.listeners.delete(name);
}) }));
import { invoke } from "@tauri-apps/api/core";
import { streamRequest } from "@/lib/stream";

beforeEach(() => { vi.clearAllMocks(); relay.listeners.clear(); });

describe("Rust 中继的计费拒绝", () => {
  it("等待中的 read 收到 HTTP 拒绝时抛出原因，不能伪装正常关流", async () => {
    const reader = await streamRequest("/api/screenplay/generate/stream");
    const streamId = vi.mocked(invoke).mock.calls[0][1] as { request: { stream_id: string } };
    const pending = reader.read();
    const rejected = expect(pending).rejects.toThrow("HTTP 402");
    relay.listeners.get("sse_end")!({ payload: { stream_id: streamId.request.stream_id, ok: false, error: "HTTP 402: 可用积分不足" } });
    await rejected;
    await reader.cancel();
  });
});
