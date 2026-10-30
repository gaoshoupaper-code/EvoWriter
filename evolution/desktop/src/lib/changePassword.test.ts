import { invoke } from "@tauri-apps/api/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("changePassword executor auth API", () => {
  let api: typeof import("./api");
  const relay = vi.mocked(invoke);

  beforeEach(async () => {
    vi.resetModules();
    // 生产模式有 evolution 前缀，才能检测误用 evoJson。
    vi.stubEnv("DEV", false);
    vi.useFakeTimers();
    relay.mockReset();
    api = await import("./api");
  });

  afterEach(() => {
    api.setUnauthorizedHandler(null);
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  function respond(status: number, payload: unknown) {
    relay.mockResolvedValueOnce({ status, headers: {}, body: JSON.stringify(payload) });
  }

  it("POST 精确认证路径和 JSON，不裁剪密码、不附加确认密码", async () => {
    respond(200, { ok: true });
    const unauthorized = vi.fn();
    api.setUnauthorizedHandler(unauthorized);

    await expect(api.changePassword(" old password ", " new password ")).resolves.toEqual({ ok: true });

    expect(relay).toHaveBeenCalledTimes(1);
    expect(relay).toHaveBeenCalledWith("http_request", {
      request: {
        path: "/api/auth/change-password",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: { current_password: " old password ", new_password: " new password " },
        stream: false,
      },
    });
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("400 保留后端中文 detail，不触发登录跳转", async () => {
    respond(400, { detail: "当前密码错误" });
    const unauthorized = vi.fn();
    api.setUnauthorizedHandler(unauthorized);

    await expect(api.changePassword("wrong-password", "new-password")).rejects.toThrow("当前密码错误");
    expect(unauthorized).not.toHaveBeenCalled();
    expect(relay).toHaveBeenCalledTimes(1);
  });

  it("401 沿用全局未登录处理", async () => {
    respond(401, { detail: "请先登录" });
    const unauthorized = vi.fn();
    api.setUnauthorizedHandler(unauthorized);

    await expect(api.changePassword("old-password", "new-password")).rejects.toThrow("请先登录");
    expect(unauthorized).toHaveBeenCalledTimes(1);
    expect(relay).toHaveBeenCalledTimes(1);
  });

  it("网络失败保留原始错误", async () => {
    relay.mockRejectedValueOnce(new Error("网络连接失败"));

    await expect(api.changePassword("old-password", "new-password")).rejects.toThrow("网络连接失败");
    expect(relay).toHaveBeenCalledTimes(1);
  });
});
