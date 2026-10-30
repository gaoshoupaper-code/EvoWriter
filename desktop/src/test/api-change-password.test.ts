import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { changePassword, setUnauthorizedHandler } from "@/lib/api";

const relay = vi.mocked(invoke);

beforeEach(() => {
  relay.mockReset();
  setUnauthorizedHandler(null);
});

afterEach(() => { setUnauthorizedHandler(null); });

describe("本人修改密码 API", () => {
  it("独立 POST 两个密码字段，不裁剪空格，不发送确认密码", async () => {
    relay.mockResolvedValue({ status: 200, headers: {}, body: JSON.stringify({ ok: true }) });
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);

    await expect(changePassword(" old secret ", " new secret ")).resolves.toEqual({ ok: true });

    expect(relay).toHaveBeenCalledExactlyOnceWith("http_request", {
      request: {
        path: "/api/auth/change-password",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: { current_password: " old secret ", new_password: " new secret " },
        stream: false,
      },
    });
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("400 原样保留旧密码错误的中文 detail，不触发登录跳转", async () => {
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);
    relay.mockResolvedValue({ status: 400, headers: {}, body: JSON.stringify({ detail: "当前密码错误" }) });

    await expect(changePassword("wrong-secret", "new-secret")).rejects.toThrow("当前密码错误");
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("401 沿用全局未登录处理", async () => {
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);
    relay.mockResolvedValue({ status: 401, headers: {}, body: JSON.stringify({ detail: "请先登录" }) });

    await expect(changePassword("old-secret", "new-secret")).rejects.toThrow("请先登录");
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it.each([
    "upstream unavailable",
    JSON.stringify({ detail: [{ msg: "服务异常" }] }),
  ])("无中文 detail 时沿用状态码错误", async (body) => {
    relay.mockResolvedValue({ status: 503, headers: {}, body });
    await expect(changePassword("old-secret", "new-secret")).rejects.toThrow("API returned 503");
  });

  it("中继失败不伪造成功", async () => {
    relay.mockRejectedValue(new Error("网络连接失败"));
    await expect(changePassword("old-secret", "new-secret")).rejects.toThrow("网络连接失败");
  });
});
