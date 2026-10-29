import { beforeEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { advanceWritingStage } from "@/lib/api";

const relay = vi.mocked(invoke);

beforeEach(() => { relay.mockReset(); });

describe("段审确认请求", () => {
  it("携带独立审查编号，让同一 trace 的后续审查继续推进", async () => {
    relay.mockResolvedValue({ status: 200, headers: {}, body: JSON.stringify({ phase: "chapter" }) });
    await expect(advanceWritingStage("ws-1", "trace-shared", "review-volume")).resolves.toEqual({ phase: "chapter" });
    expect(relay).toHaveBeenCalledWith("http_request", {
      request: {
        path: "/api/workspaces/ws-1/writing/stage/advance", method: "POST",
        headers: { "Content-Type": "application/json" },
        body: { trace_id: "trace-shared", review_id: "review-volume" }, stream: false,
      },
    });
  });

  it("409 展示服务端的具体阻断原因", async () => {
    relay.mockResolvedValue({
      status: 409, headers: {}, body: JSON.stringify({ detail: "第 1 卷尚未定稿。" }),
    });
    await expect(advanceWritingStage("ws-1", "trace-shared", "review-volume"))
      .rejects.toThrow("第 1 卷尚未定稿。");
  });

  it.each([
    JSON.stringify({ detail: [{ msg: "校验错误" }] }),
    "upstream unavailable",
  ])("错误响应无法提供说明时保留状态码", async (body) => {
    relay.mockResolvedValue({ status: 409, headers: {}, body });
    await expect(advanceWritingStage("ws-1", "trace-shared")).rejects.toThrow("API returned 409");
  });
});
