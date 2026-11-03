import { beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { adjustCredits, createInviteCodes, fetchBillingConfig, fetchBillingOperations, fetchUserTransactions, saveBillingPrice } from "./api";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
beforeEach(() => { vi.mocked(invoke).mockReset().mockResolvedValue({ status: 200, headers: {}, body: "{}" }); });

it("发分与邀请码请求传递调用方的稳定标识，不在 API 层重生成", async () => {
  await adjustCredits("owner", 12.34, "补发", "same-request");
  await adjustCredits("owner", 12.34, "补发", "same-request");
  const first = vi.mocked(invoke).mock.calls[0][1] as { request: { body: unknown } };
  const retry = vi.mocked(invoke).mock.calls[1][1] as { request: { body: unknown } };
  expect(first.request.body).toEqual({ amount: 12.34, note: "补发", request_id: "same-request" });
  expect(retry.request.body).toEqual(first.request.body);
  await createInviteCodes(2, 0.25, "试用", "invite-request");
  const invite = vi.mocked(invoke).mock.calls[2][1] as { request: { body: unknown } };
  expect(invite.request.body).toEqual({ count: 2, granted_credits: 0.25, note: "试用", request_id: "invite-request" });
});

it("模型标识编码后传递，积分系数不换算或取整", async () => {
  await saveBillingPrice("provider/model", { input_per_million: "0.001", output_per_million: "2", cached_per_million: "0" });
  const request = vi.mocked(invoke).mock.calls[0][1] as { request: { path: string; body: unknown } };
  expect(request.request.path).toBe("/api/admin/billing/prices/provider%2Fmodel");
  expect(request.request.body).toEqual({ input_per_million: "0.001", output_per_million: "2", cached_per_million: "0" });
});

it("结构化计费错误显示服务端说明，不显示原始 JSON", async () => {
  vi.mocked(invoke).mockResolvedValue({ status: 409, headers: {}, body: JSON.stringify({ detail: { code: "model_unpriced", message: "请先配置有效积分系数" } }) });
  await expect(fetchBillingConfig()).rejects.toThrow("请先配置有效积分系数");
});

it("单用户账目向服务端传递筛选和条数，不依赖全局列表过滤", async () => {
  await fetchBillingOperations(200, "quiet author&role=all");
  await fetchUserTransactions("quiet author", 100);
  const requests = vi.mocked(invoke).mock.calls.map((call) => (call[1] as { request: { path: string } }).request.path);
  expect(requests).toEqual([
    "/api/admin/billing/operations?limit=200&user_id=quiet+author%26role%3Dall",
    "/api/admin/users/quiet%20author/credits/transactions?limit=100",
  ]);
});
